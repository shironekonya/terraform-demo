#!/usr/bin/env bash
# ===========================================================================
# gray_slot.sh —— 算出这次灰度该建哪个槽、该保哪个槽
#
# 蓝绿的记账方式：每个槽位是一个 terraform workspace（state 在 OSS 里按
# workspace 前缀隔离），workspace 里有没有 alicloud_instance 就是有没有机器。
#
#   只有一边有机器   → 那边是 active（在服务），另一边是 new（这次建它）
#   两边都没有       → 首次部署：new = blue
#   两边都有         → 上一次灰度没走完（建了新的没来得及二选一）。
#                      拒绝继续 —— 自动挑一边销毁是拿现网机器赌运气，
#                      这里宁可把流水线打红让人看一眼。
#
# 输出约定：环境变量行走 stdout（给 eval / artifact 用），
# 人类可读的日志一律走 stderr。混在一起没法 eval。
# ===========================================================================
set -euo pipefail

cd "$(dirname "$0")/../aliyun-ecs"

err() { echo "$*" >&2; }
die() { err "✗ $*"; exit 1; }

export TF_IN_AUTOMATION=true
terraform init -input=false -lock-timeout=5m >/dev/null

# default 工作区是历史遗留入口：它在灰度模型里没有语义。
# 里面有旧机器的话，流水线既不会更新它、也没法在 cutover 时回收它。
if terraform workspace select default >/dev/null 2>&1; then
    if terraform state list 2>/dev/null | grep -q '^alicloud_instance\.'; then
        die "default 工作区里还有旧机器的 state。灰度模型只认 blue/green 两个槽位，
    请先处理它（二选一）：
      · 机器不要了：  选中 default 工作区后执行 terraform 的 destroy（写法见流水线 teardown job）
      · 机器要保留：  把它当作现网，手工迁移到 blue（或先回收再由流水线重建）"
    fi
fi

exists_slot() {
    terraform workspace select "$1" >/dev/null 2>&1 \
        || terraform workspace new "$1" >/dev/null
    terraform state list 2>/dev/null | grep -q '^alicloud_instance\.'
}

blue=0 green=0
exists_slot blue  && blue=1  || true
exists_slot green && green=1 || true

active="" new=""

if [ "$blue" -eq 1 ] && [ "$green" -eq 1 ]; then
    die "blue 和 green 两个槽位都有机器 —— 上次灰度没走完（建了新的，没走到 cutover/rollback）。
    先看一眼哪台是上次想上的、哪台在服务：
        terraform workspace select blue  && terraform state list
        terraform workspace select green && terraform state list
    把**不在服务**的那台回收掉再重跑流水线（用 terraform 的 destroy 子命令，
    写法参考流水线里的 teardown / gray:rollback job）。
    （两台都拿不准的话，先 curl 各自的公网 IP，有版本标记的那台是在服务的。）"
elif [ "$blue" -eq 0 ] && [ "$green" -eq 0 ]; then
    new=blue
    err "── 首次部署：两个槽位都是空的，从 blue 开始"
else
    if [ "$blue" -eq 1 ]; then active=blue; new=green; else active=green; new=blue; fi
    err "── 在服务（active）：$active ；本次部署（new）：$new"
fi

echo "ACTIVE_SLOT=$active"
echo "NEW_SLOT=$new"
