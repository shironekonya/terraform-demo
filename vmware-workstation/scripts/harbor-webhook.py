#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Harbor 项目 + Webhook 策略同步（在 K3s 主节点上执行）

幂等原则：
    不存在            -> 创建
    已存在且配置一致  -> 什么都不做
    已存在但配置不同  -> 更新

★★ 最关键的一处防护：只监听 PUSH_ARTIFACT，绝不监听 PULL_ARTIFACT。
    因为部署任务自身要拉镜像，监听 PULL_ARTIFACT 会导致
        部署 -> 拉镜像 -> 触发 webhook -> 再部署 -> 再拉镜像 ...
    无限循环，把 Jenkins 和 K3s 一起拖死。

---------------------------------------------------------------------------
为什么这个脚本能从 shell 版瘦身一大截

旧版用 `curl` + `grep`/`sed`/`tr` 手工解析 JSON，比如按名字找 webhook id：

    WEBHOOK_ID=$(echo "$LIST" | tr '}' '\n' \\
      | grep -F "\\"name\\":\\"${WEBHOOK_NAME}\\"" \\
      | sed -n 's/.*"id":\\([0-9]*\\).*/\\1/p' | head -1)

这段逻辑有若干隐含假设（字段顺序、无嵌套、无转义），换个版本就可能失效。
Python 直接用 json 模块，语义是明确的：

    policy = next((p for p in policies if p.get("name") == name), None)

---------------------------------------------------------------------------
本地调试

    WF_CONFIG_JSON='{"api":"http://localhost","project":"apps","user":"admin",
      "password":"...","webhook_name":"jenkins-deploy-trigger",
      "endpoint":"http://192.168.1.101:8081/x?token=y","auth_header":"",
      "event_types":["PUSH_ARTIFACT"]}' python3 scripts/harbor-webhook.py
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

CONFIG_B64 = "__WF_CONFIG_B64__"

TAG = "harbor"
TIMEOUT = 60

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


def warn(message: str) -> None:
    print(f"  ⚠ {message}")


CFG = load_config()

# ---------------------------------------------------------------------------
# 基址归一化：Harbor 的 REST API 在 /api/v2.0 下
# ---------------------------------------------------------------------------
def normalize_base(url: str) -> str:
    base = url.rstrip("/")
    if not base.endswith("/api/v2.0"):
        base = f"{base}/api/v2.0"
    return base


# 基础配置
#BASE = normalize_base(CFG["api"])
PROJECT: str = CFG["project"]
WEBHOOK_NAME: str = CFG["webhook_name"]
ENDPOINT: str = CFG["endpoint"]
AUTH_HEADER: str = CFG.get("auth_header", "")
EVENTS: list[str] = list(CFG.get("event_types", []))

# Harbor 大多用自签证书，沿用旧脚本的「跳过校验」行为
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

_AUTH = {
    "Authorization": "Basic "
    + base64.b64encode(f"{CFG['user']}:{CFG['password']}".encode("utf-8")).decode("ascii")
}


def _parse(raw: str):
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def request(method: str, path: str, *, payload=None) -> tuple[int, object]:
    """发一次 API 请求，返回 (HTTP 状态码, 响应体)。

    状态码 0 表示连接层面就失败了（区别于服务返回 4xx/5xx）。
    """
    url = path if path.startswith("http") else f"{BASE}{path}"
    headers = {"Accept": "application/json", **_AUTH}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as resp:
            return resp.status, _parse(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, _parse(exc.read().decode("utf-8", "replace"))
    except OSError as exc:
        return 0, str(exc)


def brief(body: object, limit: int = 300) -> str:
    text = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
    return text[:limit]
#brief用于


# ===========================================================================
# 0. 连通性检查
# ===========================================================================
def check_reachable() -> None:
    status, body = request("GET", "/systeminfo")
    if status != 200:
        die(
            f"调不通 Harbor API: {BASE}/systeminfo（HTTP {status}）\n"
            "     请确认：1) Harbor 在运行  2) 用户名密码正确\n"
            "               3) harbor_url 指向宿主机可达的地址（脚本在主节点上跑，用 localhost）\n"
            f"     响应：{brief(body)}"
        )
    version = body.get("harbor_version") if isinstance(body, dict) else "?"
    log(f"Harbor API 可达（{BASE}，版本 {version}）")


# ===========================================================================
# 1. 确保项目存在
# ===========================================================================
def ensure_project() -> None:
    status, body = request("GET", f"/projects?name={urllib.parse.quote(PROJECT)}")
    items = body if isinstance(body, list) else []
    existing = next((p for p in items if p.get("name") == PROJECT), None) # 遍历项目列表，查找项目是否存在

    if existing:
        log(f"项目 {PROJECT} 已存在（id={existing.get('project_id')}）")
        return

    log(f"创建项目 {PROJECT}")
    status, body = request(
        "POST",
        "/projects",
        payload={
            "project_name": PROJECT,
            "public": False,
            "metadata": {"public": "false"},
        },
    )
    # 201 = 创建成功；409 = 并发下已被别处创建 —— 两种都算就绪
    if status in (201, 409):
        log(f"项目 {PROJECT} 就绪（HTTP {status}）")
    else:
        die(f"创建项目失败，HTTP {status}，响应：{brief(body)}")


# ===========================================================================
# 2. 查询已有 webhook 策略（按名字识别，保证幂等）
# ===========================================================================
def list_policies() -> list[dict]:
    status, body = request("GET", f"/projects/{PROJECT}/webhook/policies?page_size=100")
    if status != 200 or not isinstance(body, list):
        return []
    return body


def build_payload() -> dict:
    target = {
        "type": "http",
        "address": ENDPOINT,
        "skip_cert_verify": True,
        "payload_format": "Default",
    }
    # auth_header 为空时不要写进 payload，否则 Harbor 会收到一个空认证头
    if AUTH_HEADER:
        target["auth_header"] = AUTH_HEADER

    return {
        "name": WEBHOOK_NAME,
        "description": "Terraform managed - trigger Jenkins deploy on image push",
        "enabled": True,
        "event_types": EVENTS,
        "targets": [target],
    }


def policy_drift(policy: dict) -> list[str]:
    """比对已有策略与期望，返回差异描述列表（空列表 = 一致）。"""
    reasons: list[str] = []

    actual_events = set(policy.get("event_types") or [])
    if actual_events != set(EVENTS):
        reasons.append(f"事件类型不一致（实际 {sorted(actual_events)} / 期望 {sorted(EVENTS)}）")

    # 反向检查：出现了 PULL_ARTIFACT 说明有循环风险，必须纠正
    if "PULL_ARTIFACT" in actual_events:
        reasons.append("⚠ 检测到 PULL_ARTIFACT（会造成部署循环）")

    addresses = [t.get("address") for t in (policy.get("targets") or [])]
    if ENDPOINT not in addresses:
        reasons.append(f"回调地址不一致（实际 {addresses} / 期望 {ENDPOINT}）")

    if AUTH_HEADER:
        headers = [t.get("auth_header", "") for t in (policy.get("targets") or [])]
        if AUTH_HEADER not in headers:
            reasons.append("认证头不一致")

    return reasons


def sync_webhook() -> None:
    policies = list_policies()
    existing = next((p for p in policies if p.get("name") == WEBHOOK_NAME), None)

    if existing is None:
        log(f"webhook 策略 {WEBHOOK_NAME} 不存在，创建中")
        status, body = request(
            "POST", f"/projects/{PROJECT}/webhook/policies", payload=build_payload()
        )
        if status in (200, 201):
            log("webhook 创建成功 ✓")
        else:
            die(f"创建 webhook 失败，HTTP {status}，响应：{brief(body)}")
        return

    policy_id = existing.get("id")
    log(f"webhook 策略 {WEBHOOK_NAME} 已存在（id={policy_id}），核对配置")

    reasons = policy_drift(existing)
    if not reasons:
        log("配置完全一致，无需改动 ✓")
        return

    for reason in reasons:
        log(f"  -> 需更新：{reason}")

    status, body = request(
        "PUT", f"/projects/{PROJECT}/webhook/policies/{policy_id}", payload=build_payload()
    )
    if status in (200, 201):
        log("webhook 更新成功 ✓")
    else:
        die(f"更新 webhook 失败，HTTP {status}，响应：{brief(body)}")


# ===========================================================================
# 3. 结果核对
# ===========================================================================
def summarize() -> None:
    print()
    log(f"当前项目 {PROJECT} 的 webhook 策略：")
    names = sorted({p.get("name", "?") for p in list_policies()})
    for name in names:
        print(f"    - {name}")
    print()
    log(f"监听事件：{EVENTS}")
    log(f"回调地址：{ENDPOINT}")


def main() -> None:
    check_reachable()
    ensure_project()
    sync_webhook()
    summarize()


if __name__ == "__main__":
    main()
