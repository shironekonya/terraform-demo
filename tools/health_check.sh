#!/usr/bin/env bash
# ===========================================================================
# health_check.sh —— 灰度流水线的验收门
#
# 用法：health_check.sh <公网IP> <期望版本>
#
# 验的不只是"80 通了"：
#   · HTTP 200 —— nginx 在服务（反代活着、容器活着）
#   · 页面里有 <meta name="wf-release" content="期望版本"> —— 跑的是**这一版**
#     页面能打开但版本不对 = 反代指到了旧容器/旧机器，一样算失败。
#
# 新机器要经历：开机 → ansible 初始化 → 容器栈起来 → nginx 反代就绪，
# 全链路冷启动给 90 秒（15 次 x 6 秒）重试窗口；窗口内没就绪就按失败处理，
# 由流水线的 rollback 路径销毁这台机器。
# ===========================================================================
set -euo pipefail

ip="${1:?用法: health_check.sh <公网IP> <期望版本>}"
want="${2:?缺少期望版本（CI 里传 \$CI_COMMIT_SHORT_SHA）}"
expect="<meta name=\"wf-release\" content=\"${want}\">"

tries="${WF_HEALTH_TRIES:-15}"
interval="${WF_HEALTH_INTERVAL:-6}"

for i in "$(seq 1 "$tries")"; do
    body="$(curl -sS --max-time 5 "http://${ip}/" 2>/dev/null || true)"
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://${ip}/" 2>/dev/null || echo 000)"

    if [ "$code" = "200" ] && [[ "$body" == *"$expect"* ]]; then
        echo "✓ 健康检查通过：http://${ip}/ 返回 200，版本标记 = ${want}（第 ${i} 次探测）"
        exit 0
    fi

    # 失败时给出"差在哪"的一行诊断，避免只看到 code=000 干瞪眼
    verdict="未就绪"
    if [ "$code" != "200" ]; then
        verdict="HTTP ${code:-无响应}"
    elif [[ "$body" != *'wf-release'* ]]; then
        verdict="页面里没有版本标记（可能还是 Terraform 的欢迎页或待部署页）"
    else
        verdict="版本标记对不上：页面是 $(echo "$body" | grep -o 'wf-release" content="[^"]*' || echo '?')"
    fi
    echo "  [${i}/${tries}] ${verdict} —— ${interval}s 后重试"
    sleep "$interval"
done

echo
echo "✗ 健康检查失败：${tries} 次探测内 http://${ip}/ 没有返回版本 ${want} 的页面。"
echo "  常见原因（按出现概率）："
echo "    1. ansible 初始化失败（回看 gray:configure 的日志，通常在镜像拉取那一步）"
echo "    2. 容器起了但 nginx 反代没配好（机器上执行：nginx -t && systemctl status nginx）"
echo "    3. 安全组没放行 80（回看 terraform 的 plan 输出里 security_group_rule）"
exit 1
