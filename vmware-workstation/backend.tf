# ===========================================================================
# 远程后端 —— OSS（与 aliyun-ecs 同一个桶，不同 prefix）
#
# "state 放哪"和"被管的资源在哪"是两件独立的事：本地虚机同样需要防并发覆盖。
# 完整说明见 backend.tf.example 与 aliyun-ecs/backend.tf。
# ===========================================================================

terraform {
  backend "oss" {
    bucket = "shironeko-wf-tfstate"
    prefix = "terraform-demo/vmware-workstation"
    key    = "terraform.tfstate"
    region = "cn-hangzhou"
    acl    = "private"

    encrypt = true

    # 锁：与 aliyun-ecs 共用同一把（资源由 state-bootstrap/ 创建）
    tablestore_endpoint = "https://wf-tfstate.cn-hangzhou.ots.aliyuncs.com"
    tablestore_table    = "tflock"
  }
}
