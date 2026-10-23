# ===========================================================================
# 远程后端 —— OSS
#
# ★ 由 CI/147 的 bootstrap 启用：B4 在 --require-remote-state 下要求每个
#   在用模块都必须有远程后端，否则 validate 第一步就红。
#
# bucket 要提前存在（Terraform 不会替你建）。当前由 147 的
# RUNNER_147.local.sh 用机器上已有的 key 变量创建/校验：
#
#   aliyun oss mb oss://shironeko-wf-tfstate --region cn-hangzhou || true
#
# ===========================================================================

terraform {
  backend "oss" {
    bucket = "shironeko-wf-tfstate"
    prefix = "terraform-demo/aliyun-ecs"
    key    = "terraform.tfstate"
    region = "cn-hangzhou"
    acl    = "private"

    # --- 服务端加密：state 里有明文凭证，开着成本为零 -------------------
    encrypt = true

    # --- 状态锁（OSS 桶 + OTS 锁表由 state-bootstrap/ 模块创建） --------
    #   aliyun-ecs 与 vmware-workstation 共用同一把锁表，prefix 不同互不干扰。
    tablestore_endpoint = "https://wf-tfstate.cn-hangzhou.ots.aliyuncs.com"
    tablestore_table    = "tflock"
  }
}

# ---------------------------------------------------------------------------
# 凭证：不写在这里。优先级（从高到低）：
#   1) 环境变量 ALICLOUD_ACCESS_KEY / ALICLOUD_SECRET_KEY
#      —— CI 里由 /root/.wf-aliyun.env 提供（147 机器自带的 key 变量，
#         bootstrap 收敛进去；不进 GitLab）
#   2) ~/.aliyun/config.json
# ---------------------------------------------------------------------------
