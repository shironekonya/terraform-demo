terraform {
  required_version = ">= 1.5.0"

  # ---------------------------------------------------------------------------
  # 为什么不用 provider 去「接管」已存在的 VMware Workstation 虚机？
  #
  # 因为做不到。这是硬约束，不是我偷懒：
  #   1. VMware Workstation 没有官方 Terraform provider
  #      （HashiCorp 官方只维护 vsphere，面向 vCenter/ESXi）
  #   2. 用 null_resource 也无法 import —— import 的前提是存在对应的 resource
  #      类型且有 read 实现。null_resource 只是个「这段脚本跑过没有」的标记，
  #      它不持有虚机对象，所以没有任何东西能被接管进 state。
  #
  # 因此正确的分层是：
  #   虚机本身    -> 归 VMware Workstation 管（手工操作 / vmrun 脚本）
  #   容器化服务  -> 归 Terraform 管（本目录）
  #
  # 核心概念：Terraform 只能管它「创造得出来」或「有 provider 支持 read」
  # 的资源。管不了的东西，应该用 remote-exec 这种配置管理思路去碰，
  # 而不是假装能接管它 —— 后者会给你虚假的安全感。
  # ---------------------------------------------------------------------------

  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}

# 无需 provider 配置：全部通过 SSH 远程执行，不在本地留状态
