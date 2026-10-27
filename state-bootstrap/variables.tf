variable "region" {
  description = "后端资源所在 region（要和两份 backend.tf 里的 region 一致）"
  type        = string
  default     = "cn-hangzhou"
}

variable "bucket" {
  description = "state 桶名。全局唯一，被占用时换名（四处要一起改：本文件 + 两份 backend.tf + RUNNER_147.local.sh）"
  type        = string
  # ★ 为什么带 -tfstate 后缀：裸名 cloud-native-demo 实测在 cn-hangzhou 已被
  #   别的账号占用（403 AccessDenied、import 也拿不下来）。OSS 命名空间全站共享，
  #   短而通用的名字被抢是常态 —— 与其猜，不如一开始就用带用途后缀的名字。
  default     = "cloud-native-demo-tfstate"
}
