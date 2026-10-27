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
# ★ 桶名为什么带 -tfstate 后缀：OSS 命名空间全站共享，裸名 cloud-native-demo
#   实测在 cn-hangzhou 已被别的账号占用（403，terraform import 也拿不下来）。
#   换名要四处同步：本文件、vmware-workstation/backend.tf、
#   state-bootstrap/variables.tf、RUNNER_147.local.sh 的 BUCKET。
#
# 状态锁：**不配 OTS**。并发保护由执行器保证 —— runner 的 concurrent = 1，
#   同一时刻只有一个 job 在 apply。加第二个 runner 之前必须补锁
#   （tablestore_endpoint + tablestore_table，见 backend.tf.example）。
# ===========================================================================

terraform {
  backend "oss" {
    bucket = "cloud-native-demo-tfstate"
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
