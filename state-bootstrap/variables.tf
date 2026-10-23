variable "region" {
  description = "后端资源所在 region（要和两份 backend.tf 里的 region 一致）"
  type        = string
  default     = "cn-hangzhou"
}

variable "bucket" {
  description = "state 桶名。全局唯一，被占用时换名（三处要一起改：本文件 + 两份 backend.tf）"
  type        = string
  default     = "cloud-native-demo"
}
