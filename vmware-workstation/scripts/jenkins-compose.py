#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jenkins compose 栈 —— 核对与接管（在 K3s 主节点上执行）

由 Terraform 通过 SSH 送到远端执行（`python3 -u -`），必须在任何情况下都可安全重跑。

它做两件互相独立的事：
    A. 核对「运行中的容器」是否符合期望        —— docker inspect 比对
    B. 核对「磁盘上的 compose 文件」是否符合期望 —— 语义比对，不是文本比对

两个模式（读 config["mode"]）：
    verify     ★ 默认。只核对、只报告，一行都不改。
    reconcile  只在确实存在差异时才动手：备份 -> 写文件 -> up -d

---------------------------------------------------------------------------
为什么默认只核对、不接管

compose 文件本身就是一份完整的声明式配置。它不只描述镜像和端口，
还记录了几个**特意挂进去的宿主资源**：

    /var/run/docker.sock      -> Jenkins 在宿主机上构建镜像
    /usr/local/bin/k3s        -> Jenkins 直接操作宿主 K3s
    /usr/local/bin/kubectl    -> 部署到 K3s
    /root/.kube               -> 读取 kubeconfig

这些是普通 Jenkins 的 `docker run` 命令里**不会出现**的东西。如果 Terraform
无条件按自己的模板覆写，最可能的事故是抹掉这几个挂载，而现象很有迷惑性：
容器正常起来、Jenkins 界面正常、任务能触发 —— 但每个部署任务都失败，
报「kubectl: command not found」。所以「接管」必须是有意识的动作，不能是默认。

---------------------------------------------------------------------------
两条硬性安全约束（本脚本绝不会违反）

    1. 绝不执行 `docker compose down -v`。
       -v 会删卷。虽然数据是 bind mount（不受 -v 影响），但仍然不用它 ——
       不给任何可能删数据的机会。收敛容器用 up -d，停止用 stop。
    2. 绝不写、绝不删数据目录。只读它的「是否存在 / 占用多大」。

---------------------------------------------------------------------------
关于 compose 文件为什么用「语义比对」

直接 diff 文本时，注释、缩进、字段顺序的差异都会被算成「有差异」，
于是每次 apply 都提示要覆写文件，很吵，用户会干脆关掉这个功能。
`docker compose config` 会渲染成规范化配置，只有真实语义变化才会被报出来。

---------------------------------------------------------------------------
本地调试（不需要真的连远端）

    # 脚本没被 Terraform 渲染时，可以用环境变量直接喂配置：
    WF_CONFIG_JSON='{"container":"jenkins","image":"jenkins/jenkins:2.555.3-lts",
      "http_port":8081,"agent_port":50000,"inner_http_port":8080,
      "inner_agent_port":50000,"home_dir":"/data/jenkins-data",
      "compose_dir":"/data/jenkins-data","restart_policy":"always",
      "mode":"verify","expected_compose":"","host_mounts":[]}' \
      python3 scripts/jenkins-compose.py
"""

from __future__ import annotations

import base64
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

# 远端 locale 可能是 POSIX/C，显式把输出流固定为 UTF-8，
# 否则报告里的中文可能变成乱码或被 ascii 编码器拒绝。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):  # Python < 3.7 或输出被重定向到非常规流
    pass

# ===========================================================================
# 配置载入
#
# Terraform 会把整份配置 base64 编码后替换掉下面的占位符（见 containers.tf）。
# 用 base64 而不是直接嵌 JSON：base64 的字符集只有 [A-Za-z0-9+/=]，
# 不存在引号/反斜杠/换行的转义问题，也不可能意外终止 Python 字符串字面量。
# ===========================================================================
CONFIG_B64 = "__WF_CONFIG_B64__" #配置占位符，用于被Terraform替换为实际配置 ，在containers.tf中定义

TAG = "jenkins"


def die(message: str) -> None:
    print(f"  ✗ {message}", file=sys.stderr)
    raise SystemExit(1)


def load_config() -> dict:
    override = os.environ.get("WF_CONFIG_JSON")
    if override:
        return json.loads(override)
    if CONFIG_B64.startswith("__WF_CONFIG"):
        die(
            "配置占位符未被替换 —— 本脚本要由 Terraform 渲染后执行。\n"
            "     本地调试请改用环境变量：WF_CONFIG_JSON='{...}' python3 <本脚本>"
        )
    return json.loads(base64.b64decode(CONFIG_B64).decode("utf-8"))


def log(message: str) -> None:
    print(f"  [{TAG}] {message}")


def ok(message: str) -> None:
    print(f"    ✓ {message}")


def warn(message: str) -> None:
    print(f"    ⚠ {message}")


def bad(message: str) -> None:
    print(f"    ✗ {message}")


def note(message: str) -> None:
    """缩进一级的补充说明。"""
    print(f"        {message}")


# ===========================================================================
# 核对结果收集
# ===========================================================================
class Audit:
    """收集本次核对发现的问题，区分「容器漂移」与「文件漂移」。

    两个漂移标志决定 reconcile 模式做什么：
        文件漂移 -> 才需要覆写 compose 文件
        任一漂移 -> 需要 up -d 收敛容器状态
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.issues: list[str] = []
        self.container_drift = False
        self.file_drift = False

    def issue(self, message: str, *, container: bool = False, file: bool = False) -> None:
        self.issues.append(message) #issue方法用于添加问题，container和file参数用于判断问题是否是容器漂移或文件漂移
        self.container_drift = self.container_drift or container
        self.file_drift = self.file_drift or file

    @property #装饰器，用于将方法转换为属性，方便调用
    def count(self) -> int:
        return len(self.issues)


# ===========================================================================
# 命令封装
#
# 全部用「参数列表 + shell=False」，避免拼接字符串带来的引号问题。
# 旧实现里那串 `docker inspect --format "{{range \$p,...}}"` 就是在和引号打架。
# ===========================================================================
def run(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess:
    """执行一条命令，把「命令不存在 / 超时」也归一成 CompletedProcess。

    直接让 subprocess 抛 FileNotFoundError 的话，用户看到的是一段 Python
    调用栈，而不是「这台机器没装 docker」——排查成本完全不同。
    """
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: 命令不存在")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"{args[0]}: 执行超时")


def docker(*args: str) -> subprocess.CompletedProcess:
    return run(["docker", *args])


def inspect(fmt: str) -> str:
    """对目标容器执行 docker inspect，返回渲染后的字符串（取第一行的非空结果）。"""
    return docker("inspect", CFG["container"], "--format", fmt).stdout.strip()


def inspect_json(fmt: str, fallback):
    """对目标容器执行 docker inspect 并解析 JSON。

    用 {{json .xxx}} 让 docker 直接吐 JSON，比 Go 模板里手写循环可读得多，
    也顺带解决了「端口映射到底哪个是 key」这类容易搞反的问题。
    """
    raw = inspect(fmt)
    if not raw or raw == "null":
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


CFG = load_config()
CONTAINER: str = CFG["container"]
COMPOSE_FILE: str = f"{CFG['compose_dir']}/docker-compose.yaml" or f"{CFG['compose_dir']}/docker-compose.yml"
INNER_HTTP_PORT: int = CFG.get("inner_http_port", 8080)
INNER_AGENT_PORT: int = CFG.get("inner_agent_port", 50000)

# compose 命令的探测结果，形如 ["docker", "compose"] 或 ["docker-compose"]
COMPOSE: list[str] = []


def host_port_of(inner_port: int) -> str:
    """返回「容器内端口 inner_port」映射到的宿主机端口号。

    ★ 参数是**容器内**端口（8080 / 50000），不是宿主机端口 —— 这点很容易搞反。

      docker inspect 的 .NetworkSettings.Ports 是一个以「容器内端口」为 key
      的映射（形如 "8080/tcp"），宿主机端口是它的 value。所以要比对的 key
      是容器端口，而宿主机端口是我们想要的结果。

      （这个坑很隐蔽：如果两个端口恰好相同，写错了也能跑对，于是 bug 一直
        藏着；一旦两者不同 —— 比如宿主 8081 映射容器 8080 —— 就会永远
        判为不匹配，每次 apply 都触发重建。本函数把参数名写成 inner_port
        就是为了让调用点无法搞反。）
    """
    ports = inspect_json("{{json .NetworkSettings.Ports}}", {}) or {}
    bindings = ports.get(f"{inner_port}/tcp") or []
    if not bindings:
        return ""
    return str(bindings[0].get("HostPort", ""))


def canon_of(path: str) -> str | None:
    """把 compose 文件渲染成规范化配置，用于语义比对。

    固定 -p 名，避免两个文件所在目录不同导致 project name 不同、产生假差异。
    """
    p = run([*COMPOSE, "-p", "jenkins-canon", "-f", path, "config"])
    if p.returncode != 0:
        return None
    return p.stdout


def preflight() -> None:
    """检查环境是否符合要求。"""
    if not shutil.which("docker"):
        die("这台机器上没有 docker 命令，无法管理 compose 栈")

    if run(["docker", "info"]).returncode != 0:
        die("docker 守护进程未运行，或当前用户无权限（需加入 docker 组）")

    global COMPOSE #全局变量，用于存储compose命令的路径
    if run(["docker", "compose", "version"]).returncode == 0:
        COMPOSE = ["docker", "compose"]
    elif shutil.which("docker-compose"):
        COMPOSE = ["docker-compose"]
    else:
        die("找不到 compose 命令（`docker compose` 与 `docker-compose` 都没有）")


# ===========================================================================
# A. 运行中的容器 vs 期望
# ===========================================================================
def check_container(audit: Audit) -> None:
    print()
    print("  --- A. 运行中的容器 ---")

    if docker("inspect", CONTAINER).returncode != 0:
        bad(f"容器「{CONTAINER}」不存在")
        note("两种可能：")
        note(f"  a) 容器名不是 {CONTAINER}（核对 compose 里的 container_name）")
        note(f"  b) 栈当前没在运行（{' '.join(COMPOSE)} -f {COMPOSE_FILE} ps 看一眼）")
        audit.issue(f"容器 {CONTAINER} 不存在", container=True)
        return

    labels = inspect_json("{{json .Config.Labels}}", {}) or {} #获取容器的标签
    project = labels.get("com.docker.compose.project", "") #获取compose项目名
    config_files = labels.get("com.docker.compose.project.config_files", "") #获取compose文件路径

    # --- A1. 是不是 compose 创建的？---
    if not project:
        warn("容器存在，但**不是 compose 创建的**（没有 com.docker.compose.project 标签）")
        note("说明它是被 docker run 直接起的。后果有两个：")
        note("  - 磁盘上那个 compose 文件与它无关，改文件不会影响它")
        note("  - 之后 docker compose up -d 会因为容器名冲突而报错")
        note("建议先确认它是怎么起的，再决定要不要让 compose 接管。")
        audit.issue("容器不是 compose 创建的", container=True)
    else:
        ok(f"由 compose 创建，工程名: {project}")
        if config_files and config_files != COMPOSE_FILE:
            warn("实际使用的 compose 文件与期望不一致：")
            note(f"实际: {config_files}")
            note(f"期望: {COMPOSE_FILE}")
            note("→ 请把 jenkins_compose_dir 改成实际的目录")
            audit.issue("compose 文件路径不一致", container=True)

    # --- A2. 逐项比对 ---
    cur_image = inspect("{{.Config.Image}}")
    cur_policy = inspect("{{.HostConfig.RestartPolicy.Name}}")
    cur_status = inspect("{{.State.Status}}") or "unknown"

    if cur_image == CFG["image"]:
        ok(f"镜像一致: {cur_image}")
    else:
        warn(f"镜像不一致: 实际 {cur_image} / 期望 {CFG['image']}")
        audit.issue("镜像不一致", container=True)

    if cur_policy == CFG["restart_policy"]:
        ok(f"重启策略一致: {cur_policy}")
    else:
        warn(f"重启策略不一致: 实际 {cur_policy} / 期望 {CFG['restart_policy']}")
        audit.issue("重启策略不一致", container=True)

    cur_http = host_port_of(INNER_HTTP_PORT)
    if cur_http == str(CFG["http_port"]):
        ok(f"Web 端口一致: 宿主 {cur_http} -> 容器 {INNER_HTTP_PORT}")
    else:
        warn(f"Web 端口不一致: 实际宿主 {cur_http or '（未映射）'} / 期望 {CFG['http_port']}")
        note("★ 端口不符会直接打断流水线：Harbor / GitLab 的 webhook 都按")
        note("  terraform.tfvars 里配置的地址回调，端口错了就是连接被拒，")
        note("  而且两边都不当致命错误，现象是「流水线毫无反应」。")
        audit.issue("Web 端口不一致", container=True)

    cur_agent = host_port_of(INNER_AGENT_PORT)
    if cur_agent == str(CFG["agent_port"]):
        ok(f"Agent 端口一致: 宿主 {cur_agent} -> 容器 {INNER_AGENT_PORT}")
    else:
        warn(f"Agent 端口不一致: 实际宿主 {cur_agent or '（未映射）'} / 期望 {CFG['agent_port']}")
        audit.issue("Agent 端口不一致", container=True)

    # --- A3. 挂载 ---
    mounts = inspect_json("{{json .Mounts}}", []) or []
    mount_index = {
        f"{m.get('Type')}|{m.get('Source')}|{m.get('Destination')}": m for m in mounts
    }

    expected_data_mount = f"bind|{CFG['home_dir']}|/var/jenkins_home"
    if expected_data_mount in mount_index:
        ok(f"数据目录挂载正确: {CFG['home_dir']} -> /var/jenkins_home（bind mount）")
    else:
        warn("数据目录挂载与期望不符")
        note(f"期望: {CFG['home_dir']} -> /var/jenkins_home")
        note("实际挂载:")
        actual_home = [k for k in mount_index if k.endswith("|/var/jenkins_home")]
        if actual_home:
            for key in actual_home:
                note(f"  {key.replace('|', ': ')}")
        else:
            note("  （没有挂载 /var/jenkins_home！）")
        note("★ 这一项错了，Jenkins 看起来能起来，但配置、插件、任务全不是你的那份。")
        audit.issue("数据目录挂载不符", container=True)

    # 宿主资源挂载：只提示不判定 —— 这些是手工加的，不在变量范围内
    note("宿主资源挂载（提示用，不参与判定）:")
    destinations = {m.get("Destination", "") for m in mounts}
    for path in CFG.get("host_mounts", []):
        mark = "✓" if path in destinations else "-"
        suffix = "" if path in destinations else "（未挂载）"
        note(f"  {mark} {path}{suffix}")

    if cur_status != "running":
        warn(f"容器当前状态是「{cur_status}」，不是 running")
        audit.issue(f"容器状态为 {cur_status}", container=True)


# ===========================================================================
# B. 磁盘上的 compose 文件 vs 期望模板
# ===========================================================================
def check_compose_file(audit: Audit) -> None:
    print()
    print("  --- B. compose 文件（语义比对）---")

    expected = CFG.get("expected_compose", "")
    if not expected.strip():
        warn("没有收到期望的 compose 内容（Terraform 侧未注入），跳过本项比对")
        return

    if not os.path.isfile(COMPOSE_FILE):
        warn(f"compose 文件不存在: {COMPOSE_FILE}")
        note("请确认 jenkins_compose_dir 是否正确。")
        note("也可以直接问容器它自己用的哪个文件：")
        note(f"  docker inspect {CONTAINER} --format '{{{{index .Config.Labels \"com.docker.compose.project.config_files\"}}}}'")
        return

    with tempfile.TemporaryDirectory(prefix="wf-jenkins-") as tmp:
        expected_path = os.path.join(tmp, "docker-compose.yaml") or os.path.join(tmp, "docker-compose.yml")
        with open(expected_path, "w", encoding="utf-8") as fh:
            fh.write(expected)

        current_canon = canon_of(COMPOSE_FILE)
        if current_canon is None:
            warn(f"现有 compose 文件解析失败（{' '.join(COMPOSE)} -f <file> config 报错），跳过语义比对")
            note(f"可以手工看下错误：{' '.join(COMPOSE)} -f {COMPOSE_FILE} config")
            return

        expected_canon = canon_of(expected_path) #实例化期望模板的语义
        if expected_canon is None:
            warn("期望模板本身解析失败 —— 这属于本配置的问题，请检查模板文件")
            return

            if current_canon == expected_canon: 
                ok("语义一致，无需改动")
                with open(COMPOSE_FILE, encoding="utf-8") as fh:
                    current_raw = fh.read()
                if current_raw != expected:
                    note("（与模板仅注释/排版不同，语义相同，因此不改动你的文件）")
                return

        warn("存在**语义差异**（下面只列真实配置变化，不含注释/排版）:")
        diff = difflib.unified_diff(
            current_canon.splitlines(),
            expected_canon.splitlines(),
            fromfile="现状",
            tofile="期望",
            lineterm="",
            n=2,
        )
        for i, line in enumerate(diff):
            if i < 3:  # 跳过 ---/+++ 文件头
                continue
            if i > 62:
                note("...（差异过长，已截断）")
                break
            note(line)
        audit.issue("compose 文件语义差异", file=True)


# ===========================================================================
# C. 处置
# ===========================================================================
def overwrite_compose_file() -> None:
    print()
    print("    --- 覆写 compose 文件 ---")

    if not os.access(COMPOSE_FILE, os.W_OK):
        die(
            f"没有写权限: {COMPOSE_FILE}\n"
            "      该文件属主可能是 root（你的提示符显示是 root 登录的），而 Terraform\n"
            "      用的是 ssh 用户。三个选择：\n"
            f"        a) 把 k3s_server.user 改成 root\n"
            f"        b) 在远端执行: sudo chown $(id -un) {COMPOSE_FILE}\n"
            "        c) 保持 jenkins_manage_compose = false，只用核对模式（推荐）"
        )
    if not os.access(CFG["compose_dir"], os.W_OK):
        die(f"没有目录写权限: {CFG['compose_dir']}，无法创建备份文件")

    # 顺序很重要：先备份成功，再动原文件
    backup = f"{COMPOSE_FILE}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy2(COMPOSE_FILE, backup)
    except OSError as exc:
        die(f"备份失败，已中止（原文件未改动）：{exc}")
    ok(f"原文件已备份 -> {backup}")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml"or ".yml",
                                     prefix="wf-jenkins-", delete=False) as fh:
        fh.write(CFG["expected_compose"])
        staged = fh.name
    try:
        shutil.copyfile(staged, COMPOSE_FILE)
    except OSError as exc:
        die(f"写入失败（原文件仍在，备份可用）：{exc}")
    finally:
        os.unlink(staged)
    ok("已写入新的 compose 文件")


def converge_stack(project: str) -> None:
    print()
    print(f"    --- 收敛栈状态（{' '.join(COMPOSE)} up -d）---")
    note("注意：用 up -d 而非 down -v，容器会按新配置重建，")
    note(f"      数据目录 {CFG['home_dir']} 原样保留（bind mount 不受影响）。")

    args = [*COMPOSE]
    if project:
        args += ["-p", project] #列表追加项目名称
    args += ["-f", COMPOSE_FILE, "up", "-d"]

    if run(args, timeout=600).returncode != 0:
        die(f"up -d 失败，请手工执行查看详细错误：{' '.join(args)}")

    note("等待容器进入 running 状态...")
    for _ in range(30): #等待 30 秒
        status = inspect("{{.State.Status}}") or "unknown"
        if status == "running":
            ok("容器已运行")
            break
        if status == "exited":
            bad("容器启动后立即退出，最近日志：")
            tail = docker("logs", "--tail", "20", CONTAINER)
            for line in (tail.stdout + tail.stderr).splitlines():
                note(line)
            raise SystemExit(1)
        time.sleep(1)
    else:
        raise SystemExit("    ✗ 等待超时，容器仍未进入 running 状态")

    print()
    note("复核对齐结果：")
    now = host_port_of(INNER_HTTP_PORT)
    if now:
        ok(f"Web 端口已对齐: 宿主 {now} -> 容器 {INNER_HTTP_PORT}")
    else:
        warn(f"Web 端口仍未映射到 {CFG['http_port']}")


def dispose(audit: Audit) -> None: #自定义审计处置函数进行判断
    print()
    print("  --- C. 处置 ---")

    if audit.count == 0:
        print("    ✓ 全部一致，无需任何改动。")
        return

    print(f"    发现 {audit.count} 项差异（见上）。")

    if audit.mode == "verify":
        print()
        print("    当前是【核对模式】（jenkins_manage_compose = false），未做任何改动。")
        print("    两条处置路径，任选其一：")
        print()
        print("      a) 改 terraform.tfvars 让期望值对齐现实")
        print("         —— 如果现实是对的（通常如此），这是推荐做法")
        print()
        print("      b) 设 jenkins_manage_compose = true 让 Terraform 接管 compose 文件")
        print("         —— 覆写前会备份原文件；但注意你会失去手工维护 compose 的自由")
        print()
        print("    什么都不做也完全没问题：这个脚本的存在意义就是让你知道差异在哪。")
        return

    if audit.mode != "reconcile":
        die(f"未知模式: {audit.mode}（应为 verify 或 reconcile）")

    if audit.file_drift:
        overwrite_compose_file()
    if audit.file_drift or audit.container_drift:
        project = (inspect_json("{{json .Config.Labels}}", {}) or {}).get(
            "com.docker.compose.project", ""
        )
        converge_stack(project)


# ===========================================================================
def main() -> None:
    print()
    print("============ Jenkins compose 栈核对 ============")
    print()
    print("  期望（来自 terraform.tfvars）：")
    print(f"    容器名     : {CONTAINER}")
    print(f"    镜像       : {CFG['image']}")
    print(f"    Web 端口   : 宿主 {CFG['http_port']}  ->  容器 {INNER_HTTP_PORT}")
    print(f"    Agent 端口 : 宿主 {CFG['agent_port']}  ->  容器 {INNER_AGENT_PORT}")
    print(f"    数据目录   : {CFG['home_dir']}  ->  /var/jenkins_home")
    print(f"    重启策略   : {CFG['restart_policy']}")
    print(f"    compose    : {COMPOSE_FILE}")
    print(f"    脚本模式   : {CFG['mode']}")

    audit = Audit(CFG["mode"])
    check_container(audit)
    check_compose_file(audit)
    dispose(audit)

    print()
    print(f"  访问方式：http://<主节点IP>:{CFG['http_port']}")
    print(f"  查看栈状态：{' '.join(COMPOSE)} -f {COMPOSE_FILE} ps")
    print("================================================")


if __name__ == "__main__":
    preflight()
    main()
