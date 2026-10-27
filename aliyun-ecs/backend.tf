# ===========================================================================
# 远程后端 —— OSS
#
# ★ 由 CI/147 的 bootstrap 启用：B4 在 --require-remote-state 下要求每个
#   在用模块都必须有远程后端，否则 validate 第一步就红。
#
# bucket 由 147 的 RUNNER_147.local.sh 通过 state-bootstrap/ 模块创建：
#
#   terraform -chdir=state-bootstrap apply
#
# ★ 桶名 cloud-native-demo（使用者指定的名字，不改）。
#   OSS 命名空间跨账号全局共享，匿名探测只能看到「桶在不在」（403/404），
#   看不到 owner —— 所以「403 且 import 失败」有两种完全不同的原因：
#     · AK 所在账号/RAM 用户对该桶没权限  -> 补权限，名字不用动
#     · 桶真的属于别的账号                 -> 才需要换名（四处同步）
#   脚本第 3 步会把 terraform import 的原话打出来，按原话判读，别猜。
#   换名要四处同步：本文件、vmware-workstation/backend.tf、
#   state-bootstrap/variables.tf、RUNNER_147.local.sh 的 BUCKET。
#
# 状态锁：**不配 OTS**。并发保护由执行器保证 —— runner 的 concurrent = 1，
#   同一时刻只有一个 job 在 apply。加第二个 runner 之前必须补锁
#   （tablestore_endpoint + tablestore_table，见 backend.tf.example）。
# ===========================================================================

terraform {
  backend "oss" {
    bucket = "cloud-native-demo"
    prefix = "terraform-demo/aliyun-ecs"
    key    = "terraform.tfstate"
    region = "cn-hangzhou"
    acl    = "private"

    # --- 服务端加密：state 里有明文凭证，开着成本为零 -------------------
    encrypt = true
  }
}

# ---------------------------------------------------------------------------
# 凭证：不写在这里。优先级（从高到低）：
#   1) 环境变量 ALICLOUD_ACCESS_KEY / ALICLOUD_SECRET_KEY
#      —— CI 里由 /root/.wf-aliyun.env 提供（147 机器自带的 key 变量，
#         bootstrap 收敛进去；不进 GitLab）
#   2) ~/.aliyun/config.json
# ---------------------------------------------------------------------------
