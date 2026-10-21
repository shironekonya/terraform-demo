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
        check("端口映射正确（8080:80）", svc.get("ports") == ["8080:80"], svc.get("ports"))
        check("首页文件以只读挂载", any("index.html" in str(v) and str(v).endswith(":ro")
                                        for v in svc.get("volumes") or []), svc.get("volumes"))
        check("带健康检查", "healthcheck" in svc, svc)
        check("刻意不写 version 字段（compose v2 已废弃）", "version" not in (data or {}), list(data or {}))
    except yaml.YAMLError as exc:
        check("compose 渲染结果 → 合法 YAML", False, f"{exc}\n{compose_out}")

    html_out = env.get_template("roles/app_stack/templates/index.html.j2").render(**ctx)
    check("首页含主机名", "demo-ecs-1" in html_out)
    check("首页含地址", "47.98.12.34" in html_out)
    check("首页是完整 HTML", html_out.lstrip().startswith("<!DOCTYPE html>") and "</html>" in html_out)
    check("首页会显示容器名", "wf-web" in html_out)

    # 关键：不同主机的渲染结果必须不同（否则"批量下发同一份模板"就没意义）
    ctx2 = dict(ctx, inventory_hostname="demo-ecs-2", ansible_host="47.98.56.78")
    html_out2 = env.get_template("roles/app_stack/templates/index.html.j2").render(**ctx2)
    check("两台机器渲染出的首页不同（体现各自身份）", html_out != html_out2)


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
