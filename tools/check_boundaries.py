#!/usr/bin/env python3
"""分层边界的静态检查：Terraform / Ansible / K8s / 胶水脚本，各管各的。

为什么需要这个脚本：
    「Terraform 管资源存在性、Ansible 管机器内部状态、K8s 工作负载交给 K8s 工具、
    胶水脚本不碰 state」—— 这些边界在文档里很容易达成共识，但在**代码里守不住**。

    它们失效的方式全都是渐进式的：某次为了赶进度在 local-exec 里 apt-get install，
    某次在 ansible 里加一句 terraform apply 兜底，某次把 Deployment 写成了
    kubernetes_deployment。每一次单独看都「就这一次」，积累起来就是
    「两个工具抢同一个对象」—— 那种局面下没人说得清当前状态以谁为准。

    所以边界要像保护规则一样做成**可执行检查**：不连机器、不装第三方依赖、纯静态。

它校验 9 条边界（每条对应一种真实的越界方式）：

    B1  Terraform / 胶水脚本不得触发 Ansible
        —— 拿 Ansible 当 Terraform 的触发器会破坏前者的状态权威性：
           apply 报成功，但后面那半截 Ansible 失败了，state 里记的却是「成功」。

    B2  Ansible 不得变更 Terraform 状态
        —— 只允许只读地读 output（output / show / plan / validate / fmt / version）。
           apply / destroy / import / taint / state rm 一律越界。

    B3  K8s 工作负载不进 Terraform
        —— Deployment / Service / Ingress / StatefulSet / Helm release 这类用
           K8s YAML、Helm 或 ArgoCD 管。Terraform 管集群本身是可以的，
           但「应用发布」不该走它 —— 发布节奏和基础设施节奏是两件事。

    B4  state 必须在远程后端 + 开启状态锁
        —— 本地 state 且无锁 = 两个人同时 apply 会互相覆盖，
           产生 state 里没有、却真实存在的「幽灵资源」（永远无法 destroy）。
           没有后端配置时给提示；`--require-remote-state` 把它升级为失败（CI 用）。
           ★ 子模块（被 module 块引用的目录）**不应该**有自己的 backend，
             检查器会跳过它们，避免给出有害建议。

    B5  任何人不得直接读写 *.tfstate 文件
        —— 要读输出用 `terraform output`。把 state 文件当数据库读，
           等于绕开了 Terraform 的锁和版本管理。

    B6  装包 / 改服务 / 改内核参数这类「机器内部状态」归 Ansible
        —— 出现在 Terraform 的 provisioner 里、或胶水脚本的执行分支里，就提示。
           唯一的豁免是云实例的 `user_data`：那是实例启动配置的一部分，
           属于「资源存在性」，而且它只在创建时执行一次。

    B7  变更的唯一触发器是 CI/CD
        —— B1~B6 说的都是「谁不该碰谁」，但没回答「那到底谁能动手」。
           答案必须是流水线，否则「以谁的本地环境为准」这个问题永远没有答案。

           检查四件事：
             · 仓库里存在 CI 流水线定义（否则提示；--require-ci 时判失败）
             · 流水线同时覆盖 Terraform 阶段和 Ansible 阶段（只管一半 = 没管住）
             · Terraform 与 Ansible **不在同一个 job 里串联**
               （在 CI 内部又让前者触发后者，等于把刚拆开的耦合装回去）
             · 变更类动作要么要求人工放行（when: manual），要么显式限定范围
               （`site.yml` 必须带 `--limit`，碰 local 组的必须人工放行）
             · 仓库里没有能一键改环境、绕开流水线的入口脚本（提示）

    B8  跨层衔接必须显式声明
        —— B1/B2 把跨层调用一律禁掉，但现实里总有一两次数据要从一层传到另一层
           （云机器的 IP 只有 Terraform 知道，Ansible 必须拿到它才能连上去）。

           「绝对的禁止」和「无限制的允许」都不可取，正确形态是：
           **默认零条，每一条都写成显式声明**（boundaries.yml），
           并且声明本身要满足三个条件：

             · 方向只有一种：某一层**只读地**读 Terraform 的输出。
               把 to 钉死成 terraform，是因为反方向（调用 Ansible）属于 B1 的地盘，
               用声明去解决 B1 等于把安全阀当开关用。
               方向钉死之后「单向」就是结构性的 —— 环根本构造不出来，
               所以这里**不需要**成环检测：一个永远不会响的检查比没有检查更糟，
               它会让人以为查过了。
             · 只能只读（mode: read）—— 写方向的衔接永不开放
             · 必须写 why —— 写不出理由，说明这条衔接本就不该存在

           另外还检查声明与代码是否一致：
             代码里有跨层调用但没声明 → 失败（漏登记）
             声明了但代码里找不到     → 提示（声明腐烂，或者路径写错了）

    B9  宿主机虚拟化平台不进仓库的运行路径
        —— demo 的组件全部跑在虚拟机**内部**；宿主机只提供虚拟化能力，
           既不该被 IaC 操作，也不该被声明成资源。

           理由不是洁癖，是三条具体的后果：
             · 控制端会被钉死在某台装了 Workstation 的机器上 ——
               不可复现、上不了 CI，等于把整条流水线的前提废弃掉
             · 虚机生命周期脱离 state：半接管会留下「state 里没有、
               却真实存在」的对象，那种东西永远 destroy 不掉
             · Workstation 没有官方 provider，Terraform 根本没有 read 能力 ——
               写进去只能得到虚假的安全感

           检测三类痕迹：
             · 宿主机虚拟化命令行：vmrun / vmware-cmd / vmware-vdiskmanager / vmware-vix
             · 虚拟化 provider 声明与 vsphere_* / vmware_* 资源类型
             · .vmx / .vmdk 这类宿主机虚机的配置与磁盘路径

关于豁免（exemptions）：
    仓库里总会有些**不参与运行**的东西：停用的旧方案、语法参考、教学对照。
    它们不必遵守运行时的边界，因为根本不会被跑起来。

    但「谁不参与运行」不能靠目录名去猜（比如「以 archive 开头就跳过」）——
    那是隐式约定，两个方向都会坏：
      · 改个名字，豁免静默失效，检查器开始对着一堆存档代码报红（全是噪音）
      · 把真在跑的代码塞进一个 archive- 目录，就永远查不到了

    所以豁免必须**登记在册**：boundaries.yml 的 exemptions 段，写清 path 与 why。
    并且它对**所有**检查生效，不做成「可选跳过」的开关 ——
    留一个开关，迟早有某条检查忘了打开它，于是最该被豁免的代码被查得最狠。

判定原则（比规则本身更重要）：
    只看**会被执行**的命令。注释、文档、以及 `echo` / `msg:` 里的提示文本一律不算。
    本项目里有大量「提示用户去装 python3」的 echo，里面就含 `apt-get install`；
    如果连提示都算越界，这个校验器第一天就会被无视，然后被删掉。

    B7 是对流水线文件的**文本级**检查，不是 GitLab CI 的语义解析器 ——
    和本文件对 HCL 的做法一样（B3/B4 也是正则，不是 HCL parser）。
    它会因为「命令被折成两行」这类写法误报，所以定了一条约定：
    **`ansible-playbook` 的调用必须写在一行里**（`--limit` 要和它同行）。
    宁可偶尔误报让人改一下写法，也不要漏报。

用法：
    python3 tools/check_boundaries.py
    python3 tools/check_boundaries.py --require-remote-state   # CI：无远程后端即失败
    python3 tools/check_boundaries.py --require-ci             # CI：无流水线定义即失败
    python3 tools/check_boundaries.py --selftest               # 证明校验器真的会响
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import sys
import tempfile

try:  # Windows 控制台默认可能不是 UTF-8，输出中文会炸
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass


# ===========================================================================
# 规则常量
# ===========================================================================

BOUNDARIES = [
    ("B1", "Terraform / 胶水脚本不触发 Ansible"),
    ("B2", "Ansible 不改变 Terraform 状态"),
    ("B3", "K8s 工作负载不进 Terraform"),
    ("B4", "state 在远程后端且开启锁"),
    ("B5", "不直接读写 tfstate 文件"),
    ("B6", "机器内部状态归 Ansible"),
    ("B7", "变更的唯一触发器是 CI/CD"),
    ("B8", "跨层衔接必须显式声明"),
    ("B9", "宿主机虚拟化平台不进运行路径"),
]

# ---------------------------------------------------------------------------
# B7 用的常量
# ---------------------------------------------------------------------------

# 流水线定义文件的候选位置（相对仓库根）。不存在任何一个 = 没有唯一触发器。
CI_FILE_CANDIDATES = (
    ".gitlab-ci.yml",
    ".gitlab-ci.yaml",
    "Jenkinsfile",
    "ci/Jenkinsfile",
    ".circleci/config.yml",
)

# GitLab CI 里的保留顶层键 —— 它们不是 job，是流水线级配置。
# 把 job 分块时要把它们排除掉，否则会把 variables 块当成一个 job。
GITLAB_RESERVED_TOP_KEYS = {
    "stages", "variables", "default", "include", "workflow", "image",
    "services", "before_script", "after_script", "cache", "types", "pages",
}

# Terraform 的**变更类**命令（只读的 plan/fmt/validate 不在其中）
TF_MUTATION_RE = re.compile(r"\bterraform\s+(?:apply|destroy|import|taint|untaint)\b")

# Ansible 的触发命令
ANSIBLE_PLAYBOOK_RE = re.compile(r"\bansible-playbook\b")

# 人工放行
MANUAL_RE = re.compile(r"^\s*(?:-\s*)?when:\s*manual\b")

# 全量变更入口（会同时碰到 cloud 和 local 两组）
SITE_PLAYBOOK_RE = re.compile(r"\bplaybooks/site\.yml\b")

# 「这个 job 里出现了解锁开关」—— 解锁开关不该在流水线里被默认带上
UNLOCK_RE = re.compile(r"local_mutation_allowed\s*[=:]\s*['\"]?true")

# 一键入口脚本的文件名特征：这些名字本身就暗示「一个人就能改环境」
BYPASS_SCRIPT_NAME_RE = re.compile(
    r"^(?:deploy|apply|bootstrap|provision|rollout|setup|install|destroy|teardown)"
    r"(?:[-_.].*)?\.(?:sh|bash|ps1|py|cmd|bat)$",
    re.IGNORECASE,
)
BYPASS_SCRIPT_SUFFIXES = {".sh", ".bash", ".ps1", ".cmd", ".bat"}

# ---------------------------------------------------------------------------
# B8 用的常量
# ---------------------------------------------------------------------------

HANDOFF_FILE = "boundaries.yml"
HANDOFF_ALLOWED_KEYS = {"from", "to", "mode", "where", "why"}
HANDOFF_REQUIRED_KEYS = {"from", "to", "mode", "where"}
HANDOFF_ALLOWED_MODES = {"read"}
HANDOFF_ALLOWED_LAYERS = {"terraform", "ansible", "glue"}

# ---------------------------------------------------------------------------
# B9 用的常量 + 豁免清单
# ---------------------------------------------------------------------------

# 这个文件允许出现的顶层段。多出来的键**报错**而不是忽略 —— 见 read_sections。
SECTION_KEYS = ("handoffs", "exemptions")

# 豁免清单的字段
EXEMPT_ALLOWED_KEYS = {"path", "why"}
EXEMPT_REQUIRED_KEYS = {"path"}

# 「声明文件」而不是「运行代码」—— 运行路径类的检查要跳过它们。
# boundaries.yml 里会写出被豁免的目录名（可能含 vsphere 这类词），
# 它不是可执行的东西，不该被当成越界代码。
DECLARATION_FILES = {HANDOFF_FILE}

# 严格 YAML 子集的形状（见 read_sections 的说明）
YAML_TOP_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")
YAML_ITEM_RE = re.compile(r"^ {2}- (.*)$")
YAML_ITEM_KV_RE = re.compile(r"^ {4}([A-Za-z_][\w-]*):\s*(.*)$")

# K8s 工作负载 —— 用闭集而非「kubernetes_ 开头就算」。
# 理由：kubernetes_namespace / kubernetes_nodes / kubernetes_service_account
# 这类集群周边对象并不是「应用发布」，一刀切会把正常用法也拦掉。
K8S_WORKLOAD_TYPES = {
    # 三个最典型的，也是用户明确点名的
    "kubernetes_deployment", "kubernetes_deployment_v1",
    "kubernetes_service", "kubernetes_service_v1",
    "kubernetes_ingress", "kubernetes_ingress_v1",
    "kubernetes_ingress_class", "kubernetes_ingress_class_v1",
    # 同族的其余工作负载
    "kubernetes_stateful_set", "kubernetes_stateful_set_v1",
    "kubernetes_daemonset", "kubernetes_daemon_set", "kubernetes_daemonsets_v1",
    "kubernetes_replication_controller", "kubernetes_replication_controller_v1",
    "kubernetes_job", "kubernetes_job_v1",
    "kubernetes_cron_job", "kubernetes_cron_job_v1",
    "kubernetes_pod", "kubernetes_pod_v1",
    "kubernetes_horizontal_pod_autoscaler", "kubernetes_horizontal_pod_autoscaler_v1",
    # 把 K8s 当交付通道的第三方 provider
    "helm_release",
    "kubectl_manifest",
}

# terraform 的只读子命令：读 output 是允许的（这正是「胶水脚本」该做的事）
TERRAFORM_RO_SUBCOMMANDS = {
    "output", "show", "plan", "validate", "fmt", "version",
    "providers", "graph", "console", "state",
}
# 其中 state 只有这几个子命令是只读的
TERRAFORM_STATE_RO = {"list", "show", "pull"}

# 「机器内部状态」动作 —— 装包 / 改服务 / 改内核参数 / 改用户
MACHINE_STATE_RE = re.compile(
    r"\b(?:apt-get|apt|yum|dnf|zypper|apk|pacman)\s+(?:-y\s+|--assume-yes\s+)?"
    r"(?:install|remove|purge|upgrade|update)\b"
    r"|\bsystemctl\s+(?:enable|disable|start|stop|restart|reload|mask)\b"
    r"|\bservice\s+\S+\s+(?:start|stop|restart|reload)\b"
    r"|\bsysctl\s+-w\b|\bmodprobe\b"
    r"|\b(?:useradd|usermod|groupadd|passwd|chpasswd)\b"
    r"|\bchkconfig\s+--level\b"
)

# 「这行是给人看的提示文本吗」—— 判定越界与否的关键过滤器
PRINT_VERBS = {
    "echo", "printf", "print", "println", "msg", "debug", "die", "fail",
    "warn", "warning", "error", "log", "logging", "logger", "puts", "say",
    "write-host", "write-output", "console",
}

SKIP_DIR_NAMES = {
    ".terraform", ".git", "__pycache__", ".cache", "node_modules",
    ".venv", "venv", "reports", ".mypy_cache",
}

# 提示文本的引导词。匹配点左侧只要出现过其中之一，就认为这段是「给人看的文字」。
MESSAGE_HEAD_RE = re.compile(
    r"\b(?:echo|printf|print|msg|die|warn(?:ing)?|log(?:ging)?|logger|"
    r"puts|say|console\.[a-z]+|write-host|write-output)\b\s*[\(:]?\s*"
)

# argv 数组的分隔形状：`'a', 'b'`
ARGV_SPLIT_RE = re.compile(r"['\"]\s*,\s*['\"]")

# 本文件自己（扫描时排除，理由见 collect）
SELF_PATH = pathlib.Path(__file__).resolve()

# 执行上下文的起始行
PY_EXEC_RE = re.compile(
    r"\b(?:subprocess\.(?:run|Popen|call|check_call|check_output)"
    r"|os\.system|os\.popen|os\.exec\w*)\b"
)
YML_EXEC_RE = re.compile(
    r"^\s*(?:-\s*)?(?:ansible\.builtin\.|ansible\.legacy\.|ansible\.windows\.)?"
    r"(?:command|shell|raw|script|win_command|win_shell)\s*:"
)
TF_PROVISIONER_RE = re.compile(r'\bprovisioner\s+"[^"]+"')

# ---------------------------------------------------------------------------
# B9 用的正则
# ---------------------------------------------------------------------------

# 宿主机虚拟化平台的命令行工具。闭集 —— 只认这几个名字，
# 不做「名字里含 vm 就算」这种模糊匹配（那会把 vm_names / memSize 全卷进来）。
#
# 两处细节都是踩出来的：
#   · `(?<![\w.])` 排掉 `local.vmrun` / `var.vmrun_path` 这类属性访问 ——
#     shell 里常把 vmrun 存进变量再用，变量名不是调用。
#   · IGNORECASE 是为了接住 `$VMRUN` 这种全大写写法（存进变量后仍要认得出来）。
HOST_VIRT_CLI_RE = re.compile(
    r"(?<![\w.])vmrun(?:\.exe)?\b"
    r"|(?<![\w-])vmware-cmd\b"
    r"|(?<![\w-])vmware-vdiskmanager\b"
    r"|(?<![\w-])vmware-vix\b",
    re.IGNORECASE,
)

# 虚拟化平台的 provider 声明与资源类型 —— 只在 .tf 里按**声明位置**匹配。
# 不裸匹配 "vsphere" 这个词：注释与变量名里出现它都是正常的。
HOST_VIRT_TF_RE = re.compile(
    r'^\s*provider\s+"(?:vsphere|vmware|vmworkstation)"'
    r'|^\s*(?:resource|data)\s+"(?:vsphere|vmware|vmworkstation)_'
    r'|source\s*=\s*"[^"]*/(?:vsphere|vmware|vmworkstation)\b[^"]*"'
)

# 宿主机上虚机的配置文件与虚拟磁盘。代码里出现 .vmx / .vmdk 路径，
# 说明它要操作的是宿主机上的虚机 —— 这正是「宿主机虚拟化平台」的入口。
HOST_VIRT_FILE_RE = re.compile(r"\.(?:vmx|vmdk)\b")


# ===========================================================================
# 报告
# ===========================================================================

class Finding:
    __slots__ = ("rule", "where", "message", "hint")

    def __init__(self, rule: str, where: str, message: str, hint: str = ""):
        self.rule = rule
        self.where = where
        self.message = message
        self.hint = hint


class Report:
    def __init__(self, root: pathlib.Path, require_remote_state: bool = False,
                 require_ci: bool = False):
        self.root = root
        self.require_remote_state = require_remote_state
        self.require_ci = require_ci
        self.fails: list[Finding] = []
        self.warns: list[Finding] = []
        self.infos: list[str] = []
        self.scanned = 0

    def fail(self, rule, where, message, hint=""):
        self.fails.append(Finding(rule, where, message, hint))

    def warn(self, rule, where, message, hint=""):
        self.warns.append(Finding(rule, where, message, hint))

    def info(self, text):
        self.infos.append(text)

    def count(self, rule):
        f = sum(1 for x in self.fails if x.rule == rule)
        w = sum(1 for x in self.warns if x.rule == rule)
        return f, w


# ===========================================================================
# 文本处理工具
# ===========================================================================

def rel(root: pathlib.Path, path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def strip_hash_comment(line: str) -> str:
    """去掉 # 注释，但引号内的 # 保留（`echo "a#b"` 不该被截断）。"""
    out = []
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "#":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def is_message_line(code: str) -> bool:
    """整行是不是「提示文本语句」（echo/print/die/msg: ...）。"""
    s = code.strip()
    if s.startswith("- "):
        s = s[2:].strip()
    m = re.match(r"([A-Za-z_][\w.\-]*)\s*[\(:\s]", s)
    if not m:
        return False
    token = m.group(1).lower()
    if token in PRINT_VERBS:
        return True
    first = token.split(".")[0]
    return first in PRINT_VERBS


def is_inside_message(code: str, pos: int) -> bool:
    """匹配点左边已经出现了 echo/msg: 之类 —— 说明这段文字是给人看的。

    注意这里**不要求**提示词后面紧跟引号：`echo 安装：sudo apt-get install -y python3`
    这种不带引号的写法同样常见（真实代码里就有好几处）。
    """
    head = code[:pos]
    return bool(MESSAGE_HEAD_RE.search(head))


def normalize_argv(code: str) -> str:
    """把 `['systemctl', 'restart', 'docker']` 摊平成 `systemctl restart docker`。

    为什么必须做：按空格分词的命令模式匹配不到 argv 数组形式，
    而 argv 形式恰恰是**更规范**的写法（不经过 shell）。
    不摊平就会出现「shell 字符串写法能查出来、argv 写法查不出来」的漏洞 ——
    漏洞比不查更糟，因为它给人「已经检查过了」的错觉。
    """
    return ARGV_SPLIT_RE.sub(" ", code)


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def exec_context_lines(lines, start_re) -> set:
    """返回「执行上下文」的行号集合。

    某行匹配 start_re 时，它自己 + 之后所有**缩进更深**的行都算——
    这正是 ansible 的 `shell: |` 块、Python 的 `subprocess.run(\n [...])`、
    HCL 的 `provisioner "..." {\n ... }` 三者的共同形状。
    """
    hit = set()
    i, n = 0, len(lines)
    while i < n:
        if start_re.search(strip_hash_comment(lines[i])):
            base = indent_of(lines[i])
            hit.add(i)
            j = i + 1
            while j < n:
                cur = lines[j]
                if not strip_hash_comment(cur).strip():
                    j += 1
                    continue
                if indent_of(cur) <= base:
                    break
                hit.add(j)
                j += 1
            i = j
            continue
        i += 1
    return hit


def py_docstring_lines(lines) -> set:
    """三引号块的行号（文档字符串里写 apt-get install 不算越界）。"""
    inside = set()
    quote = None
    for i, line in enumerate(lines):
        s = line.strip()
        if quote is None:
            m = re.match(r'^[rubfRUBF]{0,2}("""|\'\'\')', s)
            if m:
                q = m.group(1)
                if s[m.end():].count(q) >= 1:
                    inside.add(i)
                    continue
                quote = q
                inside.add(i)
                continue
        else:
            inside.add(i)
            if quote in s:
                quote = None
    return inside


def code_lines(path: pathlib.Path, exec_only=False):
    """yield (lineno_1based, 已剥注释的代码行)。

    - .py：剥行注释、跳过三引号块；exec_only 时只保留执行上下文
    - .yml / .sh / .tf：剥行注释（.sh 视为"每行都是命令"，不做上下文过滤）
    """
    text = read_text(path)
    lines = text.splitlines()
    suffix = path.suffix.lower()

    if suffix == ".py":
        keep = exec_context_lines(lines, PY_EXEC_RE) if exec_only else None
        doc = py_docstring_lines(lines)
        for i, raw in enumerate(lines):
            if i in doc:
                continue
            if keep is not None and i not in keep:
                continue
            code = strip_hash_comment(raw)
            if code.strip():
                yield i + 1, code
        return

    if suffix in (".yml", ".yaml") and exec_only:
        keep = exec_context_lines(lines, YML_EXEC_RE)
        for i in sorted(keep):
            code = strip_hash_comment(lines[i])
            if code.strip():
                yield i + 1, code
        return

    if suffix == ".tf" and exec_only:
        keep = exec_context_lines(lines, TF_PROVISIONER_RE)
        for i in sorted(keep):
            code = strip_hash_comment(lines[i])
            if code.strip():
                yield i + 1, code
        return

    for i, raw in enumerate(lines):
        code = strip_hash_comment(raw)
        if code.strip():
            yield i + 1, code


def collect(root: pathlib.Path, suffixes):
    """仓库里所有该被检查的文件（排除自证样例、缓存目录、豁免路径）。

    ★ 豁免**永远生效**，不做成可选开关。
      豁免清单的语义是「这个路径不参与运行」，那它就不该被任何一条边界检查扫到。
      留一个「要不要跳过豁免」的开关，迟早会有人在某条检查里忘了打开它 ——
      于是最该被豁免的那批代码，恰好被查得最狠。
    """
    out = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        # ★ 排除本文件自己：--selftest 的样例里全是「故意写出来的越界代码」，
        #   不排除的话，扫真实仓库时会把这些样例当成真违规报出来（自我误报）。
        if path.resolve() == SELF_PATH:
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.suffix.lower() not in suffixes:
            continue
        if is_exempt(root, path):
            continue
        out.append(path)
    return out


# ===========================================================================
# B1 · Terraform / 胶水脚本不得触发 Ansible
# ===========================================================================

ANSIBLE_INVOKE_RE = re.compile(
    r"\bansible-(?:playbook|galaxy|vault|pull|doc|console|inventory|config|lint)\b"
    r"|\bansible\s+(?:-|--|all\b)"
)


def ansible_call_files(root: pathlib.Path) -> list[pathlib.Path]:
    """B1/B8 要扫的文件：Terraform 配置 + 胶水脚本。

    ★ 刻意**不包括** CI 流水线文件。流水线是编排者，它同时调用 Terraform 和
      Ansible 是本职；要禁的是「Terraform 或胶水脚本顺手触发 Ansible」。
      （流水线那边由 B7 单独检查：不许把两者串进同一个 job。）
    """
    out: list[pathlib.Path] = []
    out += collect(root, {".tf"})
    out += [p for p in collect(root, {".py", ".sh"})
            if "ansible" not in p.relative_to(root).parts[:1]]
    out += collect(root / "vmware-workstation", {".py", ".sh"})

    deduped: list[pathlib.Path] = []
    seen: set = set()
    for path in out:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def iter_ansible_calls(root: pathlib.Path):
    """yield (path, lineno, code)：Terraform / 胶水脚本对 Ansible 的每一次调用。

    B1 与 B8 共用这一份遍历逻辑。共用是刻意的：两条规则各写一份的话，
    某天有人改了过滤条件，就会出现「B1 说它越界、B8 说没这回事」
    这种自相矛盾的输出 —— 那比只写一条规则还糟。
    """
    for path in ansible_call_files(root):
        exec_only = path.suffix.lower() == ".py"
        for lineno, raw in code_lines(path, exec_only=exec_only):
            code = normalize_argv(raw)
            m = ANSIBLE_INVOKE_RE.search(code)
            if not m:
                continue
            if is_message_line(code) or is_inside_message(code, m.start()):
                continue
            yield path, lineno, code


def check_b1(root: pathlib.Path, rep: Report) -> None:
    for _ in ansible_call_files(root):
        rep.scanned += 1

    for path, lineno, code in iter_ansible_calls(root):
        rep.fail(
            "B1", f"{rel(root, path)}:{lineno}", code.strip()[:110],
            hint=(
                "Terraform 或胶水脚本触发 Ansible，会让「谁对状态负责」变得含糊：\n"
                "apply 报成功，但后面那半截失败了，state 里记的仍是成功。\n"
                "正确做法：Terraform 只负责把机器建出来，Ansible 由人/CI 单独跑，\n"
                "两者之间通过 terraform output 单向传递数据。"
            ),
        )


# ===========================================================================
# B2 · Ansible 不得变更 Terraform 状态
# ===========================================================================

TERRAFORM_TOKEN_RE = re.compile(r"\bterraform\b(.*)$")


def _terraform_subcommand(rest: str):
    """从 terraform 之后的片段里取出子命令与它的下一级词。"""
    tokens = re.findall(r'[A-Za-z][\w\-]*', rest)
    if not tokens:
        return None, None
    return tokens[0].lower(), (tokens[1].lower() if len(tokens) > 1 else None)


def terraform_call_files(root: pathlib.Path) -> list[pathlib.Path]:
    """B2/B8 要扫的文件：Ansible 侧的一切（playbook / group_vars / 工具脚本）。"""
    ansible_dir = root / "ansible"
    if not ansible_dir.is_dir():
        return []
    return collect(ansible_dir, {".yml", ".yaml", ".py"})


def iter_terraform_calls(root: pathlib.Path):
    """yield (path, lineno, code, 子命令, 下一级词)：Ansible 侧对 terraform 的每一次调用。

    B2 用它判断「这次调用越不越界」，B8 用它判断「这次调用登记了没有」。
    和 iter_ansible_calls 一样，共用一份遍历逻辑是为了两条规则口径一致。
    """
    for path in terraform_call_files(root):
        suffix = path.suffix.lower()
        for lineno, raw in code_lines(path, exec_only=(suffix != ".sh")):
            code = normalize_argv(raw)
            if "terraform" not in code:
                continue
            for m in TERRAFORM_TOKEN_RE.finditer(code):
                if is_message_line(code) or is_inside_message(code, m.start()):
                    continue
                sub, sub2 = _terraform_subcommand(m.group(1))
                if sub is None:
                    continue
                yield path, lineno, code, sub, sub2


def check_b2(root: pathlib.Path, rep: Report) -> None:
    if not (root / "ansible").is_dir():
        return

    for _ in terraform_call_files(root):
        rep.scanned += 1

    for path, lineno, code, sub, sub2 in iter_terraform_calls(root):
        where = f"{rel(root, path)}:{lineno}"
        if sub not in TERRAFORM_RO_SUBCOMMANDS:
            rep.fail(
                "B2", where,
                f"terraform {sub}  ← {code.strip()[:96]}",
                hint=(
                    "Ansible 只能只读地读 Terraform 的输出，不能改它的状态。\n"
                    "允许：terraform output / show / plan / validate / fmt / version\n"
                    "禁止：apply / destroy / import / taint / init / state rm …\n"
                    "要建资源请让人或 CI 去跑 Terraform —— "
                    "否则「Ansible 跑过之后资源是谁建的」就说不清了。"
                ),
            )
        elif sub == "state" and sub2 not in TERRAFORM_STATE_RO:
            rep.fail(
                "B2", where,
                f"terraform state {sub2}  ← {code.strip()[:96]}",
                hint=(
                    "`terraform state list/show/pull` 只读，允许；\n"
                    "`state rm/mv/push/replace-provider` 会改状态，属于越界。\n"
                    "确需改 state 请人工执行并留下记录。"
                ),
            )


# ===========================================================================
# B3 · K8s 工作负载不进 Terraform
# ===========================================================================

TF_RESOURCE_RE = re.compile(r'^\s*(resource|data)\s+"([^"]+)"')


def check_b3(root: pathlib.Path, rep: Report) -> None:
    for path in collect(root, {".tf"}):
        rep.scanned += 1
        for lineno, code in code_lines(path):
            m = TF_RESOURCE_RE.match(code)
            if not m:
                continue
            rtype = m.group(2)
            if rtype not in K8S_WORKLOAD_TYPES:
                continue
            rep.fail(
                "B3", f"{rel(root, path)}:{lineno}", f'{m.group(1)} "{rtype}"',
                hint=(
                    "K8s 工作负载（Deployment / Service / Ingress / Helm release…）"
                    "应该交给 K8s 自己的工具：\n"
                    "  · 应用编排      → K8s YAML（放 git，和代码一起 review）\n"
                    "  · 带参数的发布  → Helm\n"
                    "  · 多环境持续交付 → ArgoCD / Flux\n"
                    "Terraform 管集群本身是可以的（它的节奏是「有没有这个集群」），\n"
                    "但应用发布的节奏是「什么时候发这一版」，两者不该捆在一起。"
                ),
            )


# ===========================================================================
# B4 · state 必须在远程后端 + 开启状态锁
# ===========================================================================

BACKEND_RE = re.compile(r'^\s*backend\s+"([^"]+)"\s*\{')

# 自带并发锁的后端（不需要额外配置锁）
LOCKED_BY_DESIGN = {"pg", "consul", "azurerm", "kubernetes", "http"}


def _backend_blocks(path: pathlib.Path):
    """取出文件里的 backend 块：[(类型, 起始行, 块内文本)]。"""
    lines = read_text(path).splitlines()
    blocks = []
    for i, raw in enumerate(lines):
        code = strip_hash_comment(raw)
        m = BACKEND_RE.match(code)
        if not m:
            continue
        depth = code.count("{") - code.count("}")
        body = [code]
        j = i + 1
        while j < len(lines) and depth > 0:
            codej = strip_hash_comment(lines[j])
            body.append(codej)
            depth += codej.count("{") - codej.count("}")
            j += 1
        blocks.append((m.group(1), i + 1, "\n".join(body)))
    return blocks


def terraform_modules(root: pathlib.Path) -> list[pathlib.Path]:
    """在用 Terraform 模块目录。

    ★ 子模块排除：Terraform 明确禁止子模块配置 backend（state 归父模块管），
      检查器要是建议给它加 backend 就是在帮倒忙。

    ★ 豁免目录排除：判据是 boundaries.yml 的 exemptions 段，不是目录名。
      靠名字猜的法子两个方向都会坏 —— 改个名豁免就静默失效；
      真在跑的代码塞进一个 archive- 目录，就永远查不到。
    """
    modules = []
    child_sources = set()

    for path in collect(root, {".tf"}):
        for m in re.finditer(r'source\s*=\s*"((?:\.\.?/)[^"]+)"', read_text(path)):
            src = m.group(1)
            child_sources.add((path.parent / src).resolve())

    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in SKIP_DIR_NAMES:
            continue
        if not any(d.glob("*.tf")):
            continue
        if d.resolve() in child_sources:
            continue
        if is_exempt(root, d):
            continue
        modules.append(d)
    return modules


def check_b4(root: pathlib.Path, rep: Report) -> None:
    modules = terraform_modules(root)
    if not modules:
        return

    for d in modules:
        rep.scanned += 1
        blocks = []
        for tf in sorted(d.glob("*.tf")):
            blocks += _backend_blocks(tf)
        # 同时看 example 模板，用来给出可操作的指引
        has_example = any(d.glob("backend.tf.example")) or any(d.glob("backend.tf.json.example"))

        if not blocks:
            msg = f"{rel(root, d)} 没有配置远程后端（state 在本地）"
            hint = (
                "本地 state 的两个后果：\n"
                "  1. 两个人同时 apply 会互相覆盖 → 产生 state 里没有、"
                "却真实存在的幽灵资源（永远 destroy 不掉）\n"
                "  2. state 里有明文凭证（user_data、密钥、密码），散落在各人电脑上\n"
                "启用方式见 " + ("backend.tf.example" if has_example else "仓库的远程后端模板")
                + "：cp backend.tf.example backend.tf → terraform init -migrate-state\n"
                "单机练手可以忽略；多人协作 / CI 必须配。"
            )
            if rep.require_remote_state:
                rep.fail("B4", rel(root, d), msg, hint=hint)
            else:
                rep.warn("B4", rel(root, d), msg, hint=hint)
            continue

        for btype, lineno, body in blocks:
            where = f"{rel(root, d)}:{lineno}"
            if re.search(r'access_key\s*=|secret_key\s*=|password\s*=', body):
                rep.fail(
                    "B4", where, f'backend "{btype}" 里硬编码了凭证',
                    hint=(
                        "凭证写进 backend 配置会被存进 .terraform/ 与 plan 文件，"
                        "而且必然进版本库。\n"
                        "改用环境变量：ALICLOUD_ACCESS_KEY / ALICLOUD_SECRET_KEY，\n"
                        "或 CI 里用 RAM 角色换临时凭证（ALICLOUD_ASSUME_ROLE_ARN）。"
                    ),
                )
            if btype == "local":
                rep.fail(
                    "B4", where, 'backend "local" —— 等于没有锁',
                    hint="local 后端的 state 就是本机一个文件，没有任何并发保护。",
                )
            elif btype == "oss":
                if not re.search(r"tablestore_table\s*=", body):
                    rep.fail(
                        "B4", where, 'backend "oss" 没有配 tablestore_table —— 没有状态锁',
                        hint=(
                            "OSS 后端默认只存不锁：两个人同时 apply 依然会互相覆盖。\n"
                            "加锁需要两件事：一个 OTS(TableStore) 实例 + 一张锁表，\n"
                            "锁表必须有名为 LockID 的主键、类型 String。\n"
                            "然后：tablestore_endpoint + tablestore_table 两项都配上。"
                        ),
                    )
                elif not re.search(r"tablestore_endpoint\s*=", body):
                    rep.fail(
                        "B4", where, "配了 tablestore_table 但缺 tablestore_endpoint",
                        hint=(
                            "两项要成对出现。若 endpoint 是 VPC 内网地址，"
                            "Terraform 解析不出实例名，\n还要补 tablestore_instance_name。"
                        ),
                    )
            elif btype == "s3":
                has_ddb = re.search(r"dynamodb_table\s*=", body)
                has_lockfile = re.search(r"use_lockfile\s*=\s*true", body)
                if not (has_ddb or has_lockfile):
                    rep.fail(
                        "B4", where, 'backend "s3" 没有锁（缺 dynamodb_table / use_lockfile）',
                        hint=(
                            "Terraform 1.10+ 可用 use_lockfile = true（基于对象存储的文件锁，\n"
                            "不再需要 DynamoDB 表）；更早版本要配 dynamodb_table。"
                        ),
                    )
            elif btype not in LOCKED_BY_DESIGN:
                rep.warn(
                    "B4", where, f'backend "{btype}" —— 锁的情况需要人工确认',
                    hint="不在已知自带锁的清单里（pg / consul / azurerm / kubernetes / http），"
                         "请确认这个后端是否提供了并发保护。",
                )


# ===========================================================================
# B5 · 不得直接读写 tfstate 文件
# ===========================================================================

TFSTATE_RE = re.compile(r"\btfstate\b")


def check_b5(root: pathlib.Path, rep: Report) -> None:
    targets = collect(root, {".py", ".sh"})
    targets += collect(root, {".tf"})

    for path in targets:
        suffix = path.suffix.lower()
        rep.scanned += 1
        if suffix == ".tf":
            # .tf 里只关心 provisioner 正文（backend 块里的 key = "terraform.tfstate" 是正当的）
            iterator = code_lines(path, exec_only=True)
        elif suffix == ".py":
            # ★ 这里**不能**只扫 subprocess 上下文：典型越界是 `open('terraform.tfstate')`，
            #   它根本不经过 subprocess。注释与文档字符串已经在 code_lines 里剥掉了。
            iterator = code_lines(path)
        else:
            iterator = code_lines(path)

        for lineno, raw in iterator:
            code = normalize_argv(raw)
            m = TFSTATE_RE.search(code)
            if not m:
                continue
            if is_message_line(code) or is_inside_message(code, m.start()):
                continue
            rep.fail(
                "B5", f"{rel(root, path)}:{lineno}", code.strip()[:110],
                hint=(
                    "直接读写 state 文件会绕开 Terraform 的锁与版本管理，"
                    "而且格式不是公开契约。\n"
                    "要拿输出用 `terraform output -json`（只读、稳定、有 CLI 保证）；\n"
                    "要改状态用 `terraform state` 子命令并留下记录。"
                ),
            )


# ===========================================================================
# B6 · 装包 / 改服务 / 改内核参数归 Ansible
# ===========================================================================

def check_b6(root: pathlib.Path, rep: Report) -> None:
    # 1) Terraform 的 provisioner 正文（user_data 不在其中，天然豁免）
    for path in collect(root, {".tf"}):
        rep.scanned += 1
        for lineno, raw in code_lines(path, exec_only=True):
            code = normalize_argv(raw)
            m = MACHINE_STATE_RE.search(code)
            if not m:
                continue
            if is_message_line(code) or is_inside_message(code, m.start()):
                continue
            rep.warn(
                "B6", f"{rel(root, path)}:{lineno}", code.strip()[:110],
                hint=(
                    "「装包 / 启停服务 / 改内核参数」是机器内部状态，按分工归 Ansible。\n"
                    "放在 Terraform 的 provisioner 里，就等于绕开了那三层保护，"
                    "而且重跑/失败重试的语义很难说清。\n"
                    "（云实例的 user_data 是例外：那是实例启动配置的一部分，只在创建时跑一次。）"
                ),
            )

    # 2) 胶水脚本的执行分支
    targets = [p for p in collect(root, {".py", ".sh"})
               if "ansible" not in p.relative_to(root).parts[:1]]
    for path in targets:
        rep.scanned += 1
        for lineno, raw in code_lines(path, exec_only=(path.suffix.lower() == ".py")):
            code = normalize_argv(raw)
            m = MACHINE_STATE_RE.search(code)
            if not m:
                continue
            if is_message_line(code) or is_inside_message(code, m.start()):
                continue
            rep.warn(
                "B6", f"{rel(root, path)}:{lineno}", code.strip()[:110],
                hint=(
                    "胶水脚本适合做：生成 tfvars、解析 output、调 API、做健康检查。\n"
                    "装包 / 改服务属于「机器内部状态」，交给 Ansible 才能有幂等与保护。"
                ),
            )


# ===========================================================================
# B7 · 变更的唯一触发器是 CI/CD
# ===========================================================================

def find_ci_files(root: pathlib.Path) -> list[pathlib.Path]:
    """找出仓库里的流水线定义文件。"""
    found = []
    for name in CI_FILE_CANDIDATES:
        path = root / name
        if path.is_file():
            found.append(path)
    wf_dir = root / ".github" / "workflows"
    if wf_dir.is_dir():
        for path in sorted(wf_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in (".yml", ".yaml"):
                found.append(path)
    return found


def _top_level_key(line: str):
    """顶格行 → 顶层键名；不是顶格行 → None。

    取「后面跟着行尾或空白的那个冒号」作为分隔，这样 `validate:boundaries:`
    这种自带冒号的 job 名也能整体切出来，而不会被切成两截。
    """
    if not line or line[0].isspace():
        return None
    for m in re.finditer(r":(?=\s|$)", line):
        name = line[:m.start()].strip()
        return name or None
    return None


def split_jobs(text: str) -> list[tuple[str, list[str]]]:
    """把流水线文件切成 [(job 名, 该 job 的正文行)]。

    只认 GitLab CI 的形状：job 是顶格的键，保留字除外。
    换成 Jenkinsfile / GitHub Actions 时切不出 job（那两者本来也不是这个结构），
    于是退化成「整个文件当一个 job」—— 只做跨阶段的检查，不做 job 内串联的检查。
    宁可不查，也不要乱查：把正常写法误报成越界，会让人直接不信这个工具。
    """
    jobs: list[tuple[str, list[str]]] = []
    cur_name = None
    cur_body: list[str] = []

    for raw in text.splitlines():
        code = strip_hash_comment(raw)
        if not code.strip():
            if cur_name is not None:
                cur_body.append("")
            continue
        key = _top_level_key(code)
        if key is not None:
            if cur_name is not None:
                jobs.append((cur_name, cur_body))
                cur_body = []
            if key in GITLAB_RESERVED_TOP_KEYS or key.startswith("."):
                cur_name = None
                continue
            cur_name = key
            cur_body = [code]
            continue
        if cur_name is not None:
            cur_body.append(code)

    if cur_name is not None:
        jobs.append((cur_name, cur_body))
    return jobs


def check_bypass_scripts(root: pathlib.Path, rep: Report) -> None:
    """找「一个人就能改环境」的入口脚本。

    这条只能提示，不能判失败 —— 项目早期本来就得有人在本地跑。
    但它是「CI 是唯一触发器」这句话最容易被架空的路径：
    只要有一个 deploy.sh，流水线就随时可以被绕过，而且没人会知道。
    """
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.resolve() == SELF_PATH:
            continue
        if is_exempt(root, path):
            continue
        suffix = path.suffix.lower()
        if suffix not in BYPASS_SCRIPT_SUFFIXES and not BYPASS_SCRIPT_NAME_RE.match(path.name):
            continue
        # 流水线定义文件本身不算「绕过」
        if path in find_ci_files(root):
            continue

        for lineno, raw in code_lines(path, exec_only=(suffix == ".py")):
            code = normalize_argv(raw)
            m = TF_MUTATION_RE.search(code) or ANSIBLE_PLAYBOOK_RE.search(code)
            if not m:
                continue
            if is_message_line(code) or is_inside_message(code, m.start()):
                continue
            rep.warn(
                "B7", f"{rel(root, path)}:{lineno}", code.strip()[:110],
                hint=(
                    "这是一个能一键改环境的入口脚本 —— 有它，流水线就随时可以被绕过，\n"
                    "而且绕过了没人知道。\n"
                    "如果它只是本地开发/演练用的，请在文件头写清「不要用于生产」；\n"
                    "否则应该把它变成流水线里的一个 job。"
                ),
            )
            break


def check_b7(root: pathlib.Path, rep: Report) -> None:
    ci_files = find_ci_files(root)

    if not ci_files:
        message = (
            "仓库里没有任何 CI/CD 流水线定义。\n"
            "「变更的唯一触发器是 CI/CD」这条边界目前是空的 —— 基础设施变更\n"
            "只能靠人跑 terraform apply / ansible-playbook，于是「以谁的本地环境为准」\n"
            "这个问题永远没有答案。"
        )
        hint = (
            "加一个 .gitlab-ci.yml（或 Jenkinsfile / GitHub Actions workflow），\n"
            "把变更收敛成一条路径：git push → 流水线。\n"
            "结构大致是 validate → plan → apply(manual) → configure(Ansible) → verify，\n"
            "其中 Terraform 与 Ansible 必须是**两个独立 job**，靠 artifact 传数据。"
        )
        if rep.require_ci:
            rep.fail("B7", "（仓库根）", message, hint=hint)
        else:
            rep.warn("B7", "（仓库根）", message, hint=hint)
        return

    saw_tf_mutation = False
    saw_ansible = False

    for path in ci_files:
        rep.scanned += 1
        where = rel(root, path)

        for job_name, body in split_jobs(read_text(path)):
            executed = [ln for ln in body if ln.strip()]
            if not executed:
                continue
            joined = "\n".join(executed)
            job_where = f"{where} → job `{job_name}`"

            has_tf_mutation = bool(TF_MUTATION_RE.search(joined))
            has_ansible = bool(ANSIBLE_PLAYBOOK_RE.search(joined))
            has_manual = any(MANUAL_RE.match(ln) for ln in executed)

            if has_tf_mutation:
                saw_tf_mutation = True
            if has_ansible:
                saw_ansible = True

            # ---- ① 同一个 job 里不许把两者串起来 -------------------------
            if has_tf_mutation and has_ansible:
                rep.fail(
                    "B7", job_where,
                    "同一个 job 里既有 terraform 的变更命令、又有 ansible-playbook",
                    hint=(
                        "拆成两个 job。在 CI 里让 Terraform 触发 Ansible，等于把刚拆开的\n"
                        "耦合原样装回去：apply 那半截成功、同一 job 的后半截失败，\n"
                        "流水线只告诉你「这个 job 是红的」，状态归属又说不清了。\n"
                        "正确做法：Terraform 的 job 产出 artifact（terraform output -json），\n"
                        "Ansible 的 job 消费这个 artifact —— 单向、一次、看得见。"
                    ),
                )

            # ---- ② 变更类动作必须人工放行 -------------------------------
            if has_tf_mutation and not has_manual:
                rep.fail(
                    "B7", job_where,
                    "terraform 的变更命令所在 job 没有 when: manual",
                    hint=(
                        "一次 push 就改了云上资源，等于流水线替你做了决定。\n"
                        "apply / destroy 这类动作要写：\n"
                        "    rules:\n"
                        "      - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'\n"
                        "        when: manual\n"
                        "        allow_failure: false\n"
                        "顺带一提：apply 应该用 plan 阶段产出的那份 plan 文件，\n"
                        "而不是重新算一遍 —— 否则人工点的那一下，批准的不是你看见的那份计划。"
                    ),
                )

            # ---- ③ 全量入口必须显式限定范围 -----------------------------
            for line in executed:
                if not SITE_PLAYBOOK_RE.search(line):
                    continue
                if "--limit" not in line:
                    rep.fail(
                        "B7", f"{job_where}: {line.strip()[:80]}",
                        "ansible-playbook 跑 site.yml 却没有 --limit",
                        hint=(
                            "site.yml 不限定范围时会同时覆盖 cloud 和 local 两组。\n"
                            "本地服务器虽然被 roles/guard 拦着，但流水线里不该依赖\n"
                            "「拦得住」—— 该直接不碰。显式写 --limit cloud 或 --limit local。\n"
                            "（约定：ansible-playbook 的调用写在一行里，本检查是行级的。）"
                        ),
                    )
                    continue

                limit_part = line.split("--limit", 1)[1]
                if "local" not in limit_part:
                    continue
                if not has_manual:
                    rep.fail(
                        "B7", f"{job_where}: {line.strip()[:80]}",
                        "这个 job 会碰本地服务器（--limit 里含 local）却没有 when: manual",
                        hint=(
                            "本地服务器是存量机器，默认完全只读。流水线里要碰它，\n"
                            "必须有一个人明确点那一下（when: manual）。\n"
                            "只给云端机器做基线的话，把 --limit 改成 cloud 就行。"
                        ),
                    )

            # ---- ④ 解锁开关不能悄悄带上 --------------------------------
            if UNLOCK_RE.search(joined) and not has_manual:
                rep.fail(
                    "B7", job_where,
                    "job 里带着 local_mutation_allowed=true，却没有 when: manual",
                    hint=(
                        "解锁开关是第二道门，第一道门是人工放行。两道门都要有：\n"
                        "少了第一道，等于任何人改一行 YAML 就能改存量机器。"
                    ),
                )

    # ---- ⑤ 流水线只覆盖了一半 ------------------------------------------
    if saw_tf_mutation and not saw_ansible:
        rep.warn(
            "B7", "（流水线）",
            "流水线里有 Terraform 变更，但没有任何 ansible-playbook",
            hint=(
                "「唯一触发器」意味着两条路径都要收进流水线。只收了一半的话，\n"
                "另一半还是靠人手跑 —— 那半截同样会出现「以谁的本地环境为准」。"
            ),
        )
    elif saw_ansible and not saw_tf_mutation:
        rep.warn(
            "B7", "（流水线）",
            "流水线里有 ansible-playbook，但没有任何 Terraform 变更",
            hint=(
                "同上：资源生命周期那一半如果还在人手跑，边界就只守了一半。"
            ),
        )

    rep.info(
        f"流水线触发器：{', '.join(rel(root, p) for p in ci_files)}"
        f"（{len(ci_files)} 个定义文件）"
    )

    check_bypass_scripts(root, rep)


# ===========================================================================
# B8 · 跨层衔接必须显式声明
# ===========================================================================

class _ParseError:
    """解析失败的占位符。

    它存在的唯一理由：把「这个字段写错了」和「这个字段压根没写」区分开。
    否则一句 `why: "少了右引号` 会被报两次（引号错 + 没写 why），
    报两次的后果是第二次看起来像另一个问题，反而不好修。
    """

    def __repr__(self) -> str:
        return "<解析失败>"


PARSE_ERROR = _ParseError()


def parse_scalar(raw: str, lineno: int, errors: list):
    """解析一个标量：支持裸值，以及**首尾成对**的引号。

    刻意不支持转义 —— 子集越小越不会猜错。引号里的 `#` 不当注释，
    裸值里的 ` #` 之后当注释丢掉。解析失败时返回 PARSE_ERROR。
    """
    s = raw.strip()
    if not s:
        return ""
    if s[0] in "\"'":
        if len(s) < 2 or not s.endswith(s[0]):
            errors.append((lineno, f"引号没有闭合：{s[:60]}"))
            return PARSE_ERROR
        inner = s[1:-1]
        if s[0] in inner:
            errors.append((lineno, f"引号里不能再出现同样的引号（本子集不支持转义）：{s[:60]}"))
            return PARSE_ERROR
        return inner
    return re.split(r"\s+#", s, maxsplit=1)[0].strip()


def read_sections(path: pathlib.Path):
    """把 boundaries.yml 切成 {段名: [(行号, 原始行)]}，返回 (sections, errors)。

    errors 的每一项是 (行号, 说明, 归属段)。**归属段不是装饰**：
    B8 管 handoffs、B9 管 exemptions，而解析是全文一次做完的 ——
    不给错误标上归属，同一处语法错会被两条规则各报一遍，
    读的人会以为有两个毛病（这个坑在 B8 的单条规则版本里踩过）。
    归属为 None 表示「顶层结构错」，统一交给 B8 报。

    只支持这个文件用到的那一小撮 YAML 子集：

        handoffs:                 ← 顶层键（段名），缩进 0
          - from: ansible         ← 列表项，缩进 2
            to: terraform         ← 字段，缩进 4
            mode: read
            where: xxx.py
            why: "……"

    顶层只认 SECTION_KEYS 里的段名。子集之外的写法一律报错（带行号），**绝不猜**。
    猜错的后果是「声明被静默忽略」—— 那比没有声明更危险，
    因为人会以为它生效了。
    """
    sections: dict[str, list[tuple[int, str]]] = {k: [] for k in SECTION_KEYS}
    errors: list[tuple[int, str, str | None]] = []
    seen: set[str] = set()
    cur: str | None = None

    for lineno, raw in enumerate(read_text(path).splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        if not line[0].isspace():
            top = YAML_TOP_RE.match(line)
            if not top:
                errors.append((
                    lineno,
                    f"顶层只支持 `键:` 的形式，看不懂：{line.strip()[:60]}",
                    None,
                ))
                cur = None
                continue
            key, rest = top.group(1), top.group(2).strip()
            if key not in SECTION_KEYS:
                errors.append((
                    lineno,
                    f"顶层只支持 {' / '.join(SECTION_KEYS)}；多出来的 `{key}:` "
                    "会被静默忽略，所以直接报错。想写说明请用 # 注释。",
                    None,
                ))
                cur = None
                continue
            if key in seen:
                errors.append((lineno, f"`{key}:` 这个段出现了两次", key))
                cur = None
                continue
            seen.add(key)
            cur = key
            if rest and rest != "[]":
                errors.append((lineno, f"{key} 下面只能是列表项，不能直接写值", key))
                cur = None
            continue

        if cur is None:
            errors.append((
                lineno,
                "缩进行出现在任何顶层段之前 —— 顶层少了 `"
                + "` 或 `".join(SECTION_KEYS) + "` 这一行",
                None,
            ))
            continue
        sections[cur].append((lineno, line))

    return sections, errors


def parse_items(rows) -> tuple[list[dict], list[tuple[int, str]]]:
    """把某一「段」的行解析成 [{"字段": 值, "__line__": 行号}]。

    形状固定在两层：列表项缩进 2（`- key: v`），字段缩进 4。其它缩进一律报错 ——
    这个子集是刻意收窄的：宁可让人改一下写法，也不要猜。
    """
    entries: list[dict] = []
    errors: list[tuple[int, str]] = []
    current: dict | None = None

    for lineno, line in rows:
        item = YAML_ITEM_RE.match(line)
        if item:
            if current is not None:
                entries.append(current)
            current = {"__line__": lineno}
            body = item.group(1).strip()
            if body:
                kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", body)
                if not kv:
                    errors.append((lineno, f"看不懂这一行：{body[:60]}"))
                    entries.append(current)
                    current = None
                    continue
                current[kv.group(1)] = parse_scalar(kv.group(2), lineno, errors)
            continue

        kv = YAML_ITEM_KV_RE.match(line)
        if kv:
            if current is None:
                errors.append((
                    lineno, f"这个字段不属于任何列表项：{line.strip()[:60]}",
                ))
                continue
            key = kv.group(1)
            if key in current:
                errors.append((lineno, f"同一个列表项里重复写了 {key}"))
                continue
            current[key] = parse_scalar(kv.group(2), lineno, errors)
            continue

        errors.append((
            lineno,
            f"缩进对不上（本子集只认：顶层键 0、列表项 2、字段 4）：{line.strip()[:60]}",
        ))

    if current is not None:
        entries.append(current)
    return entries, errors


# 解析结果缓存。这个文件在一次运行里会被读很多遍：collect() 每调一次都要查
# 豁免清单，B8 查 handoffs，B9 查 exemptions。按绝对路径缓存，
# 顺带保证同一处语法错只被报一次 —— 否则两条规则会各报一遍同一行。
_SECTIONS_CACHE: dict[str, tuple] = {}


def read_sections_cached(path: pathlib.Path):
    key = str(path.resolve())
    if key not in _SECTIONS_CACHE:
        _SECTIONS_CACHE[key] = read_sections(path)
    return _SECTIONS_CACHE[key]


def read_handoffs(path: pathlib.Path):
    """读取 handoffs 清单，返回 (entries, errors)。

    顶层结构错（归属 None）也划给这里 —— B8 是这份声明清单的守门人。
    """
    sections, errors = read_sections_cached(path)
    entries, item_errors = parse_items(sections["handoffs"])
    own = [(n, m) for n, m, owner in errors if owner in (None, "handoffs")]
    return entries, own + item_errors


def read_exemptions(path: pathlib.Path):
    """读取 exemptions 清单，返回 (entries, errors)。

    只领归属自己的错误：顶层结构错归 B8，段内的归这里（B9）。
    """
    sections, errors = read_sections_cached(path)
    entries, item_errors = parse_items(sections["exemptions"])
    own = [(n, m) for n, m, owner in errors if owner == "exemptions"]
    return entries, own + item_errors


def exempt_paths(root: pathlib.Path) -> tuple[str, ...]:
    """豁免清单里的路径前缀（相对仓库根、正斜杠、不含首尾斜杠）。

    读不到 / 读坏了都返回空：豁免清单自身的问题归 B9 报，
    不该让「查豁免」这件事把别的检查一起带崩。
    """
    path = root / HANDOFF_FILE
    if not path.is_file():
        return ()
    entries, _ = read_exemptions(path)
    out = []
    for entry in entries:
        p = entry.get("path")
        if isinstance(p, str) and p.strip():
            out.append(p.strip().strip("/").replace("\\", "/"))
    return tuple(out)


def is_exempt(root: pathlib.Path, path: pathlib.Path) -> bool:
    """这个路径是否落在豁免清单里。

    ★ 按**路径段**比较，不做字符串前缀匹配：
      `foo/bar-OLD` 不是 `foo/bar` 的子路径（这个坑踩过）。
    """
    relp = rel(root, path).strip("/")
    for prefix in exempt_paths(root):
        if relp == prefix or relp.startswith(prefix + "/"):
            return True
    return False


def check_b8(root: pathlib.Path, rep: Report) -> None:
    handoff_path = root / HANDOFF_FILE
    declarations: dict[tuple[str, str, str], dict] = {}

    if not handoff_path.is_file():
        rep.info(
            f"没有 {HANDOFF_FILE}：按「允许零条跨层衔接」处理（这本来就是默认值）"
        )
    else:
        rep.scanned += 1
        entries, errors = read_handoffs(handoff_path)

        for lineno, message in errors:
            rep.fail(
                "B8", f"{HANDOFF_FILE}:{lineno}", message,
                hint=(
                    "这个文件刻意只支持一小撮 YAML 子集，遇到不认识的写法就报错，\n"
                    "而不是猜 —— 猜错的后果是「声明被静默忽略」，人会以为它生效了。\n"
                    "需要新写法时，请同时改 tools/check_boundaries.py 的 read_handoffs()。"
                ),
            )

        for entry in entries:
            line = entry.get("__line__", 0)
            loc = f"{HANDOFF_FILE}:{line}"

            missing = HANDOFF_REQUIRED_KEYS - set(entry)
            if missing:
                rep.fail(
                    "B8", loc, f"声明缺字段：{', '.join(sorted(missing))}",
                    hint=(
                        "必须写全 from / to / mode / where（外加 why 说明理由）。\n"
                        "少写一个字段的声明等于没写 —— 校验器不知道该拿它核什么。"
                    ),
                )
                continue

            # 解析阶段已经报过错的字段，这里不再重复报一遍
            if any(entry.get(k) is PARSE_ERROR
                   for k in HANDOFF_REQUIRED_KEYS | {"why"}):
                continue

            unknown = set(entry) - HANDOFF_ALLOWED_KEYS - {"__line__"}
            if unknown:
                rep.warn(
                    "B8", loc,
                    f"声明里有不认识的字段会被忽略：{', '.join(sorted(unknown))}",
                )

            src, dst, mode = entry["from"], entry["to"], entry["mode"]
            target = entry["where"]

            if not entry.get("why", "").strip():
                rep.fail(
                    "B8", loc, "没有写 why",
                    hint=(
                        "写不出理由，说明这条衔接本就不该存在。\n"
                        "why 不是给人看的装饰 —— 它是下一次有人要再加一条时，\n"
                        "唯一能拿出来在 review 里讨论的东西。"
                    ),
                )
                continue

            if src not in HANDOFF_ALLOWED_LAYERS:
                rep.fail(
                    "B8", loc,
                    f"from 只认 {sorted(HANDOFF_ALLOWED_LAYERS)}，实得 {src}",
                )
                continue

            if src == dst:
                rep.fail("B8", loc, f"from 和 to 相同（{src}）—— 这不是跨层衔接")
                continue

            # ★ 允许声明的衔接只有一种形状：某一层**只读地**读 Terraform 的输出。
            #
            #   为什么把 to 钉死成 terraform，而不是「任何只读方向都行」：
            #
            #   反方向（任何人读 Ansible / 让 Ansible 被别人调用）会绕开 B1 ——
            #   而用声明去解决 B1，是把安全阀当开关用。B1 禁止 Terraform 触发
            #   Ansible，是因为那会让 apply 的结果不再等于这次变更的结果；
            #   这个性质不是「这次特殊」就能绕过的。
            #
            #   顺带一个好处：把 to 钉死之后，「单向」这条性质就是**结构性**的，
            #   环根本构造不出来，也就不需要再写一个成环检测。
            #   （一个永远不会响的检查比没有检查更糟：它让人以为查过了。）
            if dst != "terraform":
                rep.fail(
                    "B8", loc,
                    f"to 只能是 terraform，实得 {dst}",
                    hint=(
                        "能被声明的跨层衔接只有一种形状：某一层**只读地**读\n"
                        "Terraform 的输出（云机器 IP、连接信息这类）。\n"
                        "反过来调用 Ansible 是不能声明的 —— 那是 B1 的地盘，\n"
                        "而 B1 要守的性质（apply 的结果 = 这次变更的结果）\n"
                        "不是「这次特殊」就能破例的。要串起来请用 CI：\n"
                        "两个独立 job + artifact，而不是让一层调用另一层。"
                    ),
                )
                continue

            if mode not in HANDOFF_ALLOWED_MODES:
                rep.fail(
                    "B8", loc,
                    f"mode: {mode} —— 只有 read 可以被声明",
                    hint=(
                        "写方向的跨层调用没有例外：Ansible 改 Terraform 的 state、\n"
                        "Terraform 直接改机器内部状态，这两种都是「两个工具抢同一个对象」，\n"
                        "一旦发生就没人说得清当前状态以谁为准。\n"
                        "如果确实需要，那不是衔接问题，是分层划错了。"
                    ),
                )
                continue

            key = (src, dst, target)
            if key in declarations:
                rep.warn("B8", loc, f"这条衔接重复声明了：{src} → {dst} @ {target}")
                continue
            declarations[key] = entry

    # ---- 声明与代码是否对得上 -------------------------------------------
    used: set[tuple[str, str, str]] = set()

    for path, lineno, code, _sub, _sub2 in iter_terraform_calls(root):
        where = rel(root, path)
        key = ("ansible", "terraform", where)
        if key in declarations:
            used.add(key)
            continue
        rep.fail(
            "B8", f"{where}:{lineno}",
            f"Ansible 调用了 terraform，但没有声明：{code.strip()[:90]}",
            hint=(
                f"这个调用本身可能是合法的（比如只读 output），但它必须先被登记。\n"
                f"请在 {HANDOFF_FILE} 的 handoffs 里加一条：\n"
                "  - from: ansible\n"
                "    to: terraform\n"
                "    mode: read\n"
                f"    where: {where}\n"
                '    why: "为什么非这么做不可"\n'
                "另外想一想：CI 里根本不需要这条 —— 让 Terraform 的 job 把输出\n"
                "做成 artifact，Ansible 的 job 消费它，跨层调用数就是 0。"
            ),
        )

    for key, entry in declarations.items():
        src, dst, target = key
        if key in used:
            continue
        rep.warn(
            "B8", f"{HANDOFF_FILE}:{entry.get('__line__', 0)}",
            f"声明了但代码里找不到：{src} → {dst} @ {target}",
            hint=(
                "两种可能：代码被删了但声明留着（声明腐烂，下次有人照抄它会以为有先例），\n"
                "或者路径写错了（那这条衔接其实根本没登记上）。两种都要处理。"
            ),
        )


# ===========================================================================
# B9 · 宿主机虚拟化平台不进仓库运行路径
# ===========================================================================

HOST_VIRT_HINT = (
    "宿主机上的虚拟化平台（VMware Workstation / vSphere）不是这个项目的资源。\n"
    "demo 的组件全部跑在虚拟机**内部**；宿主机只提供虚拟化能力，\n"
    "既不该被 IaC 操作，也不该被声明成资源。把它写进来会有三个后果：\n"
    "  1. 控制端被钉死在某台装了 Workstation 的机器上 —— 不可复现、上不了 CI\n"
    "  2. 虚机生命周期脱离 state：半接管会留下「state 里没有、却真实存在」的对象\n"
    "  3. 它没有 provider 级的 read 能力，Terraform 接管不了 —— 只有虚假的安全感\n"
    "如果需要自动化创建虚机，方向是换成有官方 provider 的平台（见仓库里的\n"
    "vmware-vsphere-reference/ 参考写法），或者把「建虚机」留在宿主机上手工做，\n"
    "仓库只从虚机内部开始接管 —— 这也是本项目现在的做法。"
)


def check_exemptions(root: pathlib.Path, rep: Report) -> None:
    """豁免清单自身也要守规矩 —— 这一段是豁免机制的强制力所在。

    没有它，exemptions 就只是一份谁都能往里塞、塞完不用负责的名单，
    那和隐式豁免没有区别，只是把「猜目录名」换成了「猜谁的提交」。
    """
    path = root / HANDOFF_FILE
    if not path.is_file():
        return
    entries, errors = read_exemptions(path)
    rep.scanned += 1

    for lineno, message in errors:
        rep.fail(
            "B9", f"{HANDOFF_FILE}:{lineno}", message,
            hint=(
                "这个文件刻意只支持一小撮 YAML 子集，遇到不认识的写法就报错，\n"
                "而不是猜 —— 猜错的后果是「豁免被静默忽略」，"
                "人会以为它生效了。\n"
                "需要新写法时，请同时改 tools/check_boundaries.py 的 read_sections()。"
            ),
        )

    seen: set[str] = set()
    for entry in entries:
        loc = f"{HANDOFF_FILE}:{entry.get('__line__', 0)}"

        missing = EXEMPT_REQUIRED_KEYS - set(entry)
        if missing:
            rep.fail(
                "B9", loc, f"豁免缺字段：{', '.join(sorted(missing))}",
                hint="必须写 path（豁免哪个路径）和 why（为什么不参与运行）。",
            )
            continue

        # 解析阶段已经报过错的字段，这里不再重复报一遍
        if any(entry.get(k) is PARSE_ERROR
               for k in EXEMPT_REQUIRED_KEYS | {"why"}):
            continue

        unknown = set(entry) - EXEMPT_ALLOWED_KEYS - {"__line__"}
        if unknown:
            rep.warn(
                "B9", loc,
                f"豁免里有不认识的字段会被忽略：{', '.join(sorted(unknown))}",
            )

        if not entry.get("why", "").strip():
            rep.fail(
                "B9", loc, "没有写 why",
                hint=(
                    "豁免的是「不参与运行」，所以要能说清它为什么不参与。\n"
                    "写不出理由，说明它该被删掉，而不是被豁免 ——\n"
                    "一条没有理由的豁免，在 review 里没法被质疑，也就没法被纠正。"
                ),
            )
            continue

        target = str(entry["path"]).strip().strip("/")
        if not target:
            rep.fail("B9", loc, "path 是空的")
            continue
        if target in seen:
            rep.warn("B9", loc, f"重复登记的豁免路径：{target}")
        seen.add(target)

        if not (root / target).exists():
            rep.warn(
                "B9", loc, f"豁免的路径不存在：{target}",
                hint=(
                    "豁免腐烂：目录被删掉或改名了，清单没跟着改。\n"
                    "留着它会让人以为这里还有东西被豁免着 ——"
                    "实际上什么都没豁免，白占了信任。"
                ),
            )


def check_b9(root: pathlib.Path, rep: Report) -> None:
    check_exemptions(root, rep)

    def _is_prose(code: str, pos: int) -> bool:
        """这行是给人看的文字吗？注释已在 code_lines 里剥掉，
        这里拦的是 error_message / echo / msg 这类提示文本。"""
        return is_message_line(code) or is_inside_message(code, pos)

    # 1) Terraform：provider 声明 / 虚拟化资源类型 / .vmx·.vmdk 路径。
    #    这些是**声明**，不在 provisioner 里，所以要整文件扫。
    for path in collect(root, {".tf"}):
        rep.scanned += 1
        reported: set[int] = set()

        for lineno, code in code_lines(path):
            m = HOST_VIRT_TF_RE.search(code) or HOST_VIRT_FILE_RE.search(code)
            if not m or _is_prose(code, m.start()):
                continue
            reported.add(lineno)
            rep.fail("B9", f"{rel(root, path)}:{lineno}", code.strip()[:110],
                     hint=HOST_VIRT_HINT)

        # 2) 命令行的实际调用（vmrun / vmware-cmd / vmware-vdiskmanager）。
        #    ★ 只在**执行上下文**里找：`error_message = "找不到 vmrun.exe"` 是提示文本，
        #      不是调用。把提示也算成越界，这条规则第一天就会被无视，然后被删掉。
        for lineno, code in code_lines(path, exec_only=True):
            if lineno in reported:
                continue
            m = HOST_VIRT_CLI_RE.search(code)
            if not m or _is_prose(code, m.start()):
                continue
            reported.add(lineno)
            rep.fail("B9", f"{rel(root, path)}:{lineno}", code.strip()[:110],
                     hint=HOST_VIRT_HINT)

    # 3) 胶水脚本 / 流水线 / Ansible：shell 每行都是命令；
    #    Python 只看执行分支；YAML 只看 command / shell 这类执行块。
    targets = collect(root, {".py", ".sh", ".ps1"})
    targets += [p for p in collect(root, {".yml", ".yaml"})
                if rel(root, p) not in DECLARATION_FILES]

    for path in targets:
        rep.scanned += 1
        exec_only = path.suffix.lower() in (".py", ".yml", ".yaml")
        for lineno, raw in code_lines(path, exec_only=exec_only):
            code = normalize_argv(raw)
            m = HOST_VIRT_CLI_RE.search(code) or HOST_VIRT_FILE_RE.search(code)
            if not m or _is_prose(code, m.start()):
                continue
            rep.fail("B9", f"{rel(root, path)}:{lineno}", code.strip()[:110],
                     hint=HOST_VIRT_HINT)


# ===========================================================================
# 汇总
# ===========================================================================

CHECKS = [
    ("B1", check_b1),
    ("B2", check_b2),
    ("B3", check_b3),
    ("B4", check_b4),
    ("B5", check_b5),
    ("B6", check_b6),
    ("B7", check_b7),
    ("B8", check_b8),
    ("B9", check_b9),
]


def run_checks(root: pathlib.Path, require_remote_state=False,
               require_ci=False) -> Report:
    rep = Report(root, require_remote_state=require_remote_state,
                 require_ci=require_ci)

    # 先把豁免清单亮出来。豁免是「不参与运行」的登记，不是「网开一面」——
    # 它必须在输出里看得见：否则「某个目录没被报」和「某个目录被漏扫了」
    # 在结果上长得一模一样。
    exempt = exempt_paths(root)
    if exempt:
        rep.info(f"豁免（不参与运行，也不参与任何检查，见 {HANDOFF_FILE}）: "
                 + ", ".join(exempt))

    for _, fn in CHECKS:
        fn(root, rep)
    return rep


def status_of(rep: Report, rule: str) -> str:
    f, w = rep.count(rule)
    if f:
        return f"✗ {f} 个问题"
    if w:
        return f"⚠ {w} 条提示"
    return "✓ 通过"


def print_report(rep: Report) -> int:
    print()
    print("═" * 78)
    print(" 分层边界 · 静态校验")
    print("═" * 78)
    print(f"  目录        : {rep.root}")
    print(f"  检查了      : {rep.scanned} 个文件")
    modes = []
    if rep.require_remote_state:
        modes.append("远程后端为硬要求")
    if rep.require_ci:
        modes.append("流水线为硬要求")
    if modes:
        print(f"  模式        : {'、'.join(modes)}（严格）")
    print()

    print("  边界                                    状态")
    print("  " + "─" * 60)
    for rule, desc in BOUNDARIES:
        print(f"  {rule}  {desc:<34}{status_of(rep, rule)}")
    print()

    for f in rep.fails:
        print(f"  ✗ [{f.rule}] {f.where}")
        print(f"      {f.message}")
        if f.hint:
            for line in f.hint.splitlines():
                print(f"        {line}")
        print()

    for w in rep.warns:
        print(f"  ⚠ [{w.rule}] {w.where}")
        print(f"      {w.message}")
        if w.hint:
            for line in w.hint.splitlines():
                print(f"        {line}")
        print()

    for text in rep.infos:
        print(f"  · {text}")
    if rep.infos:
        print()

    print("─" * 78)
    if rep.fails:
        print(f"  结论：不通过（{len(rep.fails)} 个越界）。修掉再继续。")
        print("─" * 78)
        return 1
    if rep.warns:
        print(f"  结论：通过，但有 {len(rep.warns)} 条提示待确认。")
        print("─" * 78)
        return 0
    print(f"  结论：通过。{len(BOUNDARIES)} 条边界全部守住。")
    print("─" * 78)
    return 0


# ===========================================================================
# 自证：证明这个校验器真的抓得住问题
# ===========================================================================

def selftest() -> int:
    cases = []

    def case(name, files, check, expect_fails, expect_warns, not_mention=None,
             require_ci=False):
        cases.append((name, files, check, expect_fails, expect_warns,
                      not_mention, require_ci))

    # ---- B1 -------------------------------------------------------------
    case(
        "B1 命中：local-exec 里调 ansible-playbook",
        {"main.tf": 'resource "null_resource" "x" {\n'
                    '  provisioner "local-exec" {\n'
                    '    command = "ansible-playbook -i inv site.yml"\n'
                    "  }\n}\n"},
        "B1", 1, 0,
    )
    case(
        "B1 放行：注释里提到 ansible-playbook",
        {"main.tf": "# 这里不要用 ansible-playbook 触发，见 README\n"
                    'resource "null_resource" "x" {}\n'},
        "B1", 0, 0,
    )
    case(
        "B1 放行：提示文本里提到 ansible-playbook",
        {"main.tf": 'resource "null_resource" "x" {\n'
                    '  provisioner "local-exec" {\n'
                    '    command = "echo 下一步请手动运行 ansible-playbook site.yml"\n'
                    "  }\n}\n"},
        "B1", 0, 0,
    )
    case(
        "B1 放行：胶水脚本只读 terraform output（不涉及 ansible）",
        {"tools/g.py": "import subprocess\n\n"
                       "def main():\n"
                       "    proc = subprocess.run(['terraform', 'output', '-json'],\n"
                       "                          capture_output=True, text=True)\n"
                       "    return proc.stdout\n"},
        "B1", 0, 0,
    )

    # ---- B2 -------------------------------------------------------------
    case(
        "B2 放行：只读 terraform output",
        {"ansible/tools/g.py": "import subprocess\n\n"
                               "def reads():\n"
                               "    return subprocess.run(['terraform', 'output', '-json'],\n"
                               "                          capture_output=True, text=True)\n"},
        "B2", 0, 0,
    )
    case(
        "B2 放行：报错提示里提到 terraform apply",
        {"ansible/tools/g.py": "import sys\n\n"
                               "def die(msg):\n"
                               "    print(msg, file=sys.stderr)\n"
                               "    raise SystemExit(1)\n\n"
                               "def f():\n"
                               "    die(\n"
                               '        f"先去跑 terraform apply 把机器建出来\\n"\n'
                               '        f"  cd {0} && terraform apply\\n"\n'
                               "    )\n"},
        "B2", 0, 0,
    )
    case(
        "B2 命中：subprocess 里 terraform apply",
        {"ansible/tools/g.py": "import subprocess\n\n"
                               "def mutate():\n"
                               "    return subprocess.run(['terraform', 'apply', '-auto-approve'],\n"
                               "                          cwd='../aliyun-ecs')\n"},
        "B2", 1, 0,
    )
    case(
        "B2 命中：ansible command 里 terraform destroy",
        {"ansible/playbooks/x.yml": "---\n"
                                    "- hosts: cloud\n"
                                    "  tasks:\n"
                                    '    - name: "兜底销毁"\n'
                                    "      ansible.builtin.command: terraform destroy -auto-approve\n"},
        "B2", 1, 0,
    )
    case(
        "B2 命中：terraform state rm",
        {"ansible/playbooks/x.yml": "---\n"
                                    "- hosts: cloud\n"
                                    "  tasks:\n"
                                    '    - name: "挪走一个资源"\n'
                                    "      ansible.builtin.shell: |\n"
                                    "        cd ../aliyun-ecs\n"
                                    "        terraform state rm alicloud_instance.web\n"},
        "B2", 1, 0,
    )
    case(
        "B2 放行：terraform state list（只读）",
        {"ansible/playbooks/x.yml": "---\n"
                                    "- hosts: cloud\n"
                                    "  tasks:\n"
                                    '    - name: "看看有哪些资源"\n'
                                    "      ansible.builtin.command: terraform state list\n"},
        "B2", 0, 0,
    )

    # ---- B3 -------------------------------------------------------------
    case(
        "B3 命中：helm_release",
        {"m/main.tf": 'resource "helm_release" "app" {\n  name = "app"\n}\n'},
        "B3", 1, 0,
    )
    case(
        "B3 命中：kubernetes_deployment",
        {"m/main.tf": 'resource "kubernetes_deployment" "web" {\n  metadata { name = "web" }\n}\n'},
        "B3", 1, 0,
    )
    case(
        "B3 放行：注释里提到 kubectl / Deployment",
        {"m/main.tf": "# Jenkins 里的 kubectl -> 部署到 K3s，Deployment 由 K8s 管\n"
                      'resource "null_resource" "x" {}\n'},
        "B3", 0, 0,
    )
    case(
        "B3 放行：集群周边对象（namespace / nodes）不算工作负载",
        {"m/main.tf": 'resource "kubernetes_namespace" "ns" {\n  metadata { name = "app" }\n}\n'
                      'data "kubernetes_nodes" "all" {}\n'},
        "B3", 0, 0,
    )

    # ---- B4 -------------------------------------------------------------
    case(
        "B4 命中：backend local",
        {"m/main.tf": 'terraform {\n  backend "local" {}\n}\n'},
        "B4", 1, 0,
    )
    case(
        "B4 命中：oss 后端没配锁表",
        {"m/main.tf": 'terraform {\n  backend "oss" {\n    bucket = "b"\n'
                      '    prefix = "p"\n    key = "k"\n    region = "cn-hangzhou"\n  }\n}\n'},
        "B4", 1, 0,
    )
    case(
        "B4 命中：oss 配了锁表但缺 endpoint",
        {"m/main.tf": 'terraform {\n  backend "oss" {\n    bucket = "b"\n'
                      '    tablestore_table = "lock"\n  }\n}\n'},
        "B4", 1, 0,
    )
    case(
        "B4 命中：backend 里硬编码凭证",
        {"m/main.tf": 'terraform {\n  backend "oss" {\n    bucket = "b"\n'
                      '    tablestore_table = "lock"\n'
                      '    tablestore_endpoint = "https://x.ots.aliyuncs.com"\n'
                      '    access_key = "AKIDxxx"\n    secret_key = "yyy"\n  }\n}\n'},
        "B4", 1, 0,
    )
    case(
        "B4 放行：oss 后端配齐了锁",
        {"m/main.tf": 'terraform {\n  backend "oss" {\n    bucket = "b"\n'
                      '    prefix = "p"\n    key = "k"\n    region = "cn-hangzhou"\n'
                      '    encrypt = true\n'
                      '    tablestore_endpoint = "https://x.ots.aliyuncs.com"\n'
                      '    tablestore_table = "lock"\n  }\n}\n'},
        "B4", 0, 0,
    )
    case(
        "B4 提示：没有 backend 块（默认本地 state）",
        {"m/main.tf": 'resource "null_resource" "x" {}\n'},
        "B4", 0, 1,
    )
    case(
        "B4 放行：子模块不该有自己的 backend（建议会害人）",
        {"parent/main.tf": 'module "child" {\n  source = "./child"\n}\n',
         "parent/child/main.tf": 'variable "x" {}\n'},
        "B4", 0, 1, not_mention="child",
    )

    # ---- B5 -------------------------------------------------------------
    case(
        "B5 命中：脚本直接 open tfstate",
        {"tools/z.py": "import json\n\n"
                       "def load():\n"
                       "    with open('terraform.tfstate') as fh:\n"
                       "        return json.load(fh)\n"},
        "B5", 1, 0,
    )
    case(
        "B5 放行：注释里提到 tfstate",
        {"tools/z.py": "# 不要直接读 terraform.tfstate，用 terraform output\n"
                       "def load():\n    return {}\n"},
        "B5", 0, 0,
    )
    case(
        "B5 放行：走 terraform output 取数据",
        {"tools/z.py": "import subprocess\n\n"
                       "def load():\n"
                       "    return subprocess.run(['terraform', 'output', '-json'],\n"
                       "                          capture_output=True, text=True).stdout\n"},
        "B5", 0, 0,
    )

    # ---- B6 -------------------------------------------------------------
    case(
        "B6 提示：provisioner 里装包",
        {"m/main.tf": 'resource "null_resource" "x" {\n'
                      '  provisioner "remote-exec" {\n'
                      "    inline = [\n"
                      '      "apt-get install -y nginx",\n'
                      '      "systemctl enable --now nginx",\n'
                      "    ]\n  }\n}\n"},
        "B6", 0, 2,
    )
    case(
        "B6 放行：user_data 是实例启动配置（豁免）",
        {"m/main.tf": 'resource "alicloud_instance" "web" {\n'
                      "  user_data = <<-EOT\n"
                      "    #!/bin/sh\n"
                      "    dnf install -y nginx\n"
                      "    systemctl enable --now nginx\n"
                      "  EOT\n}\n"},
        "B6", 0, 0,
    )
    case(
        "B6 放行：提示文本里的安装命令",
        {"m/main.tf": 'resource "null_resource" "x" {\n'
                      '  provisioner "local-exec" {\n'
                      '    command = "echo 远端没有 python3；安装：sudo apt-get install -y python3"\n'
                      "  }\n}\n"},
        "B6", 0, 0,
    )
    case(
        "B6 放行：Python 文档字符串里提到 systemctl",
        {"tools/s.py": '"""这个脚本负责核对，不负责安装。\n\n'
                       "（历史脚本里曾经用 systemctl restart docker，已移交给 Ansible）\n"
                       '"""\n\n'
                       "def main():\n    return 0\n"},
        "B6", 0, 0,
    )
    case(
        "B6 提示：胶水脚本执行分支里改服务",
        {"tools/s.py": "import subprocess\n\n"
                       "def restart():\n"
                       "    subprocess.run(['systemctl', 'restart', 'docker'], check=True)\n"},
        "B6", 0, 1,
    )

    # ---- B7 -------------------------------------------------------------
    #
    # 一份「规范」的流水线，当正例的底座：变更人工放行、范围显式限定、
    # Terraform 与 Ansible 分成两个独立 job。
    # 下面几个反例都是从它**只改一处**推出来的 —— 这样用例名里说的那个改动
    # 就是唯一变量，响了才说明是那条规则在响。
    good_ci = (
        "stages:\n"
        "  - plan\n"
        "  - configure\n"
        "\n"
        "apply:\n"
        "  stage: plan\n"
        "  rules:\n"
        "    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'\n"
        "      when: manual\n"
        "  script:\n"
        "    - cd aliyun-ecs && terraform apply -auto-approve tfplan\n"
        "\n"
        "configure:cloud:\n"
        "  stage: configure\n"
        "  script:\n"
        "    - cd ansible\n"
        "    - ansible-playbook playbooks/site.yml --limit cloud\n"
    )

    case(
        "B7 放行：规范的流水线（人工放行 + 显式限定 + 两个独立 job）",
        {".gitlab-ci.yml": good_ci},
        "B7", 0, 0,
    )
    case(
        "B7 提示：仓库里没有任何流水线定义",
        {"aliyun-ecs/main.tf": 'resource "null_resource" "x" {}\n'},
        "B7", 0, 1,
    )
    case(
        "B7 命中（严格模式）：没有流水线定义，从提示升级为失败",
        {"aliyun-ecs/main.tf": 'resource "null_resource" "x" {}\n'},
        "B7", 1, 0, require_ci=True,
    )
    case(
        "B7 命中：同一个 job 里串起了 terraform 变更和 ansible-playbook",
        {".gitlab-ci.yml":
            "stages:\n"
            "  - deploy\n"
            "\n"
            "deploy:all:\n"
            "  stage: deploy\n"
            "  rules:\n"
            "    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'\n"
            "      when: manual\n"
            "  script:\n"
            "    - cd aliyun-ecs && terraform apply -auto-approve\n"
            "    - cd ansible && ansible-playbook playbooks/site.yml --limit cloud\n"},
        "B7", 1, 0,
    )
    case(
        "B7 命中：terraform 变更所在 job 没有 when: manual",
        # 从规范样例里**只**拿走 manual 那一行
        {".gitlab-ci.yml": good_ci.replace("      when: manual\n", "")},
        "B7", 1, 0,
    )
    case(
        "B7 命中：site.yml 没有带 --limit",
        {".gitlab-ci.yml": good_ci.replace(
            "    - ansible-playbook playbooks/site.yml --limit cloud\n",
            "    - ansible-playbook playbooks/site.yml\n")},
        "B7", 1, 0,
    )
    case(
        "B7 命中：--limit 里含 local 却没有 when: manual",
        {".gitlab-ci.yml": good_ci.replace("--limit cloud", "--limit local")},
        "B7", 1, 0,
    )
    case(
        "B7 命中：本地解锁开关写进了流水线，却没人放行（两道门缺一道）",
        {".gitlab-ci.yml": good_ci.replace(
            "    - ansible-playbook playbooks/site.yml --limit cloud\n",
            "    - ansible-playbook playbooks/site.yml --limit local"
            " -e local_mutation_allowed=true\n")},
        "B7", 2, 0,
    )
    case(
        "B7 提示：仓库里有一个能绕过流水线的一键入口脚本",
        {".gitlab-ci.yml": good_ci,
         "deploy.sh": "#!/bin/bash\n"
                      "set -e\n"
                      "cd aliyun-ecs\n"
                      "terraform apply -auto-approve\n"},
        "B7", 0, 1,
    )

    # ---- B8 -------------------------------------------------------------
    good_handoff = (
        "handoffs:\n"
        "  - from: ansible\n"
        "    to: terraform\n"
        "    mode: read\n"
        "    where: ansible/tools/g.py\n"
        '    why: "云机器的 IP 只有 terraform 知道"\n'
    )
    tf_output_reader = (
        "import subprocess\n"
        "\n"
        "def reads():\n"
        "    return subprocess.run(['terraform', 'output', '-json'],\n"
        "                          capture_output=True, text=True)\n"
    )

    case(
        "B8 放行：声明过、代码里也对得上",
        {"boundaries.yml": good_handoff, "ansible/tools/g.py": tf_output_reader},
        "B8", 0, 0,
    )
    case(
        "B8 命中：合法调用（只读 output）但没在清单里登记",
        {"ansible/tools/g.py": tf_output_reader},
        "B8", 1, 0,
    )
    case(
        "B8 提示：声明了但代码里找不到（声明腐烂）",
        {"boundaries.yml": good_handoff},
        "B8", 0, 1,
    )
    case(
        "B8 命中：mode 写成了 write",
        {"boundaries.yml": good_handoff.replace("mode: read", "mode: write")},
        "B8", 1, 0,
    )
    case(
        "B8 命中：想把 to 写成 ansible（拿声明当 B1 的开关用）",
        {"boundaries.yml": good_handoff.replace("to: terraform", "to: ansible")},
        "B8", 1, 0,
    )
    case(
        "B8 命中：没写 why",
        {"boundaries.yml": good_handoff.replace(
            '    why: "云机器的 IP 只有 terraform 知道"\n', "")},
        "B8", 1, 0,
    )
    case(
        # 顺带验证：解析失败只报一次。
        # 少了 PARSE_ERROR 那层区分的话，这里会叠加一条「没写 why」的假问题 ——
        # 两个错误报同一处，修的人会以为有两个毛病。
        "B8 命中：引号没闭合（子集之外不猜，直接报语法错，且只报一次）",
        {"boundaries.yml": good_handoff.replace(
            '"云机器的 IP 只有 terraform 知道"', '"云机器的 IP 只有 terraform 知道')},
        "B8", 1, 0,
    )
    case(
        "B8 命中：顶层多了一个键（宁可报错，也不静默忽略）",
        {"boundaries.yml": "ci:\n" + good_handoff,
         "ansible/tools/g.py": tf_output_reader},
        "B8", 1, 0,
    )

    # ---- B9 -------------------------------------------------------------
    #
    # 边界：demo 的组件全在虚机里跑，宿主机只提供虚拟化能力。
    # 正例是「SSH 进虚机干活」—— 控制端在哪都行，宿主机不是被管理的对象。
    case(
        "B9 放行：SSH 进虚机里干活（宿主机不参与）",
        {"m/main.tf": 'resource "null_resource" "svc" {\n'
                      '  provisioner "local-exec" {\n'
                      '    command = "ssh -i k root@$HOST \'docker run -d nginx\'"\n'
                      "  }\n}\n"},
        "B9", 0, 0,
    )
    case(
        "B9 放行：文档里讲 vSphere / vmrun（.md 不在运行路径上）",
        {"README.md": "## 以后迁到 vSphere\n\n那种写法用 hashicorp/vsphere，"
                      "vmrun 这套就废了。\n"},
        "B9", 0, 0,
    )
    case(
        "B9 放行：注释里提到 vSphere / vmrun 不算数",
        {"m/main.tf": "# 以后迁到 vSphere 的话，vmrun 这套就该删掉\n"
                      "# 目前虚机由宿主机手工创建，仓库只从虚机内部接管\n"
                      'resource "null_resource" "x" {}\n'},
        "B9", 0, 0,
    )
    case(
        "B9 放行：error_message 里提到 vmrun.exe（提示文本，不是调用）",
        {"m/precheck.tf": 'resource "null_resource" "p" {\n'
                          "  lifecycle {\n"
                          "    precondition {\n"
                          "      condition     = fileexists(var.vmrun_path)\n"
                          '      error_message = "找不到 vmrun.exe：${var.vmrun_path}"\n'
                          "    }\n  }\n}\n"},
        "B9", 0, 0,
    )
    case(
        "B9 放行：vmrun_path 这类变量名不是命令",
        {"m/v.tf": 'variable "vmrun_path" {\n  default = ""\n}\n'
                   "locals {\n  r = var.vmrun_path\n}\n"},
        "B9", 0, 0,
    )
    case(
        "B9 命中：resource 把宿主机虚机声明成资源",
        {"m/main.tf": 'resource "vsphere_virtual_machine" "node" {\n'
                      '  name = "n"\n}\n'},
        "B9", 1, 0,
    )
    case(
        "B9 命中：provider 指向 vsphere",
        {"m/main.tf": 'provider "vsphere" {\n  user = "a"\n}\n'},
        "B9", 1, 0,
    )
    case(
        "B9 命中：provider source 是 hashicorp/vsphere",
        {"m/versions.tf": "terraform {\n  required_providers {\n"
                          "    vsphere = {\n"
                          '      source = "hashicorp/vsphere"\n'
                          "    }\n  }\n}\n"},
        "B9", 1, 0,
    )
    case(
        "B9 命中：模板虚机的 .vmx 路径进了代码",
        {"m/main.tf": 'resource "null_resource" "x" {\n'
                      '  triggers = { tpl = "D:/vm/base.vmx" }\n}\n'},
        "B9", 1, 0,
    )
    case(
        "B9 命中：local-exec 里调 vmrun",
        {"m/main.tf": 'resource "null_resource" "x" {\n'
                      '  provisioner "local-exec" {\n'
                      '    command = "vmrun -T ws start D:/vm/n/n.vmx nogui"\n'
                      "  }\n}\n"},
        "B9", 1, 0,
    )
    case(
        "B9 命中：Python 的 subprocess 里调 vmrun",
        {"tools/k.py": "import subprocess\n\n"
                       "def up():\n"
                       "    subprocess.run(['vmrun', '-T', 'ws', 'list'], check=True)\n"},
        "B9", 1, 0,
    )
    case(
        "B9 命中：Ansible task 里调 vmrun",
        {"ansible/playbooks/x.yml": "---\n"
                                    "- hosts: cloud\n"
                                    "  tasks:\n"
                                    '    - name: "起虚机"\n'
                                    "      ansible.builtin.command: vmrun -T ws list\n"},
        "B9", 1, 0,
    )
    case(
        # 两行都算痕迹：赋值那行写着 vmrun 的绝对路径，调用那行是实际动作。
        # 全大写是为了验证 IGNORECASE —— shell 里存进变量后仍要认得出来。
        "B9 命中：shell 脚本里调 vmrun（全大写变量名也要认得出来）",
        {"tools/up.sh": "#!/bin/bash\n"
                        "VMRUN=/usr/bin/vmrun\n"
                        '"$VMRUN" -T ws start "$VMX" nogui\n'},
        "B9", 2, 0,
    )

    # ---- 豁免机制 -------------------------------------------------------
    #
    # 豁免的意义全在「显式」两个字上。下面这一组正反用例，
    # 钉的就是「靠目录名猜」和「登记在册」的区别。
    exempt_decl = (
        "exemptions:\n"
        "  - path: legacy\n"
        '    why: "停用的旧方案，不参与运行"\n'
    )
    legacy_module = 'resource "vsphere_virtual_machine" "x" {}\n'

    case(
        "B9 放行：登记在册的豁免目录（不参与运行）",
        {"legacy/old.tf": legacy_module, "boundaries.yml": exempt_decl},
        "B9", 0, 0,
    )
    case(
        "B9 命中：一模一样的代码，没登记豁免就要报",
        {"legacy/old.tf": legacy_module},
        "B9", 1, 0,
    )
    case(
        "B9 命中：豁免清单里没写 why",
        {"legacy/old.tf": 'resource "null_resource" "x" {}\n',
         "boundaries.yml": "exemptions:\n  - path: legacy\n"},
        "B9", 1, 0,
    )
    case(
        "B9 提示：豁免的路径根本不存在（豁免腐烂）",
        {"boundaries.yml": 'exemptions:\n  - path: ghost\n    why: "早就删了"\n'},
        "B9", 0, 1,
    )
    case(
        "B9 命中：exemptions 段里出现子集之外的缩进（不猜，直接报）",
        {"boundaries.yml": "exemptions:\n   - path: legacy\n"},
        "B9", 1, 0,
    )
    case(
        # 顶层结构错归属 None → 只由 B8 报。B9 领的是自己段内的错误。
        # 少了这层区分，同一处语法错会被两条规则各报一遍，读的人以为有两个毛病。
        "B9 不重复报账：顶层结构错归 B8，B9 只报自己段内的",
        {"x/keep.txt": "",
         "boundaries.yml": 'ci:\nexemptions:\n  - path: x\n    why: "占位"\n'},
        "B9", 0, 0,
    )

    # ---- 跑起来 ---------------------------------------------------------
    print()
    print("═" * 78)
    print(" 自证：故意构造越界与合规的样例，看校验器认不认得出来")
    print("═" * 78)
    print()

    failed = 0
    for name, files, rule, want_f, want_w, not_mention, require_ci in cases:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="wf-boundary-"))
        try:
            for relname, content in files.items():
                target = tmp / relname
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            fn = dict(CHECKS)[rule]
            rep = Report(tmp, require_ci=require_ci)
            fn(tmp, rep)

            if rule == "B4" and want_f == 0 and want_w == 1 and not_mention:
                # 子模块用例：要求恰好只有父目录被提示
                got = [w for w in rep.warns if w.rule == rule]
                ok = len(got) == 1 and not_mention not in " ".join(
                    f"{w.where} {w.message}" for w in got)
            else:
                gf, gw = rep.count(rule)
                ok = (gf == want_f and gw == want_w)

            if ok:
                detail = f"失败 {want_f} / 提示 {want_w}"
                if require_ci:
                    detail += "（严格模式）"
                if not_mention:
                    detail += f"（且不提 {not_mention}）"
                print(f"  ✓ {name}")
            else:
                gf, gw = rep.count(rule)
                print(f"  ✗ {name}")
                print(f"      期望 失败{want_f} 提示{want_w}，"
                      f"实得 失败{gf} 提示{gw}")
                failed += 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("─" * 78)
    if failed:
        print(f"  自证不通过：{failed} 个用例不符合预期。")
        print("─" * 78)
        return 1
    print(f"  自证通过：{len(cases)} 个用例，该拦的拦下了，该放行的放行了。")
    print("─" * 78)
    return 0


# ===========================================================================
# 入口
# ===========================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="分层边界的静态检查"
                    "（Terraform / Ansible / K8s / 胶水脚本 / CI 触发器）"
    )
    parser.add_argument("--root", default=None, help="仓库根目录（默认自动定位）")
    parser.add_argument("--require-remote-state", action="store_true",
                        help="把「没有远程后端」从提示升级为失败（CI 里用）")
    parser.add_argument("--require-ci", action="store_true",
                        help="把「没有 CI 流水线定义」从提示升级为失败（CI 里用）")
    parser.add_argument("--selftest", action="store_true",
                        help="自证：构造越界样例，确认校验器真的会响")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    root = pathlib.Path(args.root).resolve() if args.root \
        else pathlib.Path(__file__).resolve().parent.parent

    rep = run_checks(root, require_remote_state=args.require_remote_state,
                     require_ci=args.require_ci)
    return print_report(rep)


if __name__ == "__main__":
    raise SystemExit(main())
