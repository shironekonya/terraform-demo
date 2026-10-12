terraform {
  # 建议 >= 1.5，1.5+ 才支持 import block 与更完善的 moved/check 语法
  required_version = ">= 1.5.0"

  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = "~> 1.220"
    }
  }
}

provider "alicloud" {
  region = var.region

  # 凭证不要写死在代码里。按以下优先级任选其一：
  #   1) 环境变量：ALICLOUD_ACCESS_KEY / ALICLOUD_SECRET_KEY
  #   2) ~/.aliyun/config.json（aliyun configure 生成）
  #   3) 下方显式传入（不推荐，易泄露）
  # access_key = var.access_key
  # secret_key = var.secret_key

  # 若用 RAM 角色（如 ECS 实例内 / CI 中）：
  # assume_role { role_arn = "acs:ram::1234567890:role/tf-role" }
}
