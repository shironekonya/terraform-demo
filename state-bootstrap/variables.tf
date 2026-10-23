variable "region" {
  description = "后端资源所在 region（要和两份 backend.tf 里的 region 一致）"
  type        = string
  default     = "cn-hangzhou"
}

variable "bucket" {
  description = "state 桶名。全局唯一，被占用时换名（三处要一起改：本文件 + 两份 backend.tf）"
  type        = string
  default     = "shironeko-wf-tfstate"
}

variable "ots_instance" {
  description = "OTS 锁实例名。region 内唯一；backend.tf 的 tablestore_endpoint 由它推导"
  type        = string
  default     = "wf-tfstate"
}

variable "ots_table" {
  description = "锁表名。两份 backend.tf 的 tablestore_table 与它一致"
  type        = string
  default     = "tflock"
}
