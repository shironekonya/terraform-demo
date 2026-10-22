#!/usr/bin/env python3
"""不装 Ansible 也能跑的验证集合。

为什么要有它：
    这套 ansible 目录的产物是「渲染出来的文件」—— 报告、daemon.json、
    compose 文件、sysctl 配置。这些东西的正确性只有在渲染之后才谈得上。
    而完整的验证方式是"连上机器跑一遍 playbook"，门槛很高。

    这个脚本把**能离线验证的部分**全部拿下来：
      · 所有模板都能渲染，且渲染结果是合法的 JSON / YAML / HTML
      · guard 的路径过滤器行为正确（含容易搞错的"字符串前缀 vs 路径段"）
      · 云清单生成器在真实/退化两种输入下都能产出合法 YAML
      · 所有 YAML 文件都能解析

    它跑不过的东西（如实说明）：
      · 模块本身在远端的行为（package/systemd/template 的实际效果）
      · Ansible 的变量优先级、role 依赖求解
      · SSH 连通性、sudo 是否需要密码
    这些只能在真正运行 playbook 时才知道。

用法：
    python3 tools/selftest.py
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        PASSED.append(label)
        print(f"  ✓ {label}")
    else:
        FAILED.append(label)
        print(f"  ✗ {label}")
        if detail:
            for line in str(detail).splitlines()[:12]:
                print(f"      {line}")
    return condition


def load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def group_vars_dir() -> pathlib.Path:
    """group_vars 的真实位置。

    故意复用 check_guard.py 里的解析 —— 那个函数的规则是照着 Ansible 的
    查找规则写的（清单旁边 / playbook 旁边）。这里再写一份的话，
    两边一旦不一致，就会出现「校验通过、Ansible 看不到变量」这种最坏的组合。
    """
    checker = load_module(ROOT / "tools" / "check_guard.py", "check_guard_for_test")
    return checker.resolve_group_vars_dir(ROOT)


# ---------------------------------------------------------------------------
# Jinja 环境：尽量贴近 Ansible 的模板环境
# ---------------------------------------------------------------------------

def make_env(strict: bool = False):
    import jinja2

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(ROOT)),
        # Ansible 的 undefined 是"可链式"的，所以 {{ a.b | default('x') }} 能用。
        # 用默认的 Undefined 会在 a 不存在时直接抛错，和 Ansible 行为不一致。
        #
        # strict=True 走另一个方向：任何没被显式提供的变量都直接报错。
        # 验证保护公式时用这个 —— 那里"变量名写错"会静默变成 False，
        # 而 False 恰好意味着"不保护"，是最危险的失败方向。
        undefined=jinja2.StrictUndefined if strict else jinja2.ChainableUndefined,
        keep_trailing_newline=True,
        autoescape=False,
        # ★ trim_blocks 必须开，因为 Ansible 就是开着的。
        #   这一条不是"顺手加上"，而是被真跑打出来的：
        #   Ansible 的模板环境 trim_blocks=True，于是 {% endfor %} 这类
        #   控制标签**自己占的那一行换行会被吃掉**；模板作者因此不需要在
        #   结尾标签上写减号。如果离线环境不开这个开关，就会得到和真跑
        #   不一样的空白，于是「离线通过、真跑一行挤在一起」这种 bug
        #   在离线测试里永远看不见。
        #   （实测方式：渲染一段只有控制标签的模板，看有没有空行。）
        trim_blocks=True,
    )

    def now(fmt: str | None = None, utc: bool = False):
        import datetime

        moment = datetime.datetime.now(datetime.timezone.utc) if utc else datetime.datetime.now()
        return moment.strftime(fmt) if fmt else moment.isoformat()

    env.globals["now"] = now

    # Ansible 提供的、模板里用到的过滤器。
    # 这份清单必须和「角色/模板里实际用到的」对上：漏一个，模板渲染就会以
    # "No filter named 'xxx'" 的形式失败，而这个失败在真正的 Ansible 里不存在。
    # 反过来说，只有这里全部通过，才能确认模板本身的 Jinja 语法是好的。
    env.filters["to_json"] = lambda value, **kw: json.dumps(value, ensure_ascii=False, sort_keys=True)
    # 对齐 Ansible 的 to_nice_json：indent=4 + sort_keys
    env.filters["to_nice_json"] = lambda value, **kw: json.dumps(
        value, ensure_ascii=False, indent=4, sort_keys=True
    )
    env.filters["from_json"] = json.loads
    env.filters["from_yaml"] = lambda s: yaml.safe_load(s)
    env.filters["to_yaml"] = lambda value, **kw: yaml.safe_dump(value, allow_unicode=True)
    env.filters["ternary"] = lambda cond, a, b: a if cond else b

    # bool 是 Ansible 自己的过滤器，Jinja2 没有。
    # 语义要和 Ansible 一致：字符串 'false'/'no'/'off'/'0'/'' 都是假，
    # 其余非空字符串都是真。写错的后果很直接 —— 保护开关会反向生效。
    def to_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() not in ("", "false", "no", "off", "0", "none", "null")

    env.filters["bool"] = to_bool

    plugin_path = ROOT / "roles" / "guard" / "filter_plugins" / "wf_guard.py"
    if plugin_path.is_file():
        plugin = load_module(plugin_path, "wf_guard_for_test")
        env.filters["wf_under_any"] = plugin.wf_under_any
        env.filters["wf_overlap"] = plugin.wf_overlap
    return env


# ---------------------------------------------------------------------------
# 模拟上下文
# ---------------------------------------------------------------------------

def facts_local() -> dict:
    return {
        "inventory_hostname": "k3s-server",
        "ansible_host": "192.168.193.147",
        "wf_note": "K3s 控制平面 + Jenkins(compose) + Harbor(compose)",
        "wf_in_local_group": True,
        "wf_protected": True,
        "wf_can_write": False,
        "wf_intent": "read",
        "ansible_hostname": "k3s-server",
        "ansible_distribution": "Ubuntu",
        "ansible_distribution_version": "22.04",
        "ansible_kernel": "5.15.0-119-generic",
        "ansible_architecture": "x86_64",
        "ansible_processor_vcpus": 4,
        "ansible_memtotal_mb": 8192,
        "ansible_uptime_seconds": 1234567,
        "ansible_mounts": [
            {"mount": "/", "size_total": 100 * 1024**3, "size_available": 40 * 1024**3},
            {"mount": "/data", "size_total": 200 * 1024**3, "size_available": 5 * 1024**3},
            {"mount": "/dev/shm", "size_total": 0, "size_available": 0},
        ],
        "wf_audit_tools": {
            "stdout_lines": [
                "python3|Python 3.10.12",
                "docker|Docker version 27.3.1, build ce12230",
                "git|git version 2.34.1",
                "curl|curl 7.81.0",
                "jq|jq-1.6",
                "compose|v2.29.7 (v2 插件)",
            ]
        },
        "wf_audit_listen": {"stdout_lines": ["22", "80", "443", "8081", "50000", "6443"]},
        "wf_audit_services_raw": {
            "stdout_lines": [
                "sshd|active|enabled",
                "docker|active|enabled",
                "k3s|active|enabled",
                "nginx|inactive|disabled",
            ]
        },
        # 真实的 command/shell 注册结果里 stdout 和 stdout_lines 是同时存在的
        # （模板里也确实是"用 stdout 判哨兵、用 stdout_lines 排版"）。
        # 夹具必须照搬这一点，否则测出来的失败是夹具失真，不是模板有问题。
        "wf_audit_containers": {
            "stdout": (
                "CONTAINER ID   IMAGE                          STATUS          NAMES\n"
                "a1b2c3d4e5f6   jenkins/jenkins:2.555.3-lts    Up 12 days      jenkins"
            ),
            "stdout_lines": [
                "CONTAINER ID   IMAGE                          STATUS          NAMES",
                "a1b2c3d4e5f6   jenkins/jenkins:2.555.3-lts    Up 12 days      jenkins",
            ],
        },
        "wf_audit_k3s": {
            "stdout_lines": [
                "k3s version v1.31.3+k3s1 (c4e1c1c1)",
                "---",
                "k3s-server   Ready   control-plane,master   24d   v1.31.3+k3s1",
                "k3s-agent    Ready   <none>                 24d   v1.31.3+k3s1",
            ]
        },
        "wf_audit_ports": [
            {"port": 22, "what": "SSH"},
            {"port": 8081, "what": "★ Jenkins Web"},
            {"port": 6443, "what": "K3s API server"},
            {"port": 9999, "what": "这一项故意不存在，用来验证「未在听」的显示"},
        ],
    }


def facts_cloud() -> dict:
    base = facts_local()
    base.update(
        {
            "inventory_hostname": "demo-ecs-1",
            "ansible_host": "47.98.12.34",
            "wf_note": "阿里云 ECS · cn-hangzhou · ecs.e-c1m1.large",
            "wf_in_local_group": False,
            "wf_protected": False,
            "wf_can_write": True,
            "ansible_distribution": "Alibaba Cloud Linux",
            "ansible_distribution_version": "3.2104",
            "ansible_mounts": [
                {"mount": "/", "size_total": 40 * 1024**3, "size_available": 33 * 1024**3},
            ],
            "wf_audit_containers": {"stdout": "__NO_DOCKER__", "stdout_lines": ["__NO_DOCKER__"]},
            "wf_audit_k3s": {"stdout_lines": ["__NO_K3S__"]},
            "wf_audit_services_raw": {"stdout_lines": ["sshd|active|enabled"]},
        }
    )
    return base


# ---------------------------------------------------------------------------
# 各项测试
# ---------------------------------------------------------------------------

def test_yaml_parsing() -> None:
    print("\n[1] 所有 YAML 文件都能解析")
    files = sorted(
        set(
            list(ROOT.glob("playbooks/*.yml"))
            + list(group_vars_dir().glob("*.yml"))
            + list(ROOT.glob("inventory/*.yml"))
            + list(ROOT.glob("roles/*/tasks/*.yml"))
            + list(ROOT.glob("roles/*/defaults/*.yml"))
            + list(ROOT.glob("roles/*/handlers/*.yml"))
            + list(ROOT.glob("roles/*/meta/*.yml"))
        )
    )
    bad = []
    for path in files:
        try:
            with path.open("r", encoding="utf-8") as fh:
                yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            bad.append(f"{path.relative_to(ROOT)}: {exc}")
    check(f"{len(files)} 个 YAML 文件全部解析通过", not bad, "\n".join(bad))


def test_filters() -> None:
    print("\n[2] 路径过滤器（本地禁区判定）")
    plugin_path = ROOT / "roles" / "guard" / "filter_plugins" / "wf_guard.py"
    if not plugin_path.is_file():
        check("过滤器插件存在", False)
        return
    wf = load_module(plugin_path, "wf_guard_ut")

    cases = [
        ("路径等于禁区 → 命中", ["/data/jenkins-data"], ["/data/jenkins-data"], True),
        ("路径在禁区之下 → 命中", ["/data/jenkins-data/jobs/x.xml"], ["/data/jenkins-data"], True),
        (
            "★ 仅字符串前缀（jenkins-data-OLD）→ 不命中",
            ["/data/jenkins-data-OLD"],
            ["/data/jenkins-data"],
            False,
        ),
        ("结尾多余斜杠 → 归一化后命中", ["/data/jenkins-data/"], ["/data/jenkins-data/"], True),
        ("无关路径 → 不命中", ["/opt/wf", "/etc/sysctl.d/99-wf.conf"], ["/data/jenkins-data"], False),
        ("多个禁区命中其中一个", ["/var/opt/gitlab/git-data"], ["/data/x", "/var/opt/gitlab"], True),
        ("空输入 → 不命中", [], ["/data/jenkins-data"], False),
        ("非绝对路径 → 忽略", ["relative/path"], ["/data/jenkins-data"], False),
        ("字符串输入（单个值）也能处理", "/data/jenkins-data/x", "/data/jenkins-data", True),
    ]
    for label, paths, prefixes, expect in cases:
        got = wf.wf_overlap(paths, prefixes)
        check(f"{label}", got == expect, f"期望 {expect}，实得 {got}")

    # 真实配置：baseline 的写入目标不该落在本地禁区里
    defaults = yaml.safe_load((ROOT / "roles" / "baseline" / "defaults" / "main.yml").read_text(encoding="utf-8"))
    local_gv = yaml.safe_load((group_vars_dir() / "local.yml").read_text(encoding="utf-8"))
    targets = list(defaults.get("wf_base_dirs") or []) + [
        defaults.get("wf_sysctl_file"),
        defaults.get("wf_limits_file"),
        defaults.get("wf_timezone_file"),
    ]
    targets = [t for t in targets if t]
    hits = wf.wf_under_any(targets, local_gv.get("wf_local_forbidden_paths") or [])
    check(
        f"真实配置复核：baseline 的 {len(targets)} 个写入目标都不在本地禁区里",
        not hits,
        f"命中：{hits}",
    )


def test_audit_report() -> None:
    print("\n[3] 体检报告模板（roles/audit/templates/report.txt.j2）")
    env = make_env()

    for label, facts, expect_protected_word in (
        ("本地服务器 → 报告应写明受保护", facts_local(), "Ansible 不会修改这台机器"),
        ("云服务器 → 报告应写明允许变更", facts_cloud(), "允许变更"),
    ):
        out = env.get_template("roles/audit/templates/report.txt.j2").render(**facts)
        check(f"{label}（渲染成功且非空）", bool(out.strip()) and len(out) > 500, f"长度 {len(out)}")
        check(f"{label}（含保护状态那一行）", expect_protected_word in out, out[:600])
        check(f"{label}（含主机名）", facts["inventory_hostname"] in out)

    # 检查具体内容
    out = env.get_template("roles/audit/templates/report.txt.j2").render(**facts_local())
    check("端口 8081 显示为「在听」", "8081" in out and "✅ 在听" in out, out)
    check("不存在的端口 9999 显示为「—」", "9999" in out, out)

    # -----------------------------------------------------------------------
    # ★ 按行断言：每个循环项必须独占一行。
    #
    #   上面那些断言全是"某个字符串在不在这段文本里"，而换行丢了的时候
    #   字符串仍然在 —— 所以它们集体看不见这类 bug。
    #   真跑时报告里磁盘、工具、端口、服务四段全挤成一行，就是被这一组
    #   断言该抓到、但当时还没写的。
    # -----------------------------------------------------------------------
    lines = out.splitlines()
    facts = facts_local()

    def lines_starting_with(prefix: str) -> list[str]:
        return [ln for ln in lines if ln.strip().startswith(prefix)]

    disk_rows = [ln for ln in lines if "已用" in ln and "/" in ln]
    check(
        f"磁盘段：{len(disk_rows)} 个挂载点各占一行（0 字节的 /dev/shm 已跳过）",
        len(disk_rows) == 2,
        "\n".join(disk_rows) or "（一行都没有）",
    )
    tool_rows = [ln for ln in lines if "Python 3.10.12" in ln or "Docker version" in ln]
    check(
        f"工具段：{len(tool_rows)} 个工具各占一行",
        len(tool_rows) == 2,
        "\n".join(tool_rows) or "（一行都没有）",
    )
    check(
        "工具段没有把多个工具挤在同一行",
        not any(ln.count("Python 3.10.12") and ln.count("Docker version") for ln in lines),
        "\n".join(tool_rows),
    )
    import re as _re

    # 端口行的形状：缩进 + 端口号 + （在听 / —）+ 说明
    port_row = _re.compile(r"^\s+\d+\s+(✅ 在听|—)\s+\S")
    port_rows = [ln for ln in lines if port_row.match(ln)]
    check(
        f"端口段：{len(port_rows)} 个端口各占一行",
        len(port_rows) == len(facts["wf_audit_ports"]),
        "\n".join(port_rows) or "（一行都没有）",
    )
    svc_rows = [ln for ln in lines if "active" in ln and "alias" not in ln[:4]]
    check(
        f"服务段：{len(svc_rows)} 个服务各占一行",
        len(svc_rows) == len(facts["wf_audit_services_raw"]["stdout_lines"]),
        "\n".join(svc_rows) or "（一行都没有）",
    )

    sep_count = sum(1 for ln in lines if ln.startswith("═"))
    check(f"分隔线独占整行（找到 {sep_count} 条）", sep_count >= 3, out[:400])
    check(
        "分隔线没有被内容粘住",
        all(ln.strip("═") == "" for ln in lines if ln.startswith("═")),
        "\n".join(ln for ln in lines if ln.startswith("═")),
    )
    check("报告末尾只有一个空行收尾", out.endswith("\n") and not out.endswith("\n\n\n"), repr(out[-30:]))
    check("磁盘段落列出了 /data 挂载点", "/data" in out)
    check("0 字节的挂载点（/dev/shm）被跳过", "/dev/shm" not in out)
    check("工具版本段落含 docker", "docker" in out and "27.3.1" in out)
    check("容器段落显示了 jenkins 容器", "jenkins/jenkins:2.555.3-lts" in out)
    check("K3s 段落显示了节点列表", "k3s-agent" in out, out)
    check("报告结尾声明「未修改任何东西」", "本次未修改" in out)

    # 缺失工具的显示
    facts = facts_local()
    facts["wf_audit_tools"] = {"stdout_lines": ["python3|MISSING", "docker|MISSING", "compose|MISSING"]}
    out = env.get_template("roles/audit/templates/report.txt.j2").render(**facts)
    check("缺失的工具显示为「⛔ 未安装」", out.count("⛔ 未安装") == 3, out)

    # 没有 docker / 没有 systemd 的降级分支
    facts = facts_local()
    facts["wf_audit_containers"] = {"stdout": "__NO_DOCKER_PERM__", "stdout_lines": ["__NO_DOCKER_PERM__"]}
    facts["wf_audit_listen"] = {"stdout_lines": ["__NO_SS__"]}
    facts["wf_audit_services_raw"] = {"stdout_lines": ["__NO_SYSTEMD__"]}
    out = env.get_template("roles/audit/templates/report.txt.j2").render(**facts)
    check("降级分支：docker 无权限有提示", "读不到" in out)
    check("降级分支：无 ss/netstat 有提示", "无法探测" in out)
    check("降级分支：无 systemd 有提示", "没有 systemd" in out)
    check("降级分支：没有崩溃", bool(out.strip()))


def test_daemon_json() -> None:
    print("\n[4] daemon.json 模板（必须是合法 JSON）")
    env = make_env()
    tmpl = env.get_template("roles/docker_engine/templates/daemon.json.j2")

    cases = [
        ("不带镜像加速", {"wf_docker_log_max_size": "10m", "wf_docker_log_max_file": "3",
                          "wf_docker_registry_mirrors": []}),
        ("带一个镜像加速", {"wf_docker_log_max_size": "50m", "wf_docker_log_max_file": "5",
                            "wf_docker_registry_mirrors": ["https://x.mirror.aliyuncs.com"]}),
        ("带多个镜像加速", {"wf_docker_log_max_size": "10m", "wf_docker_log_max_file": "3",
                            "wf_docker_registry_mirrors": ["https://a.example.com", "https://b.example.com"]}),
    ]
    for label, ctx in cases:
        out = tmpl.render(**ctx)
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            check(f"{label} → 合法 JSON", False, f"{exc}\n渲染结果：\n{out}")
            continue
        check(f"{label} → 合法 JSON", True)
        check(f"{label} → 日志轮转已配置",
              data.get("log-driver") == "json-file"
              and data.get("log-opts", {}).get("max-size") == ctx["wf_docker_log_max_size"]
              and data.get("log-opts", {}).get("max-file") == ctx["wf_docker_log_max_file"],
              out)
        if ctx["wf_docker_registry_mirrors"]:
            check(f"{label} → registry-mirrors 正确", data.get("registry-mirrors") == ctx["wf_docker_registry_mirrors"], out)
        else:
            check(f"{label} → 不残留空的 registry-mirrors 键", "registry-mirrors" not in data, out)


def test_stack_templates() -> None:
    print("\n[5] 容器栈模板（compose + 首页）")
    env = make_env()
    ctx = {
        "inventory_hostname": "demo-ecs-1",
        "ansible_host": "47.98.12.34",
        "wf_stack_name": "wf-web",
        "wf_stack_image": "nginx:alpine",
        "wf_stack_port": 8080,
        "wf_stack_dir": "/opt/wf/stacks/wf-web",
        "wf_stack_release": "e5e2215",
        "wf_stack_release_root": "/opt/wf/stacks/wf-web/releases",
        "wf_stack_public_dir": "/opt/wf/stacks/wf-web/public",
        "ansible_distribution": "Alibaba Cloud Linux",
        "ansible_distribution_version": "3.2104",
        "ansible_kernel": "5.10.134-16.al8.x86_64",
        "ansible_processor_vcpus": 2,
        "ansible_memtotal_mb": 4096,
    }

    compose_out = env.get_template("roles/app_stack/templates/docker-compose.yml.j2").render(**ctx)
    try:
        data = yaml.safe_load(compose_out)
        check("compose 渲染结果 → 合法 YAML", isinstance(data, dict), compose_out)
        services = (data or {}).get("services") or {}
        check("compose 里有 services", bool(services), compose_out)
        check("服务名用了 wf_stack_name", "wf-web" in services, list(services))
        svc = services.get("wf-web", {})
        check("镜像正确", svc.get("image") == "nginx:alpine", svc)
        check("端口只绑回环，不对外（127.0.0.1:8080:80）",
              svc.get("ports") == ["127.0.0.1:8080:80"], svc.get("ports"))
        # ★ 挂的必须是**目录**：单文件 bind mount 在容器创建时就把 inode 钉死了，
        #   之后换内容容器看不到 —— 发布/回滚都会静默失效。这条断言就是钉它的。
        volumes = [str(v) for v in svc.get("volumes") or []]
        check("首页以只读挂载，且挂的是目录不是单个文件",
              any(v.endswith("public:/usr/share/nginx/html:ro") for v in volumes)
              and not any("index.html" in v for v in volumes), volumes)
        check("带健康检查", "healthcheck" in svc, svc)
        check("刻意不写 version 字段（compose v2 已废弃）", "version" not in (data or {}), list(data or {}))
    except yaml.YAMLError as exc:
        check("compose 渲染结果 → 合法 YAML", False, f"{exc}\n{compose_out}")

    html_out = env.get_template("roles/app_stack/templates/index.html.j2").render(**ctx)
    check("首页含主机名", "demo-ecs-1" in html_out)
    check("首页含地址", "47.98.12.34" in html_out)
    check("首页是完整 HTML", html_out.lstrip().startswith("<!DOCTYPE html>") and "</html>" in html_out)
    check("首页会显示容器名", "wf-web" in html_out)
    # 版本标记是"发布/回滚有没有真的生效"的唯一可读证据：
    # 正文里那行给人看，meta 那行给断言用（整串不会和其它内容撞上）。
    check("首页带版本标记（meta wf-release）",
          '<meta name="wf-release" content="e5e2215">' in html_out, html_out[:300])
    check("首页正文里也显示版本", "<dt>版本</dt><dd>e5e2215</dd>" in html_out)

    # 关键：不同主机的渲染结果必须不同（否则"批量下发同一份模板"就没意义）
    ctx2 = dict(ctx, inventory_hostname="demo-ecs-2", ansible_host="47.98.56.78")
    html_out2 = env.get_template("roles/app_stack/templates/index.html.j2").render(**ctx2)
    check("两台机器渲染出的首页不同（体现各自身份）", html_out != html_out2)


def test_release_rollback() -> None:
    """发布与回滚：版本必须落在**产物**上，不能落在"再跑一遍"上。

    三个最要命的退化，都发生在没人写下来的地方：

      ① 回滚 = 重跑一遍发布。用当前模板重新渲染一遍，得到的是
         "旧版本号 + 新内容" —— 名字对了，内容不对，比不回滚更难查。
      ② 容器挂单个文件。bind mount 在创建容器时把 inode 钉死，
         之后换宿主机的文件容器读不到 —— 回滚"成功"，页面没变，也没有报错。
      ③ 没有当前版本指针。"现在跑的是哪一版"只能靠猜，回滚就无从谈起。

    这三条都能在结构上钉死，不需要连机器。运行时行为仍然要真跑才知道。
    """
    print("\n[5c] 发布与回滚（版本落在产物上，不是落在再跑一遍上）")

    def load(rel: str):
        return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))

    def flatten(items) -> list[dict]:
        out: list[dict] = []
        for task in items or []:
            if not isinstance(task, dict):
                continue
            out.append(task)
            for key in ("block", "rescue", "always"):
                out.extend(flatten(task.get(key)))
        return out

    def named(flat: list[dict], key: str) -> list[dict]:
        return [t for t in flat if key in str(t.get("name", ""))]

    def args_of(task: dict, module: str) -> dict:
        value = task.get(module) or {}
        return value if isinstance(value, dict) else {"cmd": value}

    deploy = flatten(load("roles/app_stack/tasks/main.yml"))
    rollback = flatten(load("roles/app_stack/tasks/rollback.yml"))

    # ---- 发布侧 -----------------------------------------------------------
    artifact = named(deploy, "渲染本版首页")
    artifact_dest = str(args_of(artifact[0], "ansible.builtin.template").get("dest", "")) if artifact else ""
    check("发布会把渲染结果落到 releases/<版本>/（不可变产物）",
          bool(artifact) and "wf_stack_release_root" in artifact_dest
          and "wf_stack_release }}" in artifact_dest, artifact_dest)

    publish = named(deploy, "发布到线上目录")
    publish_args = args_of(publish[0], "ansible.builtin.copy") if publish else {}
    check("线上目录是产物的拷贝（remote_src，不从控制端再传一遍）",
          publish_args.get("remote_src") is True, publish_args)
    check("拷贝目标是 public/index.html",
          "wf_stack_public_dir" in str(publish_args.get("dest", ""))
          and str(publish_args.get("dest", "")).endswith("/index.html"), publish_args.get("dest"))

    current = named(deploy, "记录当前版本")
    check("部署成功后会写当前版本指针（回滚靠它找前一版）",
          bool(current) and "wf_stack_release" in str(args_of(current[0], "ansible.builtin.copy").get("content", "")),
          current[:1])

    history = named(deploy, "记入发布历史")
    hist_args = args_of(history[0], "ansible.builtin.lineinfile") if history else {}
    check("历史用 lineinfile 追加（同一版本重复部署不会记两遍）",
          str(hist_args.get("line", "")).strip() == "{{ wf_stack_release }}", hist_args)
    check("记账动作以「镜像已就位」为前提（没起来就不算发布过）",
          bool(current) and "wf_stack_image_ready" in str(current[0].get("when", "")), current[:1])

    verify = named(deploy, "线上页面就是本版")
    check("发布自检读回页面版本标记（200 只证明通，不证明是哪一版）",
          bool(verify) and "wf-release" in str(verify[0].get("ansible.builtin.assert", {}).get("that", "")),
          verify[:1])

    # ---- 回滚侧 -----------------------------------------------------------
    check("回滚读历史时，历史不存在不算「读失败」而是「还没发布过」",
          any("slurp" in t.get("ansible.builtin.slurp", {}) or "ansible.builtin.slurp" in t
              for t in named(rollback, "读发布历史")) and
          any(t.get("failed_when") is False for t in named(rollback, "读发布历史")),
          named(rollback, "读发布历史")[:1])
    check("没有历史时用一句人话拒绝，而不是抛 slurp 原始报错",
          bool(named(rollback, "没有发布历史")) and
          "slurp" not in str(named(rollback, "没有发布历史")[0].get("ansible.builtin.fail", {})),
          named(rollback, "没有发布历史")[:1])
    check("回滚目标必须落在历史里（防止拼错版本名回退到空）",
          "in wf_rb_releases" in str(named(rollback, "断言：目标必须是历史里真实存在的一版")[0]
                                    .get("ansible.builtin.assert", {}).get("that", "")) if
          named(rollback, "断言：目标必须是历史里真实存在的一版") else False)
    check("产物缺失时拒绝回滚（不退化成本地重新渲染）",
          bool(named(rollback, "产物缺失")) and
          "渲染" in str(named(rollback, "产物缺失")[0].get("ansible.builtin.fail", {}).get("msg", "")),
          named(rollback, "产物缺失")[:1])
    check("★ 回滚全程不出现 template 模块（出现就说明又在渲染）",
          not any("ansible.builtin.template" in t for t in rollback),
          [t.get("name") for t in rollback if "ansible.builtin.template" in t])
    check("回滚同时更新内容与指针",
          bool(named(rollback, "把目标产物发布回线上目录")) and bool(named(rollback, "更新当前版本指针")))
    check("回滚自检读的是 80 上的版本标记，不是「文件拷过去了」",
          "wf-release" in str(named(rollback, "页面上的版本标记就是目标版本")[0]
                              .get("ansible.builtin.assert", {}).get("that", ""))
          if named(rollback, "页面上的版本标记就是目标版本") else False)

    # ---- 回滚表达式的真值表 ------------------------------------------------
    # 只验结构不够：挑版本的表达式写错时，结构全对、行为全错。
    expr = named(rollback, "算出要回退到哪一版")[0].get("ansible.builtin.set_fact", {}).get("wf_rb_target")
    import jinja2

    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    cases = [
        ("两版，回退到上一个", ["v1", "v2"], "v2", None, "v1"),
        ("三版，回退到上一个", ["v1", "v2", "v3"], "v3", None, "v2"),
        ("当前是第一版 → 没有上一版", ["v1", "v2"], "v1", None, ""),
        ("只有一版 → 没有上一版", ["v1"], "v1", None, ""),
        ("指针损坏（不在历史里）→ 退化成倒数第二版", ["v1", "v2"], "ghost", None, "v1"),
        ("显式指定优先", ["v1", "v2"], "v2", "v1", "v1"),
    ]
    for label, releases, current_version, explicit, want in cases:
        # 不传 wf_rollback_to 时就是"变量不存在"，而不是传一个 None ——
        # 只测 None 会让 `default()` 的行为看起来和真跑不一样。
        kwargs = {"wf_rb_releases": releases, "wf_rb_current": current_version}
        if explicit is not None:
            kwargs["wf_rollback_to"] = explicit
        try:
            got = str(env.from_string(expr).render(**kwargs)).strip()
        except Exception as exc:  # noqa: BLE001
            got = f"ERROR {exc}"
        check(f"挑版本：{label} → {want or '(空)'}", got == want, got)

    # ---- 入口 playbook -----------------------------------------------------
    play = load("playbooks/rollback.yml")
    first = (play or [{}])[0]
    roles = first.get("roles") or []
    check("rollback.yml：guard 在 roles 第一位", bool(roles) and roles[0] == "guard", roles)
    check("rollback.yml：intent=mutate（回滚也是写操作）",
          (first.get("vars") or {}).get("intent") == "mutate", first.get("vars"))

    # ★ tasks_from 只有一个地方能生效：**roles 之外的** import_role/include_role。
    #   roles: 列表的 dict 写法（- role: app_stack / tasks_from: rollback）
    #   会被 Ansible 静默忽略、照跑 main.yml —— 于是"回滚"变成了"又发布一次"，
    #   而且不报任何错（PITFALLS 第 52 条）。所以这里断言两件事：
    #     ① 入口确实是用 import_role 带的 tasks_from
    #     ② roles 列表里全是字符串，没有混进 dict 写法
    task_block = [t for t in (first.get("tasks") or []) if isinstance(t, dict)]

    def _role_entry(task: dict) -> dict:
        for key in ("ansible.builtin.import_role", "import_role",
                    "ansible.builtin.include_role", "include_role"):
            value = task.get(key)
            if isinstance(value, dict):
                return value
        return {}

    loaded = [(str(e.get("name") or ""), str(e.get("tasks_from") or "main"))
              for e in map(_role_entry, task_block) if e]
    check("rollback.yml：用 import_role 载入 app_stack 的 rollback 任务（路径定义只有一份）",
          ("app_stack", "rollback") in loaded, loaded)
    check("rollback.yml：roles 列表里没有 dict 写法（tasks_from 会被静默忽略）",
          all(isinstance(r, str) for r in roles), roles)

    # ---- CI 入口 -----------------------------------------------------------
    ci = yaml.safe_load((ROOT.parent / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    check("CI 有独立的 rollback 阶段", "rollback" in (ci.get("stages") or []), ci.get("stages"))
    job = ci.get("rollback:app") or {}
    check("rollback:app 是人工放行", any(r.get("when") == "manual" for r in job.get("rules") or []),
          job.get("rules"))
    check("rollback:app 调的是 playbooks/rollback.yml",
          any("playbooks/rollback.yml" in str(line) for line in job.get("script") or []), job.get("script"))
    check("部署 job 会把 commit 当版本名传下去",
          any("wf_release=$CI_COMMIT_SHORT_SHA" in str(line)
              for line in (ci.get("configure:cloud") or {}).get("script") or []),
          (ci.get("configure:cloud") or {}).get("script"))


def test_image_preflight() -> None:
    """镜像就位：拉不到的时候不能"装死"。

    这里校验的是**任务的形状**，不是运行结果（结果只能连上机器才知道）。
    但形状能挡住三类最要命的退化：

      · 又把「拉镜像」塞回 `docker compose up -d` 里 —— 失败时只有 dockerd 原文；
      · `up -d` 不检查镜像是否到位 —— 没镜像时必然失败，而且报错离原因很远；
      · 拉不到就把整幕带走 —— 80 停在 user_data 的欢迎页上，机器看起来"死了"。

    判据都落在可读的结构上（有没有 timeout、when 里有没有镜像门槛、
    失败信息里有没有可执行的下一步），而不是"跑一遍看它成不成"。
    """
    print("\n[5d] 镜像就位（拉不到时给出路，而不是装死）")
    tasks = yaml.safe_load(
        (ROOT / "roles" / "app_stack" / "tasks" / "main.yml").read_text(encoding="utf-8")
    )
    defaults = yaml.safe_load(
        (ROOT / "roles" / "app_stack" / "defaults" / "main.yml").read_text(encoding="utf-8")
    )
    docker_tasks = yaml.safe_load(
        (ROOT / "roles" / "docker_engine" / "tasks" / "main.yml").read_text(encoding="utf-8")
    )

    flat: list[dict] = []

    def walk(items) -> None:
        for task in items or []:
            if not isinstance(task, dict):
                continue
            flat.append(task)
            for key in ("block", "rescue", "always"):
                walk(task.get(key))

    walk(tasks)

    def find(needle: str) -> dict:
        for task in flat:
            if needle in str(task.get("name", "")):
                return task
        return {}

    def when_text(task: dict) -> str:
        value = task.get("when")
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return " ".join(str(v) for v in value)

    def args_of(task: dict) -> dict:
        """任务的模块参数（Ansible 的写法是 `模块名: {参数}`）。"""
        for key, value in task.items():
            if isinstance(key, str) and key.startswith("ansible.builtin."):
                return value if isinstance(value, dict) else {}
        return {}

    def argv_of(task: dict) -> list[str]:
        args = args_of(task)
        if "argv" in args:
            return [str(v) for v in args["argv"]]
        return [str(args.get("cmd", ""))]

    check("defaults 里有离线包开关 wf_stack_image_tar（默认关）",
          defaults.get("wf_stack_image_tar") == "", defaults.get("wf_stack_image_tar"))
    check("拉取有硬超时（wf_stack_pull_timeout 是正整数）",
          isinstance(defaults.get("wf_stack_pull_timeout"), int) and defaults["wf_stack_pull_timeout"] > 0,
          defaults.get("wf_stack_pull_timeout"))
    check("镜像站候选是非空列表（失败时用来测可达性）",
          isinstance(defaults.get("wf_stack_mirror_candidates"), list) and bool(defaults["wf_stack_mirror_candidates"]),
          defaults.get("wf_stack_mirror_candidates"))

    probe = find("探测镜像是否已在本机")
    check("先探测本机有没有这个镜像", bool(probe) and probe.get("changed_when") is False)

    pull = find("从镜像仓库拉取")
    pull_argv = argv_of(pull)
    check("拉镜像是独立任务，不是让 compose 边拉边起",
          bool(pull) and "pull" in pull_argv and "up" not in pull_argv, pull_argv)
    check("拉取外面套了 timeout（给一个硬上限，而不是无限等）",
          "timeout" in pull_argv, pull_argv)
    check("拉取用变量而不是写死镜像名", pull_argv[-1:] == ["{{ wf_stack_image }}"], pull_argv)
    check("拉取失败不在这里 raise（要留给 80 配完再统一收场）",
          pull.get("failed_when") is False, pull.get("failed_when"))

    up = find("拉起容器栈")
    check("up -d 以「镜像已就位」为门槛", "wf_stack_image_ready" in when_text(up), when_text(up))
    check("原本的幂等条件还在（文件变了 / 没在跑才动手）",
          "wf_stack_file.changed" in when_text(up) and "wf_stack_ps" in when_text(up), when_text(up))

    front_check = find("验证 80 确实反代到了容器")
    check("80 的 200 自检只在镜像就位时跑", "wf_stack_image_ready" in when_text(front_check), when_text(front_check))

    pending = find("容器没起来时，80 要么给待部署页")
    pending_args = args_of(pending)
    pending_status = str(pending_args.get("status_code")).replace("'", "")
    check("镜像没拿到时仍然验 80（待部署页 + 5xx）",
          "502" in pending_status and "504" in pending_status, pending_status)
    # ★ 200 也必须放行：镜像没到位时旧容器可能还在跑，那是「降级但可用」，
    #   不是错误。只允许 5xx 会让 uri 模块当场报错，把真正的失败原因盖掉。
    check("旧容器仍在服务（80 返回 200）时不算错，别让 uri 模块先炸",
          "200" in pending_status and pending.get("failed_when") is False,
          f"{pending_status} / failed_when={pending.get('failed_when')}")

    pending_assert = find("5xx 页面确实是待部署页")
    pending_that = " ".join(str(v) for v in (args_of(pending_assert).get("that") or []))
    check("断言仍然要求「内容真的是待部署页」（不能只验状态码）",
          "应用还没起来" in pending_that, pending_that)

    final_fail = find("判定本次部署失败")
    fail_msg = str(args_of(final_fail).get("msg", ""))
    check("失败在最后统一收场，而不是半路中断", bool(final_fail) and "wf_stack_image_ready" in when_text(final_fail),
          when_text(final_fail))
    check("失败信息给了「离线包」这条路", "离线" in fail_msg and "docker save" in fail_msg)
    check("失败信息给了「镜像加速」这条路（含具体去哪里取地址）", "mirror.aliyuncs.com" in fail_msg)
    check("失败信息里带上了 docker pull 的真实报错", "stderr_lines" in fail_msg)

    # docker_engine 侧：写进文件 ≠ 生效
    docker_flat: list[dict] = []

    def walk_docker(items) -> None:
        for task in items or []:
            if not isinstance(task, dict):
                continue
            docker_flat.append(task)
            for key in ("block", "rescue", "always"):
                walk_docker(task.get(key))

    walk_docker(docker_tasks)
    mirror_check = next(
        (t for t in docker_flat if "取出加速器" in str(t.get("name", ""))), {}
    )
    mirror_cmd = " ".join(argv_of(mirror_check))
    check("docker_engine 会复核加速器是否真的生效", bool(mirror_check), mirror_check)
    check("复核看的是 docker info 里的 Registry Mirrors（不是配置文件）",
          "Registry Mirrors" in mirror_cmd, mirror_cmd)
    assert_tasks = [t for t in docker_flat if "加速器已经真的生效" in str(t.get("name", ""))]
    check("生效性用断言表达（不生效就红）",
          bool(assert_tasks) and "wf_docker_mirror_lines" in str(assert_tasks[0]), assert_tasks[:1])


def test_front_door_template() -> None:
    """对外入口：nginx 反代 + 兜底页。

    这两个模板是「只有一个公网入口」这条边界的落点，离线能验的部分不多，
    但足以挡住最要命的三种写错：端口没跟着变量走、兜底页被外部直接访问、
    以及把发行版的 sites-enabled / conf.d 又 include 回来（那会让默认站点抢 80）。
    """
    print("\n[5b] 对外入口（nginx 反代 + 待部署页）")
    env = make_env()
    ctx = {
        "inventory_hostname": "demo-ecs-1",
        "wf_stack_port": 8080,
        "wf_stack_dir": "/opt/wf/stacks/wf-web",
        "wf_front_dir": "/var/www/wf-front",
        "wf_nginx_user": "www-data",
    }

    out = env.get_template("roles/app_stack/templates/nginx.conf.j2").render(**ctx)
    flat = " ".join(out.split())
    # 注释里会提到 sites-enabled（解释为什么不用它），所以判定要看"去掉注释的配置"
    code_flat = " ".join(
        line for line in out.splitlines() if not line.strip().startswith("#")
    )

    check("渲染出 server 块", "server {" in flat)
    check("80 是默认站点（否则请求会落到发行版默认站点上）",
          "listen 80 default_server;" in flat)
    check("反代目标是回环 + wf_stack_port（不是硬编码 8080）",
          "proxy_pass http://127.0.0.1:8080;" in flat)
    check("端口换成别的值时模板跟着变",
          "127.0.0.1:9090" in " ".join(
              env.get_template("roles/app_stack/templates/nginx.conf.j2")
              .render(**dict(ctx, wf_stack_port=9090)).split()),
          "如果这里失败，说明端口被写死了")
    check("上游不可用时有兜底页", "error_page 502 503 504 /_wf-pending.html;" in flat)
    check("兜底页是 internal（外部不能绕过反代直接访问）",
          "location = /_wf-pending.html" in flat and "internal;" in flat)
    check("不再 include 发行版的 sites-enabled / conf.d",
          "sites-enabled" not in code_flat and "conf.d" not in code_flat,
          "include 回来就会出现两个 server 抢 80，且哪一个生效取决于文件名顺序")
    check("进程用户来自变量（Debian 是 www-data，RHEL 系是 nginx）",
          "user www-data;" in flat)

    page = env.get_template("roles/app_stack/templates/wf-pending.html.j2").render(**ctx)
    check("待部署页含主机名", "demo-ecs-1" in page)
    check("待部署页是完整 HTML",
          page.lstrip().startswith("<!DOCTYPE html>") and "</html>" in page)
    check("待部署页不含渲染时间（否则每次运行都 changed）",
          "{{" not in page and " now(" not in page)
    check("两台机器渲染出的待部署页不同（体现各自身份）",
          page != env.get_template("roles/app_stack/templates/wf-pending.html.j2")
          .render(**dict(ctx, inventory_hostname="demo-ecs-2")))


def test_baseline_templates() -> None:
    print("\n[6] 基线配置模板（sysctl / limits）")
    env = make_env()
    defaults = yaml.safe_load((ROOT / "roles" / "baseline" / "defaults" / "main.yml").read_text(encoding="utf-8"))
    ctx = {"wf_sysctl_settings": defaults["wf_sysctl_settings"], "wf_limits_settings": defaults["wf_limits_settings"]}

    sysctl_out = env.get_template("roles/baseline/templates/99-wf-sysctl.conf.j2").render(**ctx)
    sysctl_lines = [ln for ln in sysctl_out.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    check(
        f"sysctl：{len(defaults['wf_sysctl_settings'])} 项全部渲染",
        len(sysctl_lines) == len(defaults["wf_sysctl_settings"]),
        sysctl_out,
    )
    check("sysctl：格式是 key = value", all(" = " in ln for ln in sysctl_lines), sysctl_lines)
    check("sysctl：带「手工修改会被覆盖」的说明", "手工修改" in sysctl_out)

    limits_out = env.get_template("roles/baseline/templates/99-wf-limits.conf.j2").render(**ctx)
    limits_lines = [ln for ln in limits_out.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    check(
        f"limits：{len(defaults['wf_limits_settings'])} 项全部渲染",
        len(limits_lines) == len(defaults["wf_limits_settings"]),
        limits_out,
    )
    check("limits：每行 4 段（domain/type/item/value）",
          all(len(ln.split()) == 4 for ln in limits_lines), limits_lines)


def test_inventory_generator() -> None:
    print("\n[7] 云清单生成器")
    gen = load_module(ROOT / "tools" / "gen_cloud_inventory.py", "gen_inv_ut")

    outputs = {
        "region": {"value": "cn-hangzhou"},
        "summary": {
            "value": {
                "1": {"name": "demo-ecs-1", "private_ip": "10.10.1.2", "public_ip": "47.98.12.34",
                      "instance_type": "ecs.e-c1m1.large"},
                "2": {"name": "demo-ecs-2", "private_ip": "10.10.1.3", "public_ip": "",
                      "instance_type": "ecs.e-c1m1.large"},
            }
        },
    }
    instances = gen.pick_instances(outputs)
    check("从 summary 里解析出 2 台实例", len(instances) == 2, instances)
    check("实例字段完整", all(k in instances[0] for k in ("name", "public_ip", "private_ip")), instances[0])

    # 退化成 public_ips / instance_private_ips 的情况
    fallback = {
        "instance_public_ips": {"value": {"1": "1.2.3.4"}},
        "instance_private_ips": {"value": {"1": "10.0.0.2"}},
    }
    check("没有 summary 时能退化解析", len(gen.pick_instances(fallback)) == 1, gen.pick_instances(fallback))
    check("空输出 → 空列表", gen.pick_instances({}) == [])

    text = gen.render(instances, "cn-hangzhou", "root", "~/.ssh/id_ed25519")
    data = yaml.safe_load(text)
    check("生成的清单是合法 YAML", isinstance(data, dict), text)
    cloud = ((data or {}).get("all") or {}).get("children", {}).get("cloud", {})
    check("生成了 cloud 组", "cloud" in ((data or {}).get("all") or {}).get("children", {}), text)
    hosts = cloud.get("hosts") or {}
    check("cloud 组里有 2 台主机", len(hosts) == 2, hosts)
    check("主机带 ansible_host", all("ansible_host" in v for v in hosts.values()), hosts)
    check("主机带登录用户", all(v.get("ansible_user") == "root" for v in hosts.values()), hosts)
    check("主机带说明", all("wf_note" in v for v in hosts.values()), hosts)
    check("生成的清单里没有 local 组（本地由 hosts.yml 负责）", "local" not in ((data or {}).get("all") or {}).get("children", {}), text)

    empty = yaml.safe_load(gen.render([], "", "root", ""))
    check("没有可用实例时也能生成合法清单", isinstance(empty, dict), gen.render([], "", "root", ""))


def test_guard_truth_table() -> None:
    """对「本地服务器不覆盖」的保护公式做真值表验证。

    这是整套东西里最该被测的一处：公式写反了，保护就是反的，
    而且不会以"报错"的形式暴露 —— 只会安静地放行。

    刻意不复述公式。这里从 roles/guard/tasks/main.yml 里把表达式读出来求值，
    所以测的是"真正会跑的那份公式"，公式改了这里会立刻跟着变。
    """
    import jinja2
    import jinja2.meta

    print("\n[8] guard 保护公式的真值表（读的是角色文件里的公式本身）")

    guard_tasks = yaml.safe_load(
        (ROOT / "roles" / "guard" / "tasks" / "main.yml").read_text(encoding="utf-8")
    )
    exprs: dict[str, str] = {}
    for task in guard_tasks or []:
        if "计算保护状态" in str(task.get("name", "")):
            exprs = dict(task.get("ansible.builtin.set_fact") or {})
    if not check("从 guard 里读到了 wf_protected / wf_can_write 两个表达式", len(exprs) == 2, exprs):
        return

    env = make_env(strict=True)

    # 公式里允许出现的变量。多一个就说明公式动过，这个测试要跟着改。
    known_vars = {
        "wf_group_is_protected",
        "wf_in_local_group",
        "local_mutation_allowed",
        "local_overwrite_allowed",
    }
    for key, expr in exprs.items():
        # 注意：角色文件里的表达式本身已经带 {{ }}，不要再套一层
        names = jinja2.meta.find_undeclared_variables(env.parse(expr))
        check(
            f"{key} 只用到已知开关（{key} 引用了 {'、'.join(sorted(names)) or '无'}）",
            names <= known_vars,
            f"多出来的变量: {sorted(names - known_vars)}",
        )

    def evaluate(**ctx) -> tuple[bool, bool]:
        def one(expr: str) -> bool:
            # Ansible 里 set_fact 的结果是字符串，这里照着做：先渲染成字符串再转布尔
            rendered = env.from_string(expr).render(**ctx)
            return str(rendered).strip().lower() == "true"

        return one(exprs["wf_protected"]), one(exprs["wf_can_write"])

    # 场景 / 组声明受保护 / 属于local组 / 允许变更 / 允许覆盖 / 期望受保护 / 期望可覆盖
    rows = [
        ("本地机器 · 默认（什么都不传）", True, True, False, False, True, False),
        ("本地机器 · 只解锁变更", True, True, True, False, False, False),
        ("本地机器 · 变更+覆盖都解锁", True, True, True, True, False, True),
        ("★ 本地机器 · 只给覆盖开关（不给变更开关）", True, True, False, True, True, False),
        ("★ 本地机器 · group_vars 漏了受保护声明", False, True, False, False, True, False),
        ("云服务器 · 默认", False, False, False, False, False, True),
        ("云服务器 · 但那个组被声明为受保护", True, False, False, False, True, True),
        ("★ -e local_mutation_allowed=true（字符串）", True, True, "true", False, False, False),
        ("★ -e local_mutation_allowed=false（字符串）", True, True, "false", False, True, False),
        ("★ -e local_mutation_allowed=no（字符串）", True, True, "no", False, True, False),
        ("★ -e local_mutation_allowed=1（字符串）", True, True, "1", False, False, False),
    ]

    for label, grp, in_local, mut, ovw, want_protected, want_can_write in rows:
        got_protected, got_can_write = evaluate(
            wf_group_is_protected=grp,
            wf_in_local_group=in_local,
            local_mutation_allowed=mut,
            local_overwrite_allowed=ovw,
        )
        check(
            f"{label} → 受保护={got_protected} / 可覆盖={got_can_write}",
            (got_protected, got_can_write) == (want_protected, want_can_write),
            f"期望 受保护={want_protected} / 可覆盖={want_can_write}",
        )

    # 把真值表里最要紧的两条单独再断言一次，读起来更醒目
    protected, _ = evaluate(
        wf_group_is_protected=True, wf_in_local_group=True,
        local_mutation_allowed=False, local_overwrite_allowed=False,
    )
    check("★ 默认情况下本地机器一定受保护（这是整个需求的核心）", protected is True)

    protected, can_write = evaluate(
        wf_group_is_protected=True, wf_in_local_group=True,
        local_mutation_allowed=True, local_overwrite_allowed=False,
    )
    check("★ 只解锁变更时：不再受保护，但仍不许覆盖已有文件", protected is False and can_write is False)

    # 穷举全部 16 种开关组合，验证"更危险的能力蕴含较弱的能力"。
    # 这条不变量比逐行写死的期望值更能挡住"改公式时只顾一个方向"的错误。
    bad_implication = []
    bad_cloud = []
    for grp in (True, False):
        for in_local in (True, False):
            for mut in (True, False):
                for ovw in (True, False):
                    protected, can_write = evaluate(
                        wf_group_is_protected=grp, wf_in_local_group=in_local,
                        local_mutation_allowed=mut, local_overwrite_allowed=ovw,
                    )
                    combo = f"组声明={grp} 在local={in_local} 变更={mut} 覆盖={ovw}"
                    # 本地机器：能覆盖 ⟹ 变更已解锁
                    if in_local and can_write and not mut:
                        bad_implication.append(combo)
                    # 云服务器：那两个"本地开关"对它必须毫无影响 ——
                    # 可写永远是 True；是否受保护只由它所在组的声明决定。
                    # （组声明为受保护时它就是受保护的，这是"任一处标对就保护得上"的设计。）
                    if not in_local and (not can_write or protected != (grp and not mut)):
                        bad_cloud.append(f"{combo} → 受保护={protected} 可覆盖={can_write}")

    check(
        "穷举 16 种组合：本地机器「可覆盖 ⟹ 变更已解锁」始终成立",
        not bad_implication,
        "反例: " + "；".join(bad_implication),
    )
    check(
        "穷举 16 种组合：本地开关对云服务器完全无影响",
        not bad_cloud,
        "反例: " + "；".join(bad_cloud),
    )
    check(
        "★ 穷举 16 种组合：本地机器不开任何开关时一律受保护",
        all(
            evaluate(
                wf_group_is_protected=True, wf_in_local_group=True,
                local_mutation_allowed=False, local_overwrite_allowed=ovw,
            )[0]
            for ovw in (True, False)
        ),
    )

    # 变量名写错必须报错，而不是静默变成"不保护"
    raised = False
    try:
        # 故意把 wf_in_local_group 拼错成 wf_in_local_grup
        env.from_string("{{ wf_in_local_grup | bool }}").render(wf_in_local_group=True)
    except Exception:
        raised = True
    check("变量名写错时会报错（不会静默当成 False）", raised)

    # group_vars 里的真实声明要和公式的假设一致
    local_gv = yaml.safe_load((group_vars_dir() / "local.yml").read_text(encoding="utf-8")) or {}
    cloud_gv = yaml.safe_load((group_vars_dir() / "cloud.yml").read_text(encoding="utf-8")) or {}
    check("group_vars/local.yml 声明了受保护", local_gv.get("wf_group_is_protected") is True, local_gv)
    check("group_vars/cloud.yml 声明了不受保护", cloud_gv.get("wf_group_is_protected") is False, cloud_gv)
    check(
        "group_vars/local.yml 里列了本地禁区路径",
        bool(local_gv.get("wf_local_forbidden_paths")),
        local_gv.get("wf_local_forbidden_paths"),
    )


def test_shell_bodies_compile() -> None:
    """所有 shell / command 任务的正文都要能被 Jinja 编译通过。

    为什么这条必须单独测：
        Ansible 会拿 Jinja2 把 shell 正文**整段渲染一遍**，而模板引擎
        **不区分注释和正文** —— 只看有没有左双花括号。所以只要 shell 正文里
        出现了字面的双花括号（比如写 docker --format 的 Go 模板、或者只是
        在注释里举例说明），就会在任务执行时报：

            Syntax error in template: Expected an expression,
            got 'end of print statement'

        这个错误只有真正跑起来才会出现：--syntax-check 不管，YAML 解析也不管。
        实测被它咬过一次 —— 而且是在一条"提醒自己别写双花括号"的注释里。

    语法错误在 compile 阶段就会抛，和变量有没有定义无关，
    所以这里不需要提供任何变量。
    """
    print("\n[9] shell / command 正文都能被 Jinja 编译（含注释里的双花括号）")

    env = make_env()
    bad: list[str] = []
    total = 0

    def walk(task_list, where: str) -> None:
        nonlocal total
        for task in task_list or []:
            if not isinstance(task, dict):
                continue
            module, args = next(
                ((k, v) for k, v in task.items() if isinstance(k, str) and k.startswith("ansible.builtin.")),
                ("", None),
            )
            short = module.split(".")[-1] if module else ""
            if short in {"shell", "command", "script", "raw"} and isinstance(args, str):
                total += 1
                try:
                    env.parse(args)
                except Exception as exc:  # noqa: BLE001
                    bad.append(f"{where} · {task.get('name', '?')}\n      {exc}")
            for key in ("block", "rescue", "always"):
                walk(task.get(key), where)

    for path in sorted(ROOT.glob("roles/*/tasks/*.yml")) + sorted(ROOT.glob("roles/*/handlers/*.yml")):
        walk(yaml.safe_load(path.read_text(encoding="utf-8")), path.relative_to(ROOT).as_posix())

    check(f"{total} 段 shell/command 正文全部可编译", not bad, "\n".join(bad))

    # 反向验证：确认这个检查真的抓得住（构造一段带字面双花括号的 shell）
    caught = False
    try:
        env.parse('echo "{{ }}"\n')
    except Exception:  # noqa: BLE001
        caught = True
    check("★ 反向验证：字面双花括号会被这个检查抓到", caught)

    # 也确认它不会误伤正常的占位符
    false_positive = False
    try:
        env.parse("echo {{ wf_timezone }}\n")
    except Exception:  # noqa: BLE001
        false_positive = True
    check("正常的占位符不会被误判", not false_positive)


def main() -> int:
    print()
    print("═" * 78)
    print(" ansible 目录 · 离线自测（不需要安装 Ansible）")
    print("═" * 78)
    print(f"  目录: {ROOT}")

    test_yaml_parsing()
    test_filters()
    test_audit_report()
    test_daemon_json()
    test_stack_templates()
    test_release_rollback()
    test_front_door_template()
    test_image_preflight()
    test_baseline_templates()
    test_inventory_generator()
    test_guard_truth_table()
    test_shell_bodies_compile()

    print()
    print("═" * 78)
    print(f"  通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print()
        for label in FAILED:
            print(f"    ✗ {label}")
    print("═" * 78)
    print()
    print("  说明：本脚本只验证「不装 Ansible 也能验证的部分」。")
    print("        模块在远端的实际行为、变量优先级、SSH/sudo 前置条件，")
    print("        只能在真正运行 ansible-playbook 时才知道。")
    print()
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
