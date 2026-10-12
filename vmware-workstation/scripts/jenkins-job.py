#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jenkins 部署任务同步（在 K3s 主节点上执行）

幂等：任务不存在则创建，已存在且配置一致则不动，不一致才更新 config.xml。

---------------------------------------------------------------------------
设计说明 —— 为什么任务只做「指向仓库的壳」

任务的流水线逻辑应该写在应用仓库的 Jenkinsfile 里（Pipeline-as-Code），
而不是塞在这段 XML 中。这样流水线演进走 Git 提交、可 review、可回滚，
Terraform 只负责保证「任务存在且指向正确的仓库和分支」。

---------------------------------------------------------------------------
与 shell 版的两点改进

1. 「配置是否一致」改用 XML 解析比对，而不是 grep 关键字。
   旧版是 `echo "$CURRENT" | grep -qF "$REPO_URL"` —— 只要字符串在文件里
   任何位置出现就算通过，无法区分「配置正确」与「碰巧出现」。

2. 注入 XML 的值做了转义。仓库地址或分支名里出现 & 或 < 时，
   旧版会拼出非法 XML，而 Jenkins 只回一个 4xx，很难定位。

---------------------------------------------------------------------------
本地调试

    WF_CONFIG_JSON='{"jenkins_url":"http://localhost:8081","user":"admin",
      "token":"...","job_name":"deploy-app","repo_url":"http://gitlab/app.git",
      "branch":"*/main","jenkinsfile":"Jenkinsfile"}' \
      python3 scripts/jenkins-job.py
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

CONFIG_B64 = "__WF_CONFIG_B64__"

TAG = "jenkins"
TIMEOUT = 30

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


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


CFG = load_config()

JENKINS: str = CFG["jenkins_url"].rstrip("/")
JOB_NAME: str = CFG["job_name"]
REPO_URL: str = CFG["repo_url"]
BRANCH: str = CFG["branch"]
JENKINSFILE: str = CFG["jenkinsfile"]

# Jenkins 大多用自签证书；沿用旧脚本的「跳过校验」行为
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

_AUTH = {
    "Authorization": "Basic "
    + base64.b64encode(f"{CFG['user']}:{CFG['token']}".encode("utf-8")).decode("ascii")
}


def request(method: str, path: str, *, data: bytes | None = None,
            headers: dict | None = None) -> tuple[int, str]:
    """返回 (状态码, 响应文本)。状态码 0 表示连接层面失败。"""
    url = path if path.startswith("http") else f"{JENKINS}{path}"
    merged = {"Accept": "application/json", **_AUTH, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=merged, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


# ===========================================================================
# 0. 连通性 + 认证检查
#
# 特意区分「连不上」和「认证失败」—— 排查时这两种原因的处置方式完全不同。
# ===========================================================================
def check_auth() -> None:
    status, _ = request("GET", "/api/json")
    if status == 200:
        log("Jenkins API 可达，认证通过")
    elif status in (401, 403):
        die(
            f"认证失败（HTTP {status}）。请检查 jenkins_admin_user / jenkins_api_token。\n"
            "     注意：API Token 不是登录密码，需要在\n"
            "     Jenkins -> 用户 -> Configure -> API Token 生成。"
        )
    elif status == 0:
        die(f"连不上 Jenkins：{JENKINS}。请检查地址与端口。")
    else:
        log(f"⚠ Jenkins 返回 HTTP {status}，继续尝试")


# ===========================================================================
# 1. 获取 CSRF crumb
#
# Jenkins 默认开启 CSRF 防护，POST 必须带 crumb。
# 但某些配置会关闭它（此时 crumbIssuer 返回 404），所以这里不能因失败而中断。
# ===========================================================================
def fetch_crumb() -> dict:
    status, text = request("GET", "/crumbIssuer/api/json")
    if status != 200:
        log("未获取到 crumb（CSRF 可能已关闭），按无 crumb 继续")
        return {}
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        log("crumb 响应不是 JSON，按无 crumb 继续")
        return {}
    field, value = body.get("crumbRequestField"), body.get("crumb")
    if not field or not value:
        log("crumb 字段缺失，按无 crumb 继续")
        return {}
    log("已获取 CSRF crumb")
    return {field: value}


# ===========================================================================
# 2. 生成任务配置 XML
# ===========================================================================
def gen_config_xml() -> str:
    return f"""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>Terraform managed deploy pipeline. 实际流水线逻辑见仓库中的 {escape(JENKINSFILE)}</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition" plugin="workflow-cps">
    <scm class="hudson.plugins.git.GitSCM" plugin="git">
      <configVersion>2</configVersion>
      <userRemoteConfigs>
        <hudson.plugins.git.UserRemoteConfig>
          <url>{escape(REPO_URL)}</url>
        </hudson.plugins.git.UserRemoteConfig>
      </userRemoteConfigs>
      <branches>
        <hudson.plugins.git.BranchSpec>
          <name>{escape(BRANCH)}</name>
        </hudson.plugins.git.BranchSpec>
      </branches>
    </scm>
    <scriptPath>{escape(JENKINSFILE)}</scriptPath>
    <lightweight>true</lightweight>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>
"""


# ===========================================================================
# 3. 判断现有配置是否已经符合期望
# ===========================================================================
def job_matches(current_xml: str) -> bool:
    """用 XML 解析比对仓库地址 / Jenkinsfile 路径 / 分支。

    Jenkins 会把提交的 XML 重新规范化（补字段、改缩进），所以不能直接比文本；
    但逐个比对关键字段是可靠的，也比 grep 关键字严格得多。
    """
    try:
        root = ET.fromstring(current_xml)
    except ET.ParseError:
        return False

    if REPO_URL not in [e.text for e in root.iter("url")]:
        return False
    if JENKINSFILE not in [e.text for e in root.iter("scriptPath")]:
        return False
    if BRANCH not in [e.text for e in root.iter("name")]:
        return False
    return True


# ===========================================================================
# 4. 创建或更新
# ===========================================================================
def sync_job(crumb: dict) -> None:
    xml = gen_config_xml().encode("utf-8")
    xml_headers = {"Content-Type": "application/xml", **crumb}

    status, _ = request("GET", f"/job/{urllib.parse.quote(JOB_NAME)}/api/json")

    if status == 200:
        log(f"任务 {JOB_NAME} 已存在，核对配置")

        # 先比对现状，一致就不动（避免无谓地修改任务、产生多余的变更记录）
        cur_status, current = request(
            "GET", f"/job/{urllib.parse.quote(JOB_NAME)}/config.xml"
        )
        if cur_status == 200 and job_matches(current):
            log("配置与期望一致，无需改动 ✓")
            return

        code, body = request(
            "POST",
            f"/job/{urllib.parse.quote(JOB_NAME)}/config.xml",
            data=xml,
            headers=xml_headers,
        )
        if code == 200:
            log("任务配置已更新 ✓")
        else:
            die(f"更新任务失败，HTTP {code}：{body[:300]}")
        return

    log(f"任务 {JOB_NAME} 不存在（HTTP {status}），创建中")
    code, body = request(
        "POST",
        f"/createItem?name={urllib.parse.quote(JOB_NAME)}",
        data=xml,
        headers=xml_headers,
    )
    if code in (200, 201):
        log("任务创建成功 ✓")
    else:
        die(f"创建任务失败，HTTP {code}：{body[:300]}")


def main() -> None:
    check_auth()
    crumb = fetch_crumb()
    sync_job(crumb)
    log(f"访问地址: {JENKINS}/job/{JOB_NAME}")


if __name__ == "__main__":
    main()
