#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitLab 项目 + Push Webhook 同步（在 K3s 从节点上执行）

这一层管的是整条流水线的**起点**：

    git push -> GitLab -> (Push Hook) -> Jenkins

幂等原则同其他脚本：
    项目/钩子不存在    -> 创建
    已存在且配置一致    -> 什么都不做
    已存在但配置不同    -> 更新

★★ 本脚本处理的最关键问题：GitLab 默认禁止 webhook 回调私有网段。

    GitLab 从 10.6 起默认拦截 webhook 对 127.0.0.1 / 10.0.0.0/8 /
    172.16.0.0/12 / 192.168.0.0/16 的请求（防 SSRF）。而实验环境整条链路
    都在 192.168.x.x 里 —— 不打开放行，git push 之后 webhook 会被静默拦截，
    Jenkins 收不到任何东西，GitLab 界面上也没有明显报错。

    所以这里做两层处理：
      1) 打开全局开关 allow_local_requests_from_web_hooks_and_services
      2) 把 Jenkins 的 host:port 加进 outbound 放行白名单
    并在最后重新读取设置来**验证**是否真的生效（而不是设完就假定成功）。

---------------------------------------------------------------------------
本地调试

    WF_CONFIG_JSON='{"gitlab_url":"http://192.168.1.102","token":"...",
      "project_name":"demo-app","project_path":"demo-app",
      "hook_url":"http://192.168.1.101:8081/x?token=y",
      "lan_entry":"192.168.1.101:8081","allow_local":true,"do_test":false,
      "project_payload":{"name":"demo-app","path":"demo-app","visibility":"private"},
      "hook_payload":{"url":"...","push_events":true}}' \
      python3 scripts/gitlab-project.py
"""

from __future__ import annotations

import base64
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

CONFIG_B64 = "__WF_CONFIG_B64__"

TAG = "gitlab"
TIMEOUT = 60

# 白名单条目允许的字符集：只放行明显正常的 host[:port] / 网段写法，
# 避免把脏数据写回实例配置
SAFE_ENTRY = re.compile(r"^[A-Za-z0-9._:/-]+$")

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

GL: str = CFG["gitlab_url"].rstrip("/")   # 去掉结尾斜杠，避免拼出 //api/v4
API: str = f"{GL}/api/v4"
PROJECT_NAME: str = CFG["project_name"]
PROJECT_PATH: str = CFG["project_path"]
HOOK_URL: str = CFG["hook_url"]
LAN_ENTRY: str = CFG.get("lan_entry", "")
ALLOW_LOCAL: bool = bool(CFG.get("allow_local", False))
DO_TEST: bool = bool(CFG.get("do_test", False))
PROJECT_PAYLOAD: dict = CFG.get("project_payload") or {}
HOOK_PAYLOAD: dict = CFG.get("hook_payload") or {}

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

_AUTH = {"PRIVATE-TOKEN": CFG["token"]}


def request(method: str, path: str, *, payload: dict | None = None,
            form: list[tuple[str, str]] | None = None) -> tuple[int, object]:
    """发一次 API 请求，返回 (状态码, 响应体)。状态码 0 表示连接层面失败。"""
    url = path if path.startswith("http") else f"{API}{path}"
    headers = {"Accept": "application/json", **_AUTH}
    data = None

    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, (json.loads(raw) if raw.strip() else None)
        except json.JSONDecodeError:
            return exc.code, raw
    except OSError as exc:
        return 0, str(exc)


def brief(body: object, limit: int = 400) -> str:
    text = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
    return text[:limit] # 打印日志、报错信息预览，防止打印超大对象把日志撑爆，只输出摘要。


# ===========================================================================
# 0. 连通性检查
# ===========================================================================
def check_reachable() -> None:
    status, body = request("GET", "/version")
    if status != 200:
        die(
            f"连不上 GitLab API：{API}/version\n"
            "     请依次确认：\n"
            "       1) GitLab 服务在跑：sudo gitlab-ctl status\n"
            f"       2) 端口通：curl -I {GL}\n"
            "       3) gitlab_external_url 是否与 gitlab.rb 里的 external_url 一致\n"
            "          （不一致时 GitLab 可能返回重定向，导致 API 调用失败）"
        )
    version = body.get("version") if isinstance(body, dict) else "?"
    log(f"GitLab API 可达（版本 {version}）")


# ===========================================================================
# 1. Token 认证检查
#
# 连不上和认证失败是两种完全不同的原因，处置方式也不同，所以分开报错。
# ===========================================================================
def check_token() -> bool:
    status, _ = request("GET", "/user")
    if status == 200:
        log("Token 认证通过")
    elif status == 401:
        die(
            "Token 无效（HTTP 401）。请检查 gitlab_api_token。\n"
            "     可以在从节点上直接生成一个（会打印出 token 明文）：\n"
            "       sudo gitlab-rails runner \"u=User.find_by_username('root'); "
            "t=u.personal_access_tokens.new(scopes: ['api'], name: 'terraform-managed', "
            "expires_at: 365.days.from_now); t.save!; puts t.token\"\n"
            "     也可以走界面：GitLab -> 用户设置 -> Access Tokens -> 勾选 api -> 生成"
        )
    elif status == 403:
        die("Token 权限不足（HTTP 403）。该 token 需要 api scope，"
            "且需要管理员权限才能修改实例级设置。")
    elif status == 0:
        die(f"请求失败，无法连接到 {API}")
    else:
        log(f"⚠ GitLab 返回 HTTP {status}，继续尝试")

    status, _ = request("GET", "/application/settings")
    if status == 200:
        return True
    warn("当前 token 不是管理员，无法读取/修改实例级设置。")
    warn("下面的「本地网络放行」检查会跳过 —— 请自行确认该项已开启，否则 webhook 不会生效。")
    return False


# ===========================================================================
# 2. 本地网络放行（★ 本脚本最关键的一步）
# ===========================================================================
ALLOWLIST_KEYS = ("outbound_local_requests_whitelist", "outbound_local_requests_allowlist_raw")


def read_settings() -> dict:
    status, body = request("GET", "/application/settings")
    return body if isinstance(body, dict) else {}


def read_allowlist(settings: dict) -> list[str]:
    for key in ALLOWLIST_KEYS:
        value = settings.get(key)
        if isinstance(value, list):
            return [str(v) for v in value]
    return [] # 


def allow_local_network() -> None:
    print()
    log("检查 webhook 本地网络放行设置")

    settings = read_settings()
    if settings.get("allow_local_requests_from_web_hooks_and_services") is True:
        log("✓ 已允许向本地网络发起 webhook 请求")
    else:
        log("未开启，正在开启（allow_local_requests_from_web_hooks_and_services）")
        request("PUT", "/application/settings", form=[
            ("allow_local_requests_from_web_hooks_and_services", "true"),
        ])

    # 白名单：即使全局开关因为版本差异没生效，白名单也能提供一条通路。
    # 注意 GitLab 是「整体替换」数组，所以要把已有条目一起带上。
    if LAN_ENTRY:
        existing = [e for e in read_allowlist(settings) if SAFE_ENTRY.match(e)]
        if LAN_ENTRY in existing:
            log(f"✓ 白名单已包含 {LAN_ENTRY}")
        else:
            log(f"把 {LAN_ENTRY} 加入白名单")
            entries = [*existing, LAN_ENTRY]
            request("PUT", "/application/settings", form=[
                ("outbound_local_requests_whitelist[]", e) for e in entries
            ])

    # ---------------------------------------------------------------------
    # 验证：重新读一次，而不是假定刚才的写入成功了。
    # 这个设置如果不生效，整个流水线入口就是死的，值得多花这一次请求。
    # ---------------------------------------------------------------------
    settings = read_settings()
    flag_ok = settings.get("allow_local_requests_from_web_hooks_and_services") is True # 与前面的bool类型进行比较
    list_ok = (not LAN_ENTRY) or (LAN_ENTRY in read_allowlist(settings))

    if flag_ok and list_ok:
        log("✓ 验证通过：本地网络请求已放行")
        return

    warn("✗ 放行设置仍未生效（可能是 GitLab 版本改了这个设置名）。")   
    print(f"        webhook 到 {LAN_ENTRY} 会被 GitLab 拦截，流水线不会触发。")
    print("        请手工开启：Admin -> Settings -> Network -> Outbound requests")
    print("        勾选「Allow requests to the local network from webhooks and integrations」，")
    print(f"        并在放行列表里加入：{LAN_ENTRY}")


# ===========================================================================
# 3. 项目
# ===========================================================================
def resolve_namespace(payload: dict) -> dict:
    """缓存解析命名空间 id 的结果，避免重复请求。"""
    """通过接口查询命名空间 id，而不是直接解析。形如 group/project 时，先解析命名空间 id，否则会建到个人空间下。"""
    if "/" not in PROJECT_PATH:
        return payload

    ns_name = PROJECT_PATH.split("/")[0]
    status, body = request("GET", f"/namespaces?search={urllib.parse.quote(ns_name)}")
    candidates = body if isinstance(body, list) else []
    ns = next(
        (
            n for n in candidates
            if ns_name in (n.get("full_path"), n.get("path"), n.get("name"))
        ),
        None,
    )
    if ns:
        log(f"找到命名空间 {ns_name}（id={ns['id']}）")
        return {**payload, "namespace_id": ns["id"]}

    warn(f"未找到命名空间 {ns_name}，项目将创建到个人空间下。")
    warn("如需建在 group 下，请先在 GitLab 上创建该 group。")
    return payload


def ensure_project() -> int:
    """确保项目存在，返回项目 id。"""
    print()
    # quote 的 safe 设为空，这样 "/" 会被编码成 %2F（GitLab 要求）
    encoded = urllib.parse.quote(PROJECT_PATH, safe="")
    status, body = request("GET", f"/projects/{encoded}")

    if status == 200 and isinstance(body, dict) and body.get("id"):
        pid = int(body["id"])
        log(f"项目 {PROJECT_PATH} 已存在（id={pid}）")
        return pid

    log(f"项目 {PROJECT_PATH} 不存在，创建中")
    payload = resolve_namespace(dict(PROJECT_PAYLOAD))

    status, body = request("POST", "/projects", payload=payload)
    pid = body.get("id") if isinstance(body, dict) else None
    if not pid:
        die(f"创建项目失败（HTTP {status}）。GitLab 响应：\n     {brief(body)}")

    log(f"项目创建成功（id={pid}）")
    log(f"克隆地址：{GL}/{PROJECT_PATH}.git")
    return int(pid)


# ===========================================================================
# 4. Webhook（Push Hook -> Jenkins）
# ===========================================================================
def find_hook(pid: int) -> dict | None:
    status, body = request("GET", f"/projects/{pid}/hooks")
    hooks = body if isinstance(body, list) else []
    return next((h for h in hooks if h.get("url") == HOOK_URL), None)


def hook_drift(hook: dict) -> list[str]:
    reasons: list[str] = []
    if hook.get("push_events") is not True:
        reasons.append("push_events 未开启")

    # 分支过滤：期望为空时不比对（用户可能只想要默认行为）
    expected_filter = HOOK_PAYLOAD.get("push_events_branch_filter", "")
    actual_filter = hook.get("push_events_branch_filter") or ""
    if (expected_filter or "") != actual_filter:
        reasons.append(f"分支过滤不一致（当前='{actual_filter}' 期望='{expected_filter}'）")
    return reasons


def sync_hook(pid: int) -> int:
    """确保 webhook 配置与期望一致，返回 webhook id。"""
    print()
    hook = find_hook(pid)

    if hook is None:
        log("webhook 不存在，创建中")
        status, body = request("POST", f"/projects/{pid}/hooks", payload=HOOK_PAYLOAD)
        hook_id = body.get("id") if isinstance(body, dict) else None
        if not hook_id:
            die(f"创建 webhook 失败（HTTP {status}）。GitLab 响应：\n     {brief(body)}")
        log(f"webhook 创建成功（id={hook_id}）")
        return int(hook_id)

    hook_id = int(hook["id"])
    log(f"webhook 已存在（id={hook_id}），核对配置")

    reasons = hook_drift(hook) # 检查配置是否与期望一致
    if not reasons:
        log("配置与期望一致，无需改动 ✓")
        print("        （注：GitLab 不会返回 webhook secret token 的明文，")
        print("          所以 token 无法比对。如需更换，请先删除该 webhook 再重跑）")
        return hook_id

    for reason in reasons:
        log(f"  -> 需更新：{reason}")
    status, body = request("PUT", f"/projects/{pid}/hooks/{hook_id}", payload=HOOK_PAYLOAD)
    if isinstance(body, dict) and body.get("id"):
        log("webhook 更新成功 ✓")
    else:
        die(f"更新 webhook 失败（HTTP {status}）。GitLab 响应：\n     {brief(body)}")
    return hook_id


# ===========================================================================
# 5. 最近投递记录（只读诊断）
#
# 「推了代码但流水线没动静」时，这里是第一手证据：
# 能看到 GitLab 到底有没有发出去、Jenkins 回的什么码。
# 该接口在部分 GitLab 版本上不存在，取不到就安静跳过。
# ===========================================================================
def show_recent_deliveries(pid: int, hook_id: int) -> None:
    print()
    log("最近 webhook 投递记录：")
    status, body = request("GET", f"/projects/{pid}/hooks/{hook_id}/events?per_page=5")
    if status != 200 or not isinstance(body, list) or not body:
        print("    （暂无投递记录 —— 说明还没发生过 push，或该 GitLab 版本不支持此接口）")
        return

    for event in body[:5]:
        created = event.get("created_at", "?")
        code = event.get("response_status", "?")
        print(f"    {created}  响应码 {code}")


# ===========================================================================
# 6. 可选：实打实发一次测试事件
#
# 这是唯一能真正证明「GitLab 能打到 Jenkins」的方法 —— 接口会返回
# Jenkins 的真实响应码。代价是会真的触发一次构建。
# ===========================================================================
def send_test_event(pid: int, hook_id: int) -> None:
    print()
    warn("gitlab_webhook_test_on_apply = true，发送一次真实测试事件")
    warn("注意：这会真的触发一次 Jenkins 构建，进而走完整条流水线")
    status, body = request("POST", f"/projects/{pid}/hooks/{hook_id}/test/push")
    print(f"    GitLab 返回（HTTP {status}）：{brief(body, 300)}")
    print("    若这里显示 2xx/201 之类，说明 GitLab 到 Jenkins 是通的；")
    print("    若显示被拒绝或超时，回到第 2 步检查本地网络放行设置。")


# ===========================================================================
# 7. 结果摘要
# ===========================================================================
def summarize(pid: int, hook_id: int) -> None:
    print()
    log("GitLab 流水线入口配置：")
    print(f"    项目        : {GL}/{PROJECT_PATH}  (id={pid})")
    print(f"    推送 webhook: {HOOK_URL}")
    print(f"    webhook id  : {hook_id}")
    branch = HOOK_PAYLOAD.get("push_events_branch_filter") or "（全部分支）"
    print(f"    分支过滤    : {branch}")
    print()
    log("触发链路：git push -> GitLab -> Jenkins")


def main() -> None:
    check_reachable()
    is_admin = check_token()
    if is_admin and ALLOW_LOCAL:
        allow_local_network()

    pid = ensure_project()
    hook_id = sync_hook(pid)
    show_recent_deliveries(pid, hook_id)
    if DO_TEST:
        send_test_event(pid, hook_id)
    summarize(pid, hook_id)


if __name__ == "__main__":
    main()
