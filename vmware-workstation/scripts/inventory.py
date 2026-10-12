#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
只读盘点（在第一阶段使用）

把「机器上现在到底有什么」变成 Terraform 的输出。**全部只读**，
跑多少次都不会改变机器上任何东西。

按 config["section"] 分派四份报告：

    base        两台机器的基础信息（OS、内核、内存、磁盘、Docker/K3s 版本）
    containers  主节点：容器与 compose 栈（最关键的一份）
    k3s         主节点：K3s 集群状态
    gitlab      从节点：GitLab 服务健康（Omnibus 实体服务）

---------------------------------------------------------------------------
这份清单为什么值得放在 Python 里，而不是继续内联在 .tf 的 heredoc 中

旧版是在 HCL heredoc 里写 shell，于是要同时对付两套转义：
    - HCL 模板插值：shell 里每个 ${...} 都要写成 $${...}
    - awk 的字段引用：$2 要写成 \\$2

结果是一段读起来很吃力的东西：

    echo "内存    : $(free -h 2>/dev/null | awk "/^Mem:/{print \\$2}" || echo "n/a")"

Python 版把「取数据」和「排版」分开：先拿到值，再统一渲染，
既不用双写转义，也便于单独改动某一项。

---------------------------------------------------------------------------
本地调试（在任意一台 Linux 上跑，不需要 Terraform）

    WF_CONFIG_JSON='{"section":"base","host_mounts":[]}' python3 scripts/inventory.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

CONFIG_B64 = "__WF_CONFIG_B64__"

DEFAULT_HOST_MOUNTS = [
    "/var/run/docker.sock",
    "/host-bin/k3s",
    "/host-bin/kubectl",
    "/etc/rancher/k3s",
    "/root/.kube",
]

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


"""
加载配置文件。如果环境变量 WF_CONFIG_JSON 为空，
则从配置占位符中解码 JSON 字符串。
"""
def load_config() -> dict:
    override = os.environ.get("WF_CONFIG_JSON")
    if override:
        return json.loads(override)
    if CONFIG_B64.startswith("__WF_CONFIG"):
        print(
            "  ✗ 配置占位符未被替换 —— 本脚本要由 Terraform 渲染后执行。\n"
            "     本地调试请改用环境变量：WF_CONFIG_JSON='{\"section\":\"base\"}' python3 <本脚本>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return json.loads(base64.b64decode(CONFIG_B64).decode("utf-8"))


CFG = load_config()
HOST_MOUNTS: list[str] = CFG.get("host_mounts") or DEFAULT_HOST_MOUNTS


# ===========================================================================
# 命令与排版小工具
# ===========================================================================
def sh(command: str, *, sudo: bool = False, timeout: int = 30) -> str:
    """执行一条只读 shell 命令，返回标准输出（已去首尾空白）。

    失败一律返回空串 —— 盘点场景下「某项取不到」应当继续，不该中断整份报告。
    """
    full = f"sudo {command}" if sudo else command
    try:
        proc = subprocess.run(
            ["/bin/sh", "-c", full], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip()


def sh_json(command: str, *, timeout: int = 30):
    """执行命令并把输出解析成 JSON；失败返回 None。"""
    raw = sh(command, timeout=timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def banner(title: str, width: int = 40) -> None:
    print()
    print(f"{title:=^{width}}")


def head(title: str) -> None:
    print()
    print(f"--- {title} ---")


def field(label: str, value: str, indent: int = 2) -> None:
    print(f"{' ' * indent}{label:<8}: {value}")


def bullet(text: str, indent: int = 4) -> None:
    print(f"{' ' * indent}{text}")


def block(text: str, *, indent: int = 4, fallback: str = "（无输出）",
          limit: int = 0) -> None:
    """逐行缩进输出一段多行文本。比整段 echo 更容易对齐。"""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        bullet(fallback, indent)
        return
    if limit:
        lines = lines[:limit]
    for line in lines:
        bullet(line, indent)


def has(program: str) -> bool:
    return shutil.which(program) is not None


# ===========================================================================
# docker 查询
# ===========================================================================
def docker_containers(all_states: bool = True) -> list[dict]:
    """列出容器。

    用 `docker ps --format '{{json .}}'`（每行一个 JSON 对象）而不是解析表格，
    字段值里有空格或竖线时也不会错位。
    """
    args = ["docker", "ps"]
    if all_states:
        args.append("-a")
    args += ["--format", "{{json .}}"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def docker_inspect(names: list[str]) -> list[dict]:
    """一次 inspect 多个容器 —— 比逐个调用少很多往返。"""
    if not names:
        return []
    try:
        proc = subprocess.run(
            ["docker", "inspect", *names], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout) or []
    except json.JSONDecodeError:
        return []


def render_ports(ports: dict | None) -> str:
    """把 NetworkSettings.Ports 渲染成 '容器端口 -> 宿主端口' 的可读形式。"""
    if not ports:
        return "（无端口映射）"
    parts = []
    for inner, bindings in ports.items():
        hosts = ", ".join(str(b.get("HostPort")) for b in (bindings or []))
        parts.append(f"{inner} -> {hosts or '(未映射)'}")
    return "  ".join(parts)


def render_mounts(mounts: list[dict] | None) -> str:
    if not mounts:
        return "（无挂载）"
    return "  ".join(
        f"{m.get('Type')}:{m.get('Source')} -> {m.get('Destination')}" for m in mounts
    )


def compose_command() -> str:
    """返回可用的 compose 命令（优先 v2 子命令）。"""
    if sh("docker compose version"):
        return "docker compose"
    if shutil.which("docker-compose"):
        return "docker-compose"
    return ""


# ===========================================================================
# section: base —— 基础信息
# ===========================================================================
def section_base() -> None:
    banner("节点基础信息")

    os_pretty = sh('. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"') or sh("uname -s")
    field("主机名", sh("hostname") or "n/a") #field函数用于打印主机名
    field("系统", os_pretty or "n/a")
    field("内核", sh("uname -r") or "n/a")
    field("CPU", f"{sh('nproc') or '?'} 核")
    field("内存", sh("free -h 2>/dev/null | awk '/^Mem:/{print $2}'") or "n/a")
    field("磁盘 /", sh("df -h / | awk 'NR==2{print $2\" 总 / \"$4\" 可用\"}'") or "n/a")

    if has("docker"):
        field("Docker", sh('docker --version 2>/dev/null') or "已安装（版本读取失败）")
    else:
        field("Docker", "未安装")

    if has("k3s") or has("kubectl"):
        field("K3s", sh("k3s --version 2>/dev/null | head -1") or "已安装")
    else:
        field("K3s", "未检测到")


# ===========================================================================
# section: containers —— 主节点的容器与 compose 栈（最关键的一份）
# ===========================================================================
def print_container_detail(container: dict) -> None: #打印容器的详细信息，包括镜像、状态、重启策略、端口、挂载、compose工程名、compose文件、配置指纹、挂载路径
    name = container.get("Name", "?").lstrip("/")
    config = container.get("Config") or {}
    state = container.get("State") or {}
    host = container.get("HostConfig") or {}
    labels = config.get("Labels") or {}
    mounts = container.get("Mounts") or []

    print(f"  [{name}]")
    bullet(f"镜像    : {config.get('Image')}")
    bullet(f"状态    : {state.get('Status')}")
    bullet(f"重启策略: {(host.get('RestartPolicy') or {}).get('Name') or '(默认)'}")
    bullet(f"端口    : {render_ports((container.get('NetworkSettings') or {}).get('Ports'))}")
    bullet(f"挂载    : {render_mounts(mounts)}")

    bullet("--- 是不是 compose 创建的 ---")
    project = labels.get("com.docker.compose.project")
    config_files = labels.get("com.docker.compose.project.config_files")
    config_hash = labels.get("com.docker.compose.config-hash")

    if project:
        bullet(f"  ✓ compose 工程名: {project}")  #bullet函数用于打印compose工程名
        bullet(f"    compose 文件  : {config_files or '（标签为空，无法定位）'}")
    else:
        bullet("  ⚠ 没有 compose 标签 —— 它是被 docker run 直接起的，")
        bullet("    磁盘上的 compose 文件与它无关。")
    if config_hash:
        bullet(f"    配置指纹      : {config_hash[:12]}...")

    bullet("--- 宿主资源挂载（缺失会导致部署任务失败）---")
    destinations = {m.get("Destination", "") for m in mounts}
    for path in HOST_MOUNTS:
        if path in destinations:
            bullet(f"  ✓ {path}")
        else:
            bullet(f"  ✗ {path}   <- 缺失！Jenkins 将无法操作宿主 K3s / Docker")


COMPOSE_KEY_LINES = re.compile(
    r'image:|container_name:|restart:|^ *- "|volumes:|environment:|cpus:|memory:'
) #正则表达式，匹配compose文件的关键行，用于打印compose文件的内容摘要


def print_compose_file_summary(containers: list[dict]) -> None:
    """打印 Jenkins 实际使用的 compose 文件的关键行与指纹。

    文件路径不问用户、也不翻目录找，直接读容器上的
    com.docker.compose.project.config_files 标签 —— 容器记着的才是事实。
    """
    head("6b. Jenkins 的 compose 文件内容摘要")

    for container in containers:
        name = container.get("Name", "?").lstrip("/")
        labels = (container.get("Config") or {}).get("Labels") or {}
        path = labels.get("com.docker.compose.project.config_files")

        if not path or not os.path.isfile(path):
            bullet(f"[{name}] 找不到 compose 文件，跳过（标签值是: {path or '空'}）")
            continue

        bullet(f"[{name}] 文件: {path}")
        bullet("  与 Terraform 的期望值有关的关键行:")
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for lineno, text in enumerate(fh, start=1):
                    if COMPOSE_KEY_LINES.search(text):
                        bullet(f"    {lineno}: {text.rstrip()}")
        except OSError as exc:
            bullet(f"  （读取失败：{exc}）")

        try:
            with open(path, "rb") as fh:
                bullet(f"  文件指纹(sha1): {hashlib.sha1(fh.read()).hexdigest()}")
        except OSError:
            pass


def section_containers() -> None:
    banner("主节点容器盘点")

    if not has("docker"):
        print("✗ 未安装 Docker，跳过容器盘点。")
        return

    containers = docker_containers()
    running = [c for c in containers if (c.get("State") or "").startswith("Up")]

    head("1. 所有容器（含已停止）")
    if not containers:
        bullet("（无）")
    for c in containers:
        print(f"  {c.get('Names', '?'):<28} {c.get('Image', '?'):<45} "
              f"{c.get('State', '?'):<10} {c.get('Ports', '')}")

    head("2. 运行中容器数量")
    bullet(f"运行中: {len(running)} / 总计: {len(containers)}")

    head("3. 监听端口（判断服务是否对外可达）")
    block(
        sh("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null"),
        limit=30,
        fallback="（读取失败，可能需要更高权限）",
    )

    head("4. Jenkins 容器详情")
    jenkins_containers = []
    jenkins_names = [c.get("Names", "") for c in containers
                     if "jenkins" in (c.get("Names") or "").lower()]
    if not jenkins_names:
        bullet("未找到名字含 jenkins 的容器。")
    jenkins_containers = docker_inspect(jenkins_names)
    for container in jenkins_containers:
        print_container_detail(container)

    head("5. Harbor 容器（应为多容器 compose 栈）")
    harbor_all = [c for c in containers if "harbor" in (c.get("Names") or "").lower()]
    harbor_up = [c for c in harbor_all if (c.get("State") or "").startswith("Up")]
    bullet(f"运行中 {len(harbor_up)} 个 / 总计 {len(harbor_all)} 个")
    bullet("预期：官方默认配置约 9 个容器（nginx/portal/core/registry/")
    bullet("      registryctl/db/redis/jobservice/log）")
    if not harbor_all:
        bullet("⚠ 一个都没匹配到 —— Harbor 可能没在跑，或容器名不含 harbor")
        bullet("  也可能是用别的运行时（如 K8s）部署的，需要单独确认")
    elif len(harbor_all) < 5:
        bullet("⚠ 数量偏少。可能是部分容器停了，或用了精简部署方式")
    for c in harbor_all:
        print(f"    {c.get('Names', '?'):<28} {c.get('Image', '?')}  {c.get('State', '?')}")

    head("6. compose 工程总览")
    compose = compose_command()
    if compose:
        block(sh(f"{compose} ls -a 2>/dev/null"), fallback="（无）")
    else:
        bullet("未检测到 compose 命令")

    print_compose_file_summary(jenkins_containers)

    head("7. 数据卷")
    bullet("提示：Jenkins 用的是 bind mount（宿主机目录），不是命名卷。")
    bullet("      所以这里看不到它 —— 它的数据在 /data/jenkins-data 下。")
    block(sh("docker volume ls 2>/dev/null"), limit=20, fallback="（无）")


# ===========================================================================
# section: k3s —— 集群状态（只读，kubectl get 不改变集群任何状态）
# ===========================================================================
def section_k3s() -> None:
    banner("K3s 集群状态")

    if not (has("k3s") or has("kubectl")):
        print("✗ 未检测到 k3s/kubectl，跳过。")
        return

    # 注意 k3s 默认 kubeconfig 在 /etc/rancher/k3s/k3s.yaml，需要 sudo 读
    kubectl = "k3s kubectl" if has("k3s") else "kubectl"

    head("节点列表（应看到 1 个 server + 1 个 agent）")
    block(
        sh(f"{kubectl} get nodes -o wide", sudo=True),
        fallback="读取失败，可能需要 sudo 权限",
    )

    head("节点角色与状态摘要")
    columns = (
        "名称:.metadata.name,状态:.status.conditions[-1].type,"
        "角色:.metadata.labels.node-role\\.kubernetes\\.io/control-plane"
    )
    block(
        sh(f"{kubectl} get nodes -o custom-columns={shlex.quote(columns)}", sudo=True),
        fallback="（读取失败）",
    )

    head("命名空间")
    block(sh(f"{kubectl} get ns", sudo=True), fallback="（读取失败）")

    head("所有 Pod（观察 Jenkins/Harbor 是否以 K8s 工作负载形式存在）")
    block(sh(f"{kubectl} get pods -A", sudo=True), limit=30, fallback="（读取失败）")

    head("系统服务状态")
    state = sh("systemctl is-active k3s")
    bullet(f"k3s 服务: {state or '无法确认'}")


# ===========================================================================
# section: gitlab —— 从节点上的 GitLab（Omnibus 实体服务）
#
# ★ 关键前提：GitLab 是 Omnibus 包安装的**实体服务**，不是 Docker 容器。
#   所以探测顺序是先判断安装方式，再按对应方式盘点。
# ===========================================================================
def gitlab_mode() -> str:
    if has("gitlab-ctl"):
        print("  ✓ 检测到 gitlab-ctl —— Omnibus 实体服务（系统包安装）")
        return "omnibus"
    docker_gitlab = sh(
        "docker ps -a --format '{{.Names}}' 2>/dev/null | grep -i gitlab"
    )
    if has("docker") and docker_gitlab:
        print("  ⚠ 检测到 gitlab 相关 Docker 容器 —— 容器部署（与本配置预期不符，请核对）")
        return "docker"
    print("  ✗ 未检测到 gitlab-ctl，也没有 gitlab 容器 —— GitLab 可能没在跑？")
    return "none"


def gitlab_omnibus() -> None:
    head("1. 服务健康总览（gitlab-ctl status）")
    block(
        sh("gitlab-ctl status", sudo=True),
        fallback="读取失败，可能需要 sudo 权限",
    )

    head("2. 版本与 external_url")
    info = sh("gitlab-rake gitlab:env:info", sudo=True)
    for line in info.splitlines():
        if re.search(r"GitLab information|Version|GitLab Shell", line):
            print(f"    {line}")
    ext = sh("grep -E '^\\s*external_url' /etc/gitlab/gitlab.rb", sudo=True)
    for line in (ext or "").splitlines()[:3]:
        print(f"    {line}")
    if not ext:
        bullet("（未在 gitlab.rb 顶部找到 external_url，可能用了默认值）")

    head("3. Web 健康探针")
    ext_url = ""
    match = re.search(r"external_url\s+[\"']([^\"']+)[\"']", ext or "")
    if match:
        ext_url = match.group(1)
    for url in (ext_url, "http://127.0.0.1", "http://127.0.0.1:80"):
        if not url:
            continue
        if sh(f"curl -fsS --max-time 5 {shlex.quote(url)}/-/health"):
            bullet(f"✓ {url}/-/health 返回正常")
            break

    head("4. 数据目录占用（仓库都在 /var/opt/gitlab）")
    for path in ("/var/opt/gitlab", "/var/log/gitlab"):
        if os.path.isdir(path):
            bullet(f"{path} : {sh(f'du -sh {path}', sudo=True) or 'n/a'}")

    head("5. 备份目录（gitlab-backup 默认落点）")
    backups = "/var/opt/gitlab/backups"
    if os.path.isdir(backups):
        listing = sh(f"ls {backups}", sudo=True)
        count = len([x for x in listing.splitlines() if x.strip()])
        bullet(f"{backups} : {count} 个备份文件")
        block(sh(f"ls -lht {backups}", sudo=True), limit=4, fallback="")
    else:
        bullet("⚠ 备份目录不存在 —— 还没跑过 gitlab-backup？仓库只有一份拷贝，值得警惕")

    head("6. 本机内存（GitLab 与 K3s agent 共存，这里最关键）")
    block(sh("free -h"), limit=2, fallback="n/a")

    head("7. K3s agent 状态（确认 GitLab 没把资源吃垮）")
    state = sh("systemctl is-active k3s-agent") or sh("systemctl is-active k3s")
    bullet(f"k3s 服务: {state or '无法确认'}")


def gitlab_docker() -> None:
    head("Docker 部署的容器详情")
    names = [
        c.get("Names", "")
        for c in docker_containers()
        if "gitlab" in (c.get("Names") or "").lower()
    ]
    for container in docker_inspect(names):
        name = container.get("Name", "?").lstrip("/")
        config = container.get("Config") or {}
        state = container.get("State") or {}
        print(f"  [{name}]")
        bullet(f"镜像    : {config.get('Image')}")
        bullet(f"状态    : {state.get('Status')}")
        bullet(f"端口    : {render_ports((container.get('NetworkSettings') or {}).get('Ports'))}")
        bullet(f"挂载    : {render_mounts(container.get('Mounts'))}")


def section_gitlab() -> None:
    banner("从节点 GitLab 盘点")

    head("0. 安装方式识别")
    mode = gitlab_mode()

    if mode == "omnibus":
        gitlab_omnibus()
    elif mode == "docker":
        gitlab_docker()

    head("8. 本机监听端口（GitLab 对外端口以这里为准）")
    listeners = sh("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null")
    matched = [
        line for line in listeners.splitlines()
        if re.search(r":(22|80|443|2222|8081|8929)\b", line)
    ]
    for line in matched:
        bullet(line)
    if not matched:
        bullet("（未匹配到常见端口，或读取失败）")

# ===========================================================================
SECTIONS = {
    "base": section_base,
    "containers": section_containers,
    "k3s": section_k3s,
    "gitlab": section_gitlab,
}
#sections字典，包含所有可能的section，每个section对应一个函数，用于打印该section的详细信息

def main() -> None:
    section = CFG.get("section", "") #CFG字典，包含所有可能的配置项，包括section
    handler = SECTIONS.get(section) #获取对应section的函数
    if handler is None:
        print(f"  ✗ 未知的 section: {section!r}（可选 {sorted(SECTIONS)}）", file=sys.stderr)
        raise SystemExit(1)
    handler()
    print()


if __name__ == "__main__":
    main()
