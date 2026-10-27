variable "region" {
  description = "后端资源所在 region（要和两份 backend.tf 里的 region 一致）"
  type        = string
  default     = "cn-hangzhou"
}

variable "bucket" {
  description = "state 桶名。全局唯一，被占用时换名（四处要一起改：本文件 + 两份 backend.tf + RUNNER_147.local.sh）"
  type        = string
  # ★ 名字由使用者指定（cloud-native-demo），不自动加后缀。
  #   若 import 报 AccessDenied / no right，是 AK 的 RAM 用户权限不够；
  #   只有报 does not belong to you 才是桶在别的账号里 —— 那才需要换名。
  default     = "cloud-native-demo"
}
