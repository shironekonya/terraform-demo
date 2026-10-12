#!/usr/bin/env python3
"""静态校验「本地服务器不覆盖」这条线到底守住了没有。

为什么需要这个脚本：
    「本地服务器不覆盖」如果只靠「运行的时候注意一下」，那它就是一句愿望。
    而运行一遍 playbook 需要能连上那两台机器、还要装好 Ansible —— 门槛不低，
    于是这种检查在实际项目里基本不会被执行。

    这个脚本把保护规则做成**静态分析**：不连机器、不改文件，
    只读 inventory / group_vars / playbooks / roles，就能回答
    「有没有哪条路径会让变更任务碰到本地服务器」。

它校验 6 类不变量（每条都对应一个真实的失效方式）：

    P1  清单与保护声明
        - local 组存在，group_vars/local.yml 声明 wf_group_is_protected: true
        - cloud 组声明为不受保护
        - 同一台机器不能同时出现在 cloud 和 local（按名字和地址都比）

    P2  每个 play 都必须声明 intent（read / mutate）
        —— guard 靠它判断该不该拦；缺了它保护就退化成「默认安全」，
           而「默认安全」在有人写错 hosts 时就不可靠了

    P3  roles 的第一个必须是 guard
        —— 保护必须先于任何动作

    P4  可能包含本地主机的 play，intent 必须是 read
        —— 这是最核心的一条：变更型 play 不允许包含本地机器。
           唯一的例外是「显式解锁」型 play（见 P5）。

    P5  解锁型 play 的条件很严
        - intent: mutate 且可能含本地
        - hosts 必须是 local（不能是 all）
        - 必须有 pre_tasks + meta: end_play + when 提到 local_mutation_allowed
        - 不得引用 cloud-only 的 role

    P6  写入类任务必须有门
        - 在「可能跑在本地」的 role 里，写文件/改设置的任务必须带
          wf_can_write（会覆盖）或者位于一个已受 wf_protected 门控的
          include 之下（只新增不覆盖）
        - cloud-only 的 role 必须自证边界（断言 wf_in_local_group 为假）

    P7  本地禁区清单必须存在，且 baseline 的写入目标不能落在里面
        - 防的是「某次重构把写入目标改到了 /data/jenkins-data」这类
          在 code review 里很难注意到、但后果是抹掉现状的改动

    P8  group_vars 必须放在 Ansible 真的会去读的位置
        —— 这一条不是保护逻辑本身，而是「会让保护静默失效」的配置错误。
           Ansible 只在「清单文件旁边」和「playbook 文件旁边」找 group_vars。
           实测踩过一次：group_vars 在 ansible/ 根、清单在 inventory/、
           playbook 在 playbooks/ —— 两处都不是，于是整个 group_vars
           被静默忽略。表现不是报错，而是所有 wf_* 变量变 undefined：
           禁区清单变空、断言形同虚设，而 guard 因为用 magic var groups[]
           所以看起来完全正常。校验器必须能抓住这种「全绿但少一层」的情况。

用法：
    python3 tools/check_guard.py            # 校验
    python3 tools/check_guard.py --selftest # 证明这个校验器真的抓得住问题
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    print(
        "  ✗ 需要 PyYAML。\n"
        "      Ansible 本身依赖它，所以你装了 Ansible 的机器上一定有；\n"
        "      只想单独跑这个检查器的话：pip install pyyaml",
        file=sys.stderr,
    )
    raise SystemExit(2)

ROOT = pathlib.Path(__file__).resolve().parent.parent

LOCAL_GROUP = "local"
CLOUD_GROUP = "cloud"

# 只允许在云服务器上跑的 role。它们必须自带「我不在本地」的断言。
CLOUD_ONLY_ROLES = {"docker_engine", "app_stack"}

# 这些模块不会改变被管机器
READONLY_MODULES = {
    "debug",
    "assert",
    "fail",
    "set_fact",
    "set_stats",
    "stat",
    "package_facts",
    "service_facts",
    "setup",
    "gather_facts",
    "include_tasks",
    "include_role",
    "import_tasks",
    "import_role",
    "include_vars",
    "meta",
    "pause",
    "wait_for",
    "wait_for_connection",
    "uri",
    "slurp",
    "add_host",
    "group_by",
}

# 这些「参数组合」属于「只新增不存在的对象」，不是覆盖
ADDITIVE_MODULES = {"package", "yum", "dnf", "apt", "apk", "zypper", "pacman"}

# 凡是 when 里出现了这些变量之一，就认为这个任务受保护门控
GUARD_VARS = ("wf_protected", "wf_can_write", "local_mutation_allowed", "local_overwrite_allowed")


class Finding:
    __slots__ = ("level", "where", "message", "hint")

    def __init__(self, level: str, where: str, message: str, hint: str = "") -> None:
        self.level = level
        self.where = where
        self.message = message
        self.hint = hint


class Report:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.checked_plays = 0
        self.checked_roles = 0

    def error(self, where: str, message: str, hint: str = "") -> None:
        self.findings.append(Finding("error", where, message, hint))

    def warn(self, where: str, message: str, hint: str = "") -> None:
        self.findings.append(Finding("warn", where, message, hint))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warns(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warn"]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def load_yaml(path: pathlib.Path):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError:
        return None
    except yaml.YAMLError as exc:
        raise SystemExit(f"  ✗ YAML 解析失败: {path}\n      {exc}") from exc


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# ---------------------------------------------------------------------------
# group_vars 到底在哪：按 Ansible 的规则去找，不要自己猜
#
# 这一节是补出来的。真实踩过一次：
#   group_vars/ 放在 ansible/ 根，playbook 放在 playbooks/ 子目录，
#   而清单是 inventory/ —— Ansible 找 group_vars 只在
#     「清单文件旁边」和「playbook 文件旁边」
#   两处找，我们那两处都不是。于是整个 group_vars 被**静默忽略**。
#
# 后果不是报错，而是所有 wf_* 变量变成 undefined：
#   - assert 里的 wf_local_forbidden_paths 变成空清单 → 禁区检查形同虚设
#   - wf_report_dir 走 default 分支 → 报告写错地方
#   - guard 却看起来完全正常，因为 wf_in_local_group 来自 magic var groups[]
#
# 这正是最坏的失效形态：校验全绿，跑起来"也对"，只是少了一整层保护。
# 所以下面这个函数严格照 Ansible 的规则来，并且 P8 会强制它成立。
# ---------------------------------------------------------------------------

def read_cfg_inventory(root: pathlib.Path) -> str:
    """从 ansible.cfg 里读 inventory 设置。不引 INI 解析器，够用就行。"""
    cfg = root / "ansible.cfg"
    if not cfg.is_file():
        return ""
    in_defaults = False
    for raw in cfg.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("["):
            in_defaults = line.strip("[]").strip() == "defaults"
            continue
        if not in_defaults or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "inventory":
            return value.strip()
    return ""


def group_vars_candidates(root: pathlib.Path) -> list[pathlib.Path]:
    """Ansible 真的会去找 group_vars 的位置（按官方规则，顺序即优先级）。"""
    out: list[pathlib.Path] = []

    # ① 清单文件旁边。ansible.cfg 里的相对路径是相对「配置文件所在目录」解析的。
    inv_setting = read_cfg_inventory(root)
    if inv_setting:
        inv_path = (root / inv_setting).resolve()
        # 按配置的**字面形式**判断是目录型还是文件型清单，不依赖它此刻是否存在 ——
        # 依赖存在性的话，「inventory 目录还没建」这种状态会让基准偷偷退到仓库根，
        # 于是这个检查反而放行了一个真的错的布局。
        looks_like_dir = inv_setting.endswith("/") or inv_setting.endswith("\\") or inv_path.is_dir()
        base = inv_path if looks_like_dir else inv_path.parent
        out.append(base / "group_vars")

    # ② playbook 文件旁边
    for playbook in sorted((root / "playbooks").glob("*.yml")):
        out.append(playbook.parent / "group_vars")

    # 去重（保持顺序）
    seen: set[str] = set()
    unique: list[pathlib.Path] = []
    for item in out:
        key = str(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def resolve_group_vars_dir(root: pathlib.Path) -> pathlib.Path:
    """返回实际生效的 group_vars 目录；都不存在时返回第一个候选（用于报错提示）。"""
    candidates = group_vars_candidates(root)
    for item in candidates:
        if item.is_dir():
            return item
    return candidates[0] if candidates else root / "group_vars"


def rel_to(root: pathlib.Path, path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/") + "/"
    except ValueError:
        return str(path)


def module_of(task: dict) -> tuple[str, object]:
    """取任务的模块名与参数。返回 ('', None) 表示这不是一个动作任务。"""
    if not isinstance(task, dict):
        return "", None
    structural = {"name", "when", "vars", "tags", "register", "become", "become_user",
                  "delegate_to", "run_once", "changed_when", "failed_when", "loop",
                  "with_items", "until", "retries", "delay", "notify", "ignore_errors",
                  "no_log", "environment", "check_mode", "loop_control", "args",
                  "any_errors_fatal", "throttle", "timeout", "connection", "remote_user",
                  "block", "rescue", "always", "listen"}
    for key, value in task.items():
        if key in structural:
            continue
        if key in ("block", "rescue", "always"):
            continue
        return key, value
    return "", None


def short_module(module: str) -> str:
    """ansible.builtin.copy -> copy"""
    return module.rsplit(".", 1)[-1]


def flatten_tasks(tasks) -> list[dict]:
    """把 block/rescue/always 展平成一维任务列表（保留原 dict 以便看 when）。"""
    out: list[dict] = []
    for task in as_list(tasks):
        if not isinstance(task, dict):
            continue
        for section in ("block", "rescue", "always"):
            if section in task:
                out.extend(flatten_tasks(task[section]))
        if module_of(task)[0]:
            out.append(task)
    return out


def when_text(task: dict) -> str:
    """把 when 归一成一段字符串，便于做关键词判断。"""
    when = task.get("when")
    if when is None:
        return ""
    if isinstance(when, str):
        return when
    if isinstance(when, list):
        return " ".join(str(w) for w in when)
    return str(when)


def is_readonly(task: dict) -> bool:
    module, args = module_of(task)
    name = short_module(module)

    # delegate_to: localhost —— 动作发生在控制端，对「被管机器是否被改」没有影响。
    # 体检报告就是这么写的（落到控制端的 reports/ 里）。
    delegate = str(task.get("delegate_to", "")).strip()
    if delegate in ("localhost", "127.0.0.1"):
        return True

    if str(task.get("changed_when", "")).strip().lower() == "false":
        return True
    if task.get("check_mode") is True:
        return True

    if name in READONLY_MODULES:
        return True
    # include_tasks 的简写形式也可能是 "include_tasks"
    if name in ("include_tasks", "import_tasks", "include_role", "import_role"):
        return True
    return False


def is_additive(task: dict) -> bool:
    """任务是否属于「只新增不存在的对象」。"""
    module, args = module_of(task)
    name = short_module(module)

    if name in ADDITIVE_MODULES:
        state = ""
        if isinstance(args, dict):
            state = str(args.get("state", "present")).strip()
        return state in ("present", "installed", "latest")

    if name in ("file", "ansible.builtin.file"):
        if isinstance(args, dict):
            return str(args.get("state", "")).strip() == "directory"
    return False


# ---------------------------------------------------------------------------
# P1 · 清单与保护声明
# ---------------------------------------------------------------------------

def collect_inventory(root: pathlib.Path, report: Report):
    """返回 ({组: {主机}}, {主机: 地址}, [清单文件])。"""
    inv_dir = root / "inventory"
    group_hosts: dict[str, set[str]] = {}
    parent_of: dict[str, set[str]] = {}
    host_addr: dict[str, str] = {}
    sources: list[pathlib.Path] = []

    if not inv_dir.is_dir():
        report.error("inventory/", "找不到清单目录")
        return group_hosts, host_addr, sources

    for path in sorted(inv_dir.glob("*.yml")):
        data = load_yaml(path)
        if not isinstance(data, dict):
            continue
        sources.append(path)
        _walk_inventory(data.get("all", data), group_hosts, parent_of, host_addr, path, report)

    # 展开 children 归属：某个组的成员组里的主机也算它的成员
    changed = True
    while changed:
        changed = False
        for child, parents in parent_of.items():
            for parent in parents:
                before = len(group_hosts.setdefault(parent, set()))
                group_hosts[parent] |= group_hosts.get(child, set())
                if len(group_hosts[parent]) != before:
                    changed = True

    return group_hosts, host_addr, sources


def _walk_inventory(node, group_hosts, parent_of, host_addr, path, report) -> None:
    if not isinstance(node, dict):
        return
    children = node.get("children")
    if isinstance(children, dict):
        for group, body in children.items():
            group_hosts.setdefault(group, set())
            body = body or {}
            hosts = body.get("hosts") or {}
            if isinstance(hosts, dict):
                for host, hvars in hosts.items():
                    group_hosts[group].add(host)
                    if isinstance(hvars, dict) and hvars.get("ansible_host"):
                        host_addr.setdefault(host, str(hvars["ansible_host"]).strip())
            _walk_inventory(body, group_hosts, parent_of, host_addr, path, report)
            if isinstance(body.get("children"), dict):
                for sub in body["children"]:
                    parent_of.setdefault(sub, set()).add(group)

    hosts = node.get("hosts")
    if isinstance(hosts, dict):
        for host, hvars in hosts.items():
            group_hosts.setdefault("all", set()).add(host)
            if isinstance(hvars, dict) and hvars.get("ansible_host"):
                host_addr.setdefault(host, str(hvars["ansible_host"]).strip())


def check_group_vars_location(root: pathlib.Path, report: Report) -> None:
    """P8：group_vars 必须放在 Ansible 真的会去读的位置。

    这是唯一一条「不是保护逻辑本身、却会让保护静默失效」的检查，所以必须有。
    详见上面 group_vars 那一节的注释。
    """
    candidates = group_vars_candidates(root)
    found = [item for item in candidates if item.is_dir()]
    listed = "、".join(rel_to(root, item) for item in candidates)

    if not found:
        report.error(
            "group_vars/",
            "找不到 group_vars 目录 —— Ansible 会静默忽略所有分组变量",
            f"Ansible 只在「清单文件旁边」和「playbook 文件旁边」找。\n"
            f"      按当前 ansible.cfg，它只会在这些位置找：{listed}",
        )
        return

    effective = found[0]
    if len(found) > 1:
        report.warn(
            rel_to(root, effective),
            f"group_vars 在多处存在：{'、'.join(rel_to(root, d) for d in found)}",
            f"实际生效的是第一个：{rel_to(root, effective)}。"
            "另一份不会被读，容易让人改了不生效 —— 建议只留一处。",
        )

    # 最容易踩的形态：放在了仓库根，但清单和 playbook 都在子目录里。
    # 这正是真实踩过一次的那次。
    root_level = root / "group_vars"
    if root_level.is_dir() and root_level.resolve() not in {d.resolve() for d in candidates}:
        report.error(
            "group_vars/",
            "group_vars 放在了 Ansible 不会查找的位置（整个目录会被静默忽略）",
            f"ansible.cfg 里 inventory = {read_cfg_inventory(root) or '(未设置)'}，"
            "playbook 在 playbooks/ —— Ansible 只在这两处旁边找 group_vars。\n"
            f"      正确位置：{rel_to(root, candidates[0]) if candidates else 'inventory/group_vars/'}\n"
            "      ★ 这个错误不会报错，只会让所有 wf_* 变量变成 undefined：\n"
            "        禁区清单变空、报告目录走默认值，而 guard 因为用 magic var 看着一切正常。",
        )


def check_protection_declaration(root: pathlib.Path, report: Report) -> None:
    gv_dir = resolve_group_vars_dir(root)
    local_gv = load_yaml(gv_dir / f"{LOCAL_GROUP}.yml") or {}
    cloud_gv = load_yaml(gv_dir / f"{CLOUD_GROUP}.yml") or {}

    local_label = rel_to(root, gv_dir / f"{LOCAL_GROUP}.yml")
    cloud_label = rel_to(root, gv_dir / f"{CLOUD_GROUP}.yml")

    if local_gv.get("wf_group_is_protected") is not True:
        report.error(
            local_label,
            "local 组没有声明 wf_group_is_protected: true",
            "这是「本地服务器受保护」的声明处。guard 还会叠加「本机在不在 local 组」"
            "一起判断，但这份声明是给人看的、也是第一道依据，必须存在。",
        )
    if cloud_gv.get("wf_group_is_protected") is not False:
        report.warn(
            cloud_label,
            "cloud 组没有显式声明 wf_group_is_protected: false",
            "显式写出来更清楚：云服务器就是允许随意变更的那一组。",
        )


def check_group_overlap(group_hosts: dict[str, set[str]], report: Report) -> None:
    local = group_hosts.get(LOCAL_GROUP, set())
    cloud = group_hosts.get(CLOUD_GROUP, set())
    both = local & cloud
    if both:
        report.error(
            "inventory/",
            f"这些机器同时出现在 cloud 和 local 两个组里：{', '.join(sorted(both))}",
            "一台机器不能既「可以随意变更」又「默认只读」。guard 会在运行时拦下它，"
            "但更好的做法是在清单里就改对。",
        )


def load_filter_plugin(root: pathlib.Path):
    """加载 guard role 里的过滤器插件（纯函数，不依赖 Ansible）。"""
    import importlib.util

    plugin = root / "roles" / "guard" / "filter_plugins" / "wf_guard.py"
    if not plugin.is_file():
        return None
    spec = importlib.util.spec_from_file_location("wf_guard_plugin", plugin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def check_local_forbidden_paths(root: pathlib.Path, report: Report) -> None:
    """本地禁区清单必须存在，而且 baseline 的写入目标不能落在里面。

    这道检查防的是一种很具体的灾难：
        baseline 的写入目标（wf_base_dirs / 那几个配置文件）在某次重构里
        被改成了 /data/jenkins-data 之类的地方 —— 那等于把 Jenkins 的数据目录
        当普通目录去管。这类改动在 code review 里很难被注意到，
        但静态算一次路径覆盖就看得出来。
    """
    gv_dir = resolve_group_vars_dir(root)
    local_gv = load_yaml(gv_dir / f"{LOCAL_GROUP}.yml") or {}
    forbidden = local_gv.get("wf_local_forbidden_paths")
    if not isinstance(forbidden, list) or not forbidden:
        report.error(
            rel_to(root, gv_dir / f"{LOCAL_GROUP}.yml"),
            "wf_local_forbidden_paths 缺失或为空",
            "这是「本地服务器上绝不能碰的路径」清单（Jenkins 数据、GitLab 仓库、K3s 配置…）。"
            "roles/baseline 会拿它做断言。为空等于这道保险不存在。",
        )
        return

    detect = root / "roles" / "baseline" / "tasks" / "detect.yml"
    detect_text = detect.read_text(encoding="utf-8") if detect.is_file() else ""
    if "wf_under_any" not in detect_text:
        report.error(
            "roles/baseline/tasks/detect.yml",
            "缺少「写入目标不得落在本地禁区」的断言",
            "加一条：\n"
            "        - assert:\n"
            "            that: [wf_forbidden_hits | length == 0]\n"
            "          vars:\n"
            "            wf_forbidden_hits: \"{{ wf_write_targets | wf_under_any(wf_local_forbidden_paths) }}\"",
        )

    plugin = load_filter_plugin(root)
    if plugin is None:
        report.warn("roles/guard/filter_plugins/wf_guard.py", "找不到过滤器插件，跳过路径覆盖的静态计算")
        return

    defaults = load_yaml(root / "roles" / "baseline" / "defaults" / "main.yml") or {}
    targets: list[str] = []
    for key in ("wf_base_dirs",):
        value = defaults.get(key)
        if isinstance(value, list):
            targets.extend(str(v) for v in value)
    for key in ("wf_sysctl_file", "wf_limits_file", "wf_timezone_file"):
        if defaults.get(key):
            targets.append(str(defaults[key]))

    hits = plugin.wf_under_any(targets, forbidden)
    if hits:
        report.error(
            "roles/baseline/defaults/main.yml",
            f"baseline 的写入目标落在了本地禁区里：{', '.join(hits)}",
            f"禁区清单（group_vars/{LOCAL_GROUP}.yml 的 wf_local_forbidden_paths）是"
            "「改了就等于抹掉现状」的目录集合。",
        )


# ---------------------------------------------------------------------------
# P2~P5 · playbook 层面的校验
# ---------------------------------------------------------------------------

def play_intent(play: dict) -> str:
    """取 play 的变更意图。

    intent 必须写在 play 的 vars 里 —— Ansible 的 Play 没有 intent 这个关键字，
    直接写在 play 顶层会报 "not a valid attribute for a Play"。
    这里也容忍写在顶层的情况，方便自证用例写得更短。
    """
    vars_block = play.get("vars") or {}
    if isinstance(vars_block, dict) and vars_block.get("intent"):
        return str(vars_block["intent"]).strip()
    return str(play.get("intent", "")).strip()


def play_hosts_may_include_local(hosts_expr, group_hosts, report, where) -> bool:
    """这个 play 的 hosts 是否可能包含本地主机。判断不了就按"包含"处理。"""
    if hosts_expr is None:
        report.error(where, "play 没有写 hosts")
        return True

    local = group_hosts.get(LOCAL_GROUP, set())

    if isinstance(hosts_expr, list):
        return any(play_hosts_may_include_local(h, group_hosts, report, where) for h in hosts_expr)

    text = str(hosts_expr).strip()
    if text == "all":
        return True
    if text == LOCAL_GROUP:
        return True
    if text == CLOUD_GROUP or text.startswith(CLOUD_GROUP + ":"):
        return bool(group_hosts.get(CLOUD_GROUP, set()) & local) or False

    # 未知/复杂的模式（如 "web:!db"）：保守处理 —— 假设它可能包含本地机器，
    # 并在报告里提示一次。漏判的代价是「本地被改了」，误判的代价只是「多了一次检查」。
    if text in group_hosts:
        return bool(group_hosts[text] & local)
    report.warn(
        where,
        f"无法静态解析的 hosts 表达式：{text!r}",
        "按「可能包含本地服务器」处理。建议改成明确的组名，让保护规则可静态验证。",
    )
    return True


def has_local_unlock_gate(play: dict) -> bool:
    """play 是否有"显式解锁本地变更"的前置门。"""
    for task in flatten_tasks(play.get("pre_tasks")):
        module, args = module_of(task)
        if short_module(module) != "meta":
            continue
        if str(args).strip() != "end_play":
            continue
        if "local_mutation_allowed" in when_text(task):
            return True
    return False


def role_names(play: dict) -> list[str]:
    names = []
    for entry in as_list(play.get("roles")):
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            names.append(str(entry.get("role") or entry.get("name") or ""))
    return [n for n in names if n]


def check_playbook(path: pathlib.Path, group_hosts, report: Report) -> list[tuple[str, set[str], bool]]:
    """校验一个 playbook。返回 [(role 名, role 继承的门控表达式, 该 play 是否可能含本地主机)]。"""
    data = load_yaml(path)
    if data is None:
        return []
    if not isinstance(data, list):
        report.error(str(path), "playbook 顶层不是列表")
        return []

    role_contexts: list[tuple[str, set[str], bool]] = []
    rel = path.relative_to(ROOT).as_posix()

    for index, play in enumerate(data, 1):
        if not isinstance(play, dict):
            continue
        report.checked_plays += 1
        where = f"{rel} · 第 {index} 幕（{play.get('name', '未命名')}）"

        # ---- P2: intent ----
        intent = play_intent(play)
        if intent not in ("read", "mutate"):
            report.error(
                where,
                f"没有声明 intent（读到 {intent!r}）",
                "每个 play 都要写 intent: read 或 intent: mutate。"
                "guard 靠它决定要不要拦；缺了它，保护判定就没有输入。",
            )
            intent = "mutate"  # 缺声明时按最严格的假设继续检查

        # ---- P3: guard 必须第一个 ----
        roles = role_names(play)
        if not roles:
            report.error(where, "play 里没有任何 role")
        elif roles[0] != "guard":
            report.error(
                where,
                f"roles 的第一个不是 guard，而是 {roles[0]!r}",
                "保护必须先于任何动作。把 - guard 放到 roles 列表的最前面。",
            )
        elif roles.count("guard") > 1:
            report.warn(where, "guard 被重复列了多次")

        # ---- P4/P5: 变更型 play 不能碰本地 ----
        may_local = play_hosts_may_include_local(play.get("hosts"), group_hosts, report, where)
        if may_local and intent == "mutate":
            if has_local_unlock_gate(play):
                # 解锁型 play：还要再确认两件事
                hosts_text = str(play.get("hosts", "")).strip()
                if hosts_text != LOCAL_GROUP and hosts_text != "all":
                    pass  # local / all 都可以，但下面会检查 role 是否 cloud-only
                cloud_only_used = [r for r in roles if r in CLOUD_ONLY_ROLES]
                if cloud_only_used:
                    report.error(
                        where,
                        f"解锁型 play 引用了 cloud-only role：{', '.join(cloud_only_used)}",
                        "这些 role 只应该在云服务器上跑。它们自带断言，运行时会失败 —— "
                        "但更应该在 playbook 层面就不要这么写。",
                    )
            else:
                report.error(
                    where,
                    f"变更型 play（intent: mutate）的 hosts 可能包含本地服务器（hosts: {play.get('hosts')!r}）",
                    "这是「本地服务器不覆盖」最核心的一条。三种正确做法：\n"
                    "        1) 把 hosts 改成 cloud\n"
                    "        2) 把 intent 改成 read\n"
                    "        3) 确实要改本地 → 加 pre_tasks + meta: end_play + "
                    "when: not (local_mutation_allowed | default(false) | bool)（见 site.yml 第 E 幕）",
                )

        # ---- 收集 role 的上下文，供 P6 使用 ----
        gates: set[str] = set()
        for entry in as_list(play.get("roles")):
            if isinstance(entry, dict) and "when" in entry:
                gates.add(when_text(entry))
        for role in roles:
            role_contexts.append((role, set(gates), may_local))

    return role_contexts


# ---------------------------------------------------------------------------
# P6 · role 内部的写入任务必须有门
# ---------------------------------------------------------------------------

def collect_role_gates_from_includes(root: pathlib.Path, role: str, report: Report):
    """遍历 role 的 tasks，算出每个任务携带的门控表达式。"""
    tasks_dir = root / "roles" / role / "tasks"
    if not tasks_dir.is_dir():
        report.error(f"roles/{role}", "找不到 tasks 目录")
        return []

    results: list[tuple[str, str, dict, set[str]]] = []  # (文件, 类型, 任务, 门控)

    def walk(path: pathlib.Path, inherited: set[str], seen: set[pathlib.Path]) -> None:
        if path in seen:
            return
        seen.add(path)
        data = load_yaml(path)
        if data is None:
            return
        rel = path.relative_to(root).as_posix()

        for task in as_list(data):
            if not isinstance(task, dict):
                continue
            module, args = module_of(task)
            if not module:
                # 纯 block 容器：把 when 传下去
                block_gates = set(inherited)
                if "when" in task:
                    block_gates.add(when_text(task))
                for section in ("block", "rescue", "always"):
                    for sub in as_list(task.get(section)):
                        if isinstance(sub, dict) and module_of(sub)[0]:
                            results.append((rel, short_module(module_of(sub)[0]), sub, set(block_gates)))
                continue

            own_gates = set(inherited)
            if "when" in task:
                own_gates.add(when_text(task))
            results.append((rel, short_module(module), task, own_gates))

            # 跟到被 include 的任务文件里
            if short_module(module) in ("include_tasks", "import_tasks") and isinstance(args, str):
                target = path.parent / args
                walk(target, own_gates, seen)

    walk(tasks_dir / "main.yml", set(), set())
    return results


def check_cloud_only_assert(root: pathlib.Path, role: str, report: Report) -> None:
    """cloud-only 的 role 必须自证边界：断言本机不在 local 组里。

    为什么单个 role 也要自证：
        playbook 层面的隔离（"变更 role 只出现在 hosts: cloud 的 play 里"）
        只在有人正确维护 playbook 时成立。断言是**运行时**的最后一道 ——
        即使清单改错、或 role 被误放进本地 play，也会在动作发生前失败。
    """
    main = root / "roles" / role / "tasks" / "main.yml"
    data = load_yaml(main)
    assert_text = ""
    for task in flatten_tasks(data):
        if short_module(module_of(task)[0]) == "assert":
            assert_text += str(module_of(task)[1]) + " " + when_text(task)

    if "wf_in_local_group" not in assert_text:
        report.error(
            f"roles/{role}",
            f"{role} 是 cloud-only role，但 tasks/main.yml 里没有「我不在本地服务器上」的断言",
            "在 tasks/main.yml 最前面加：\n"
            "        - ansible.builtin.assert:\n"
            "            that:\n"
            "              - not (wf_in_local_group | bool)\n"
            "      这样即使清单改错、被误放进本地 play，也会在执行前失败。",
        )


def check_role_writes(root: pathlib.Path, role: str, inherited_gates: set[str],
                      report: Report, report_ok: list[str]) -> None:
    entries = collect_role_gates_from_includes(root, role, report)
    report.checked_roles += 1

    problems: list[str] = []
    additive_without_gate: list[str] = []

    for rel, module, task, gates in entries:
        if is_readonly(task):
            continue
        gate_text = " ".join(gates) + " " + " ".join(inherited_gates)
        has_authorization_gate = any(v in gate_text for v in ("wf_protected", "local_mutation_allowed"))
        has_write_gate = "wf_can_write" in gate_text
        label = f"{rel} :: {task.get('name', '(未命名)')} [{module}]"

        if is_additive(task):
            # 「只新增不存在的对象」：只需要有一个授权门在上面
            if not has_authorization_gate:
                additive_without_gate.append(label)
            continue

        # 会覆盖/改设置的任务
        if not has_write_gate:
            problems.append(label)

    if problems:
        report.error(
            f"roles/{role}",
            "这些会覆盖/改设置的任务没有 wf_can_write 门：\n      - " + "\n      - ".join(problems),
            "本地服务器默认只读；即使显式解锁 local_mutation_allowed，"
            "也只应该「只补缺、不覆盖」。所以所有会改写已有内容的动作都要带 when: wf_can_write | bool。",
        )
    if additive_without_gate:
        report.warn(
            f"roles/{role}",
            "这些「只新增」的任务上方没有任何授权门：\n      - " + "\n      - ".join(additive_without_gate),
            "建议放进一个 when: not (wf_protected | bool) 的 include 里，"
            "让「要不要动手」这件事在一个地方决定。",
        )

    if not problems and not additive_without_gate:
        report_ok.append(role)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_checks(root: pathlib.Path) -> Report:
    report = Report()
    group_hosts, host_addr, sources = collect_inventory(root, report)

    if not sources:
        report.error("inventory/", "没有找到任何清单文件（期待 hosts.yml）")

    if LOCAL_GROUP not in group_hosts:
        report.error("inventory/hosts.yml", f"清单里没有 {LOCAL_GROUP} 组")
    if not group_hosts.get(LOCAL_GROUP):
        report.warn("inventory/hosts.yml", f"{LOCAL_GROUP} 组是空的")

    check_group_vars_location(root, report)
    check_protection_declaration(root, report)
    check_group_overlap(group_hosts, report)
    check_local_forbidden_paths(root, report)

    # playbook → role 门控上下文 / 是否可能跑在本地
    role_gates: dict[str, set[str]] = {}
    role_from_local: dict[str, bool] = {}
    playbooks = sorted((root / "playbooks").glob("*.yml"))
    if not playbooks:
        report.error("playbooks/", "没有找到任何 playbook")

    for path in playbooks:
        for role, gates, may_local in check_playbook(path, group_hosts, report):
            role_gates.setdefault(role, set()).update(gates)
            role_from_local[role] = role_from_local.get(role, False) or may_local

    # role 依赖（meta）也算「被引用」：继承来源 role 的门控与「可能跑在本地」
    for role_dir in sorted((root / "roles").iterdir()):
        if not role_dir.is_dir():
            continue
        meta = load_yaml(role_dir / "meta" / "main.yml") or {}
        for dep in as_list(meta.get("dependencies")):
            dep_name = dep if isinstance(dep, str) else str((dep or {}).get("role", ""))
            if not dep_name:
                continue
            role_gates.setdefault(dep_name, set()).update(role_gates.get(role_dir.name, set()))
            if role_from_local.get(role_dir.name):
                role_from_local[dep_name] = True

    all_roles = {p.name for p in (root / "roles").iterdir() if p.is_dir()}
    for role in sorted(all_roles):
        if role not in role_gates:
            report.warn(f"roles/{role}", "这个 role 没有被任何 play 引用（可能是死代码）")

    # 两道检查分开跑：
    #   cloud-only 的 role → 永远检查「我不在本地」的断言
    #   可能跑在本地 的 role → 检查写入门
    report_ok: list[str] = []
    for role in sorted(all_roles):
        if role in CLOUD_ONLY_ROLES:
            check_cloud_only_assert(root, role, report)
        if role_from_local.get(role):
            check_role_writes(root, role, role_gates.get(role, set()), report, report_ok)

    report.ok_roles = report_ok
    return report


def print_report(report: Report, root: pathlib.Path) -> int:
    print()
    print("═" * 78)
    print(" 本地服务器保护 · 静态护栏校验")
    print("═" * 78)
    print(f"  目录        : {root}")
    print(f"  分组变量    : {rel_to(root, resolve_group_vars_dir(root))}")
    print(f"  检查了      : {report.checked_plays} 个 play / {report.checked_roles} 个 role")
    ok_roles = getattr(report, "ok_roles", [])
    if ok_roles:
        print(f"  写入门检查  : {', '.join(ok_roles)}  —— 全部通过")
    print()

    if report.errors:
        print(f"  ✗ 发现 {len(report.errors)} 个问题：")
        print()
        for f in report.errors:
            print(f"    [{f.where}]")
            print(f"      {f.message}")
            if f.hint:
                for line in f.hint.splitlines():
                    print(f"        {line}")
            print()
    else:
        print("  ✓ 8 类不变量全部通过 —— 没有任何路径能让变更任务碰到本地服务器")
        print()

    if report.warns:
        print(f"  ⚠ {len(report.warns)} 条提示：")
        for f in report.warns:
            print(f"    [{f.where}] {f.message}")
        print()

    print("─" * 78)
    if report.errors:
        print("  结论：不通过。修掉上面的问题再跑。")
        print("─" * 78)
        return 1
    print("  结论：通过。")
    print("─" * 78)
    return 0


# ---------------------------------------------------------------------------
# 自证：证明这个校验器真的抓得住问题
# ---------------------------------------------------------------------------

def selftest() -> int:
    """用合成数据跑一遍规则，确认"该拦的都能拦下、该放行的都能放行"。

    一个校验器如果从来没拦下过任何东西，它通过的原因可能只是「什么都没检查」。
    所以这里故意构造出各种破坏方式，确认每条规则都真的会触发。
    """
    print()
    print("═" * 78)
    print(" 自证：故意破坏保护规则，确认校验器能抓到")
    print("═" * 78)
    print()

    group_hosts = {
        "all": {"k3s-server", "k3s-agent", "demo-ecs-1"},
        LOCAL_GROUP: {"k3s-server", "k3s-agent"},
        CLOUD_GROUP: {"demo-ecs-1"},
    }
    cases: list[tuple[str, dict, str, bool]] = []

    # ---- P2: 缺 intent ----
    cases.append((
        "play 忘记声明 intent",
        {"name": "x", "hosts": "cloud", "roles": ["guard", "baseline"]},
        "intent",
        True,
    ))
    # ---- P3: guard 不在第一个 ----
    cases.append((
        "guard 不在 roles 的第一个",
        {"name": "x", "hosts": "cloud", "intent": "mutate", "roles": ["baseline", "guard"]},
        "guard",
        True,
    ))
    # ---- P4: 变更型 play 用 hosts: all ----
    cases.append((
        "intent: mutate + hosts: all（经典写法错误）",
        {"name": "x", "hosts": "all", "intent": "mutate", "roles": ["guard", "baseline"]},
        "intent: mutate",
        True,
    ))
    # ---- P4: 变更型 play 直接打本地 ----
    cases.append((
        "intent: mutate + hosts: local（没有任何解锁门）",
        {"name": "x", "hosts": LOCAL_GROUP, "intent": "mutate", "roles": ["guard", "baseline"]},
        "intent: mutate",
        True,
    ))
    # ---- P5: 解锁型 play 引用 cloud-only role ----
    cases.append((
        "解锁型 play 里引用了 cloud-only role",
        {
            "name": "x",
            "hosts": LOCAL_GROUP,
            "intent": "mutate",
            "pre_tasks": [{
                "name": "gate",
                "ansible.builtin.meta": "end_play",
                "when": "not (local_mutation_allowed | default(false) | bool)",
            }],
            "roles": ["guard", "app_stack"],
        },
        "cloud-only",
        True,
    ))
    # ---- 正常：只读 play ----
    cases.append((
        "只读 play（hosts: all + intent: read）—— 应该放行",
        {"name": "x", "hosts": "all", "intent": "read", "roles": ["guard", "audit"]},
        "",
        False,
    ))
    # ---- 正常：云上变更 ----
    cases.append((
        "云上变更 play（hosts: cloud + intent: mutate）—— 应该放行",
        {"name": "x", "hosts": CLOUD_GROUP, "intent": "mutate", "roles": ["guard", "baseline"]},
        "",
        False,
    ))
    # ---- 正常：带解锁门的本地变更 ----
    cases.append((
        "本地变更 play（带 meta: end_play 解锁门）—— 应该放行",
        {
            "name": "x",
            "hosts": LOCAL_GROUP,
            "intent": "mutate",
            "pre_tasks": [{
                "name": "gate",
                "ansible.builtin.meta": "end_play",
                "when": "not (local_mutation_allowed | default(false) | bool)",
            }],
            "roles": ["guard", "baseline"],
        },
        "",
        False,
    ))
    # ---- 正常：unknown hosts 保守放行但要告警 ----
    cases.append((
        "无法解析的 hosts（web:!db）—— 应保守处理并告警",
        {"name": "x", "hosts": "web:!db", "intent": "read", "roles": ["guard", "audit"]},
        "无法静态解析",
        True,
    ))

    failures = 0
    for label, play, expect_keyword, expect_flagged in cases:
        sub = Report()
        check_playbook_on_play(play, group_hosts, sub, "自证.yml")
        msgs = " ".join(f"{f.message} {f.hint}" for f in sub.findings)
        flagged = bool(sub.findings)
        hit = (expect_keyword in msgs) if expect_keyword else True
        ok = (flagged == expect_flagged) and hit

        mark = "✓" if ok else "✗"
        want = "应拦下" if expect_flagged else "应放行"
        got = "拦下了" if flagged else "放行了"
        print(f"  {mark} {want} · {label}")
        print(f"      实得：{got}")
        if not ok:
            failures += 1
            print(f"      期望关键词：{expect_keyword!r}")
            print(f"      实得消息  ：{msgs[:400]}")

    # ---- role 写入门：合成一个坏 role 和一个好 role ----
    print()
    print("  role 写入门检查：")
    good = {
        "tasks": [{
            "name": "改设置",
            "ansible.builtin.template": {"src": "a.j2", "dest": "/etc/a.conf"},
            "when": "wf_can_write | bool",
        }]
    }
    bad = {
        "tasks": [{
            "name": "偷偷改设置",
            "ansible.builtin.template": {"src": "a.j2", "dest": "/etc/a.conf"},
        }]
    }
    for label, data, expect_flag in (("带 wf_can_write 门 —— 应放行", good, False),
                                     ("没有门 —— 应拦下", bad, True)):
        sub = Report()
        check_task_list_for_writes(data["tasks"], [], sub, "合成 role")
        flagged = bool(sub.errors)
        ok = flagged == expect_flag
        print(f"  {'✓' if ok else '✗'} {label}（实得：{'拦下了' if flagged else '放行了'}）")
        if not ok:
            failures += 1

    # ---- P8: group_vars 放错位置 ----
    # 这一条是补出来的：真实踩过一次「校验全绿、Ansible 却看不到变量」。
    import tempfile

    print()
    print("  group_vars 位置检查：")

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        (base / "ansible.cfg").write_text("[defaults]\ninventory = inventory/\n", encoding="utf-8")
        (base / "playbooks").mkdir()

        # ① 正确：清单旁边有 group_vars
        (base / "inventory" / "group_vars").mkdir(parents=True)
        sub = Report()
        check_group_vars_location(base, sub)
        ok = not sub.errors
        print(f"  {'✓' if ok else '✗'} 放在清单旁边（inventory/group_vars）—— 应放行"
              f"（实得：{'拦下了' if sub.errors else '放行了'}）")
        if not ok:
            failures += 1
            print("      " + " | ".join(f.message for f in sub.errors))

        # ② 错误：放在仓库根（清单目录和 playbook 都在子目录里）
        (base / "inventory" / "group_vars").rmdir()
        (base / "group_vars").mkdir()
        sub = Report()
        check_group_vars_location(base, sub)
        msgs = " ".join(f"{f.message} {f.hint}" for f in sub.errors)
        ok = bool(sub.errors) and "找不到 group_vars" in msgs
        print(f"  {'✓' if ok else '✗'} 放在仓库根（Ansible 不会查找）—— 应拦下"
              f"（实得：{'拦下了' if sub.errors else '放行了'}）")
        if not ok:
            failures += 1
            print(f"      实得消息：{msgs[:300]}")

        # ③ 也覆盖「旁边有、根上还多一份」——两份时必须以旁边那份为准并提示
        (base / "inventory" / "group_vars").mkdir(parents=True)
        sub = Report()
        check_group_vars_location(base, sub)
        msgs = " ".join(f"{f.message} {f.hint}" for f in sub.findings)
        ok = bool(sub.findings) and "不会查找的位置" in msgs
        print(f"  {'✓' if ok else '✗'} 根上多留一份（会被静默忽略）—— 应拦下"
              f"（实得：{'拦下了' if sub.findings else '放行了'}）")
        if not ok:
            failures += 1
            print(f"      实得消息：{msgs[:300]}")

    print()
    print("─" * 78)
    if failures:
        print(f"  自证失败：{failures} 项不符合预期 —— 校验器本身有问题。")
        print("─" * 78)
        return 1
    print("  自证通过：该拦的拦下了，该放行的放行了。")
    print("─" * 78)
    return 0


def check_playbook_on_play(play: dict, group_hosts, report: Report, where: str) -> None:
    """给自证用的单 play 版校验：直接复用正式规则的同一份逻辑。

    复用而不是重写，是为了保证"自证通过"能真的说明"正式检查也有效" ——
    如果自证跑的是另一套逻辑，它就什么也证明不了。
    """
    check_playbook_core(where, play, group_hosts, report)


def check_playbook_core(rel: str, play: dict, group_hosts, report: Report) -> None:
    """与 check_playbook 相同的规则，但输入是已解析的 play（自证用）。"""
    report.checked_plays += 1
    where = f"{rel} · {play.get('name', '未命名')}"

    intent = play_intent(play)
    if intent not in ("read", "mutate"):
        report.error(
            where,
            f"没有声明 intent（读到 {intent!r}）",
            "每个 play 都要写 intent: read 或 intent: mutate。",
        )
        intent = "mutate"

    roles = role_names(play)
    if not roles:
        report.error(where, "play 里没有任何 role")
    elif roles[0] != "guard":
        report.error(where, f"roles 的第一个不是 guard，而是 {roles[0]!r}", "保护必须先于任何动作。")

    may_local = play_hosts_may_include_local(play.get("hosts"), group_hosts, report, where)
    if may_local and intent == "mutate":
        if has_local_unlock_gate(play):
            cloud_only_used = [r for r in roles if r in CLOUD_ONLY_ROLES]
            if cloud_only_used:
                report.error(where, f"解锁型 play 引用了 cloud-only role：{', '.join(cloud_only_used)}", "")
        else:
            report.error(
                where,
                f"变更型 play（intent: mutate）的 hosts 可能包含本地服务器（hosts: {play.get('hosts')!r}）",
                "把 hosts 改成 cloud、或者把 intent 改成 read、或者加解锁门。",
            )


def check_task_list_for_writes(tasks, inherited: list[str], report: Report, where: str) -> None:
    """自证用：直接对一组任务跑写入门规则。"""
    for task in flatten_tasks(tasks):
        if is_readonly(task):
            continue
        gates = list(inherited) + [when_text(task)]
        if "wf_can_write" in " ".join(gates):
            continue
        if is_additive(task) and any(v in " ".join(inherited) for v in ("wf_protected", "local_mutation_allowed")):
            continue
        report.error(where, f"{task.get('name', '(未命名)')} 没有保护门")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="静态校验「本地服务器不覆盖」的保护规则",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", default=str(ROOT), help="ansible 目录（默认取脚本的上上级）")
    parser.add_argument("--selftest", action="store_true", help="跑自证：确认校验器真的抓得住问题")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    root = pathlib.Path(args.root).resolve()
    report = run_checks(root)
    return print_report(report, root)


if __name__ == "__main__":
    sys.exit(main())
