# ===========================================================================
# 远程后端 —— OSS（与 aliyun-ecs 同一个桶，不同 prefix）
#
# "state 放哪"和"被管的资源在哪"是两件独立的事：本地虚机同样需要防并发覆盖。
# 完整说明见 backend.tf.example 与 aliyun-ecs/backend.tf。
#
# 状态锁：不配 OTS，并发保护靠 runner 的 concurrent = 1（同 aliyun-ecs）。
# ===========================================================================

terraform {
  backend "oss" {
    bucket = "cloud-native-demo"
    prefix = "terraform-demo/vmware-workstation"
    key    = "terraform.tfstate"
    region = "cn-hangzhou"
    acl    = "private"

    encrypt = true
  }
}
