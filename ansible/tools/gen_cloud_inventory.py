#!/usr/bin/env python3
"""从 Terraform 的 state 生成云服务器清单（inventory/hosts.cloud.yml）。

为什么需要它：
    云服务器的 IP 是 Terraform 建出来才知道的，destroy 之后就没了。
    手写进 inventory 必然很快过期，而"清单里留着已经释放的 IP"
    会让 ansible 卡在连接超时上，排查起来很浪费时间。

它做什么：
    1. 拿到 terraform output -json 的结果（两种取法，见下）
    2. 读 summary 输出（每台实例的 name / private_ip / public_ip / instance_type）
    3. 生成 cloud 组的清单文件
    4. 顺手清掉这些 IP 在控制端 known_hosts 里的旧主机键
       （IP 会被回收复用，不清就卡在第一幕 Gathering Facts，见 purge_known_hosts）

只读：不碰任何云资源，也不改 Terraform 的 state。
（第 4 步只动控制端自己的 ~/.ssh/known_hosts，且只动本次刚建出来的这些 IP。）

---------------------------------------------------------------------------
两种取数方式：本地手工 vs 流水线

  【本地手工】不传 --from-output-json → 自己调 `terraform output -json`
      这是「人在自己机器上干活」的路径。方便，但要求这台机器上有
      terraform、有 state、也有读 state 的凭证。

  【流水线】传 --from-output-json <文件> → 读一份预先抓好的载荷
      CI 里不能用上一种。原因：Terraform 的 job 和 Ansible 的 job 是
      **两个独立 job**，各跑在一次性的容器里。Ansible 那个 job 里没有 state、
      也没有云凭证 —— 让它去调 terraform，等于要求它重新拿到整套 Terraform 的
      权限，跨层耦合原封不动地回来了。

      正确做法：Terraform 的 job 把 `terraform output -json` 导成 artifact，
      Ansible 的 job 消费这个 artifact。衔接变成单向、一次性，
      而且**数据流是看得见的**（artifact 里躺着一个文件，谁写的、谁读的，
      在流水线界面上清清楚楚）。

用法：
    # 本地
    python3 tools/gen_cloud_inventory.py
    python3 tools/gen_cloud_inventory.py --ssh-key ~/.ssh/id_ed25519 --user root
    python3 tools/gen_cloud_inventory.py --allow-private   # 内网机器（走跳板机时）

    # 流水线（消费 artifact，全程不碰 terraform）
    python3 tools/gen_cloud_inventory.py --from-output-json tf-outputs.json
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import pathlib
import re
import subprocess
import sys

# 只做"能被安全地写成 YAML 键"的校验；不满足就加引号
SAFE_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")


def die(message: str) -> None:
    print(f"  ✗ {message}", file=sys.stderr)
    raise SystemExit(1)


def terraform_outputs(module_dir: pathlib.Path, terraform: str) -> dict:
    """跑 terraform output -json 并解析。"""
    if not module_dir.is_dir():
        die(f"找不到 Terraform 模块目录：{module_dir}")

    try:
        proc = subprocess.run(
            [terraform, "output", "-json"],
            cwd=str(module_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        die(
            f"找不到 {terraform} 命令。\n"
            "      Ansible 的控制端一般是 Linux/WSL，装法见 LOGIC.md 的「环境要求」；\n"
            "      也可以用 --terraform 指定它的绝对路径。"
        )
    except subprocess.TimeoutExpired:
        die("terraform output 超时（120s）—— 检查一下是不是在等锁或等网络")

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "No outputs found" in stderr or "state" in stderr.lower():
            die(
                "读不到 Terraform 的输出。多半是还没 apply：\n"
                f"      cd {module_dir} && terraform apply\n"
                f"      （原始错误：{stderr.splitlines()[0] if stderr else '未知'}）"
            )
        die(f"terraform output 失败：\n{stderr}")

    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        die(f"terraform output 返回的不是合法 JSON：{exc}")


def terraform_outputs_from_file(path: pathlib.Path) -> dict:
    """读一份预先抓好的 `terraform output -json` 载荷（流水线 artifact）。

    与 terraform_outputs() 的区别：这里**不执行任何命令**。
    调用方（流水线里的 Ansible job）没有 terraform、没有 state、也没有云凭证，
    它只是消费上一个 job 递过来的文件。

    这样「跨层衔接」就只剩一次单向的数据传递：
        Terraform job  --(artifact: tf-outputs.json)-->  Ansible job
    """
    if not path.is_file():
        die(
            f"找不到 Terraform 输出的载荷文件：{path}\n"
            "      这个文件应该由流水线里 Terraform 那个 job 产出（terraform output -json > 它），\n"
            "      并以 artifact 的形式传给本 job。本地手工取数时不要用 --from-output-json。"
        )

    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        die(f"载荷文件不是合法 JSON（{path}）：{exc}")

    if not isinstance(payload, dict):
        die(f"载荷文件的顶层必须是对象（terraform output -json 的形状），实得 {type(payload).__name__}")
    return payload


def pick_instances(outputs: dict) -> list[dict]:
    """把输出里的 summary 整理成实例列表。"""
    summary = outputs.get("summary", {})
    value = summary.get("value") if isinstance(summary, dict) else None
    if not isinstance(value, dict):
        # 没有 summary 输出时退化成用 public_ips / instance_private_ips 拼
        pub = (outputs.get("instance_public_ips", {}) or {}).get("value") or {}
        priv = (outputs.get("instance_private_ips", {}) or {}).get("value") or {}
        if not pub and not priv:
            return []
        value = {
            key: {"name": key, "public_ip": pub.get(key, ""), "private_ip": priv.get(key, "")}
            for key in sorted(set(pub) | set(priv))
        }

    instances = []
    for key, item in sorted(value.items()):
        if not isinstance(item, dict):
            continue
        instances.append(
            {
                "key": str(key),
                "name": str(item.get("name") or f"ecs-{key}"),
                "public_ip": (item.get("public_ip") or "").strip(),
                "private_ip": (item.get("private_ip") or "").strip(),
                "instance_type": (item.get("instance_type") or "").strip(),
            }
        )
    return instances


def render(inventory: list[dict], region: str, user: str, ssh_key: str) -> str:
    """把实例列表渲染成清单文件内容。

    值一律用 json.dumps 输出 —— JSON 是 YAML 1.2 的子集，
    所以 json.dumps 的结果必然是合法的 YAML 标量，中文也不用额外转义。
    """
    out: list[str] = []
    out.append("---")
    out.append("# " + "=" * 73)
    out.append("# 云服务器清单 —— 由 tools/gen_cloud_inventory.py 自动生成")
    out.append("#")
    out.append(f"# 地域：{region or '(未知)'}    共 {len(inventory)} 台")
    out.append("#")
    out.append("# ★ 这个文件是生成物，不要手工编辑 —— 改了下一次生成就被覆盖。")
    out.append("#   要改连接参数，请改生成命令的参数或 group_vars/cloud.yml。")
    out.append("#")
    out.append("# ★ cloud 组 = 允许全量配置、可以随时 destroy 重建的机器。")
    out.append("#   手工维护的本地服务器在 inventory/hosts.yml 的 local 组里，")
    out.append("#   那一组默认完全只读（roles/guard 会拦）。")
    out.append("# " + "=" * 73)
    out.append("")
    out.append("all:")
    out.append("  children:")

    if not inventory:
        out.append("    # 没有可用的云服务器（实例清单为空）")
        out.append("    cloud:")
        out.append("      hosts: {}")
        out.append("")
        return "\n".join(out)

    out.append("    cloud:")
    out.append("      hosts:")

    for inst in inventory:
        host_key = inst["name"] if SAFE_KEY.match(inst["name"]) else json.dumps(inst["name"], ensure_ascii=False)
        note_parts = ["阿里云 ECS"]
        if region:
            note_parts.append(region)
        if inst["instance_type"]:
            note_parts.append(inst["instance_type"])
        if inst["public_ip"] != inst["private_ip"] and inst["private_ip"]:
            note_parts.append(f"内网 {inst['private_ip']}")

        out.append(f"        {host_key}:")
        out.append(f"          ansible_host: {json.dumps(inst['public_ip'] or inst['private_ip'], ensure_ascii=False)}")
        out.append(f"          ansible_user: {json.dumps(user, ensure_ascii=False)}")
        if ssh_key:
            out.append(f"          ansible_ssh_private_key_file: {json.dumps(ssh_key, ensure_ascii=False)}")
        out.append(f"          wf_note: {json.dumps(' · '.join(note_parts), ensure_ascii=False)}")

    out.append("")
    return "\n".join(out)


def _host_key_matches(entry: str, host: str) -> bool:
    """known_hosts 里的一行是不是属于 host。

    三种写法都要认，少认一种就等于"清理了但没清掉"：

        plain       47.98.12.34 ssh-ed25519 AAAA...
        bracketed   [47.98.12.34]:2222 ssh-ed25519 AAAA...
        hashed      |1|<salt_b64>|<hmac_b64> ssh-ed25519 AAAA...

    最后一种是 HashKnownHosts 的默认产物（Ubuntu/Debian 默认开），
    肉眼完全看不出里面是哪个 IP，只能按同样算法算一遍再比对：
        digest = base64( HMAC-SHA1(salt, hostname) )
    """
    try:
        names = entry.split()[0].split(",")
    except IndexError:
        return False

    for name in names:
        if name.startswith("|1|"):
            try:
                _, _, salt_b64, digest_b64 = name.split("|")
                salt = base64.b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
                want = hmac.new(salt, host.encode(), hashlib.sha1).digest()
                got = base64.b64decode(digest_b64 + "=" * (-len(digest_b64) % 4))
            except (ValueError, binascii.Error):
                continue
            if hmac.compare_digest(want, got):
                return True
        elif name.startswith("["):
            # [host]:port —— 带端口时方括号是必写的，去掉才是地址本身
            if name.split("]")[0].lstrip("[") == host:
                return True
        elif name == host:
            return True
    return False


def purge_known_hosts(hosts: list[str], path: pathlib.Path) -> list[str]:
    """清掉这些地址在 known_hosts 里的旧主机键，返回清掉的条目头。

    ── 为什么必须清 ──
    公网 IP 会被回收再分配。destroy 之后重建，新机器大概率拿到上一台
    用过的 IP，而控制端的 known_hosts 里还留着**上一台机器**的键，于是：
        @@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@
        Offending ECDSA key in /root/.ssh/known_hosts:18
    Ansible 判 UNREACHABLE，卡在第一幕的 Gathering Facts —— 报错看着像
    "连不上"或"被中间人攻击"，实际只是记账本过期了。
    而蓝绿灰度天生每发布一次就 destroy 一台、建一台，所以这不是偶发，
    是每次都会撞上的常态。

    ── 为什么可以安全地清 ──
    传进来的 IP 是**本次刚 terraform apply 出来的**，它们对应的上一台机器
    已经被销毁、私钥也随之消失，旧记录没有任何"认机器"的价值。
    删掉之后由 accept-new 重新记一份新键，信任起点跟着挪到新机器上。
    清单之外的主机（本地存量服务器等）一律不动 —— 不搞"关掉校验"那套。
    """
    path = path.expanduser()
    target = {h.strip() for h in hosts if h and h.strip()}
    if not target or not path.is_file():
        return []

    try:
        original = path.read_text(encoding="utf-8", errors="replace").splitlines()
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        print(f"  ⚠ 读不了 {path}（{exc}），跳过主机键清理", file=sys.stderr)
        return []

    kept: list[str] = []
    removed: list[str] = []
    for line in original:
        head = line.split()[0] if line.strip() else ""
        if head and not line.lstrip().startswith("#") and any(_host_key_matches(line, h) for h in target):
            removed.append(head)
            continue
        kept.append(line)

    if not removed:
        return []

    # 先写临时文件再原子替换：中途失败不会把 known_hosts 留成半截
    tmp = path.with_name(path.name + ".wf-tmp")
    tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    try:
        tmp.chmod(mode)
    except OSError:
        pass
    tmp.replace(path)
    return removed


def main(argv: list[str] | None = None) -> int:
    here = pathlib.Path(__file__).resolve().parent
    default_module = (here.parent.parent / "aliyun-ecs").resolve()
    default_output = (here.parent / "inventory" / "hosts.cloud.yml").resolve()

    parser = argparse.ArgumentParser(
        description="从 Terraform state 生成云服务器清单（inventory/hosts.cloud.yml）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--module-dir", default=str(default_module), help="aliyun-ecs 模块目录")
    parser.add_argument("--output", default=str(default_output), help="生成的清单文件路径")
    parser.add_argument("--terraform", default="terraform", help="terraform 可执行文件（默认从 PATH 找）")
    parser.add_argument(
        "--from-output-json",
        default=None,
        metavar="FILE",
        help="不调用 terraform，改从这份预先抓好的 `terraform output -json` 载荷取数（流水线用）",
    )
    parser.add_argument("--user", default="root", help="登录用户（阿里云 ECS 默认 root）")
    parser.add_argument("--ssh-key", default="~/.ssh/id_ed25519", help="SSH 私钥路径")
    parser.add_argument(
        "--allow-private",
        action="store_true",
        help="允许把没有公网 IP 的实例也写进去（需要跳板机才能连；默认跳过并提示）",
    )
    parser.add_argument("--quiet", action="store_true", help="只输出结果，不打过程")
    parser.add_argument(
        "--known-hosts",
        default="~/.ssh/known_hosts",
        help="控制端的 known_hosts 路径（默认 ~/.ssh/known_hosts）",
    )
    parser.add_argument(
        "--no-purge-known-hosts",
        action="store_true",
        help="不清理这些 IP 的旧主机键（默认清理，见 purge_known_hosts 的说明）",
    )
    args = parser.parse_args(argv)

    module_dir = pathlib.Path(args.module_dir).resolve()
    out_path = pathlib.Path(args.output).resolve()

    if args.from_output_json:
        payload_path = pathlib.Path(args.from_output_json).resolve()
        if not args.quiet:
            print(f"  → 读取流水线递过来的 Terraform 输出：{payload_path}")
            print("      （本次不执行 terraform —— 衔接只发生在 artifact 这一层）")
        outputs = terraform_outputs_from_file(payload_path)
    else:
        if not args.quiet:
            print(f"  → 读取 Terraform 输出：{module_dir}")
        outputs = terraform_outputs(module_dir, args.terraform)
    instances = pick_instances(outputs)

    region = (outputs.get("region", {}) or {}).get("value") or ""

    usable: list[dict] = []
    skipped: list[dict] = []
    for inst in instances:
        if inst["public_ip"]:
            usable.append(inst)
        elif args.allow_private:
            usable.append(inst)
        else:
            skipped.append(inst)

    if skipped:
        print(
            f"  ⚠ 有 {len(skipped)} 台实例没有公网 IP，已跳过："
            f" {', '.join(i['name'] for i in skipped)}",
            file=sys.stderr,
        )
        print(
            "      原因：控制端连不上内网地址（10.x/172.16-31.x/192.168.x）。\n"
            "      要么给实例分配公网带宽/EIP，要么直接用 --allow-private（配合跳板机）。",
            file=sys.stderr,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(usable, region, args.user, args.ssh_key), encoding="utf-8")

    # 清单出来的同一刻就把这些 IP 的旧主机键清掉。
    # 顺序有意如此：先确认这批 IP 是"本次的"，再动 known_hosts。
    if not args.no_purge_known_hosts and usable:
        known_hosts = pathlib.Path(args.known_hosts)
        gone = purge_known_hosts(
            [i["public_ip"] or i["private_ip"] for i in usable], known_hosts
        )
        if gone:
            print(
                f"  ✓ 清掉 {len(gone)} 条过期主机键（{known_hosts.expanduser()}）：{', '.join(gone)}",
                file=sys.stderr,
            )
            print(
                "      原因：公网 IP 被回收复用，旧机器的键会让 ssh 报 "
                "HOST IDENTIFICATION CHANGED",
                file=sys.stderr,
            )

    if not args.quiet:
        print(f"  ✓ 已写入 {out_path}（{len(usable)} 台）")
        if usable:
            for inst in usable:
                print(f"      - {inst['name']:<18} {inst['public_ip'] or inst['private_ip']}")
        else:
            print("      （清单里暂时没有云服务器 —— 先 terraform apply，或这次已经 destroy 了）")
        print()
        print("  下一步：")
        print("      ansible-playbook playbooks/verify.yml        # 先只读看一眼")
        print("      ansible-playbook playbooks/site.yml          # 云上做基线，本地服务器仍只读")
    return 0


if __name__ == "__main__":
    sys.exit(main())
