# ---------------------------------------------------------------------------
# 镜像选择
#
# 两种用法，优先级从高到低：
#   1) image_id 非空  → 直接用（想钉死某一版镜像时填这个，plan 最稳定）
#   2) image_id 为空  → 按 image_os 在当前地域查最新版
#
# ★ 为什么默认是 Ubuntu 而不是 Alibaba Cloud Linux 3
#
#   ALinux3 的 /usr/bin/python3 是 3.6，而 dnf 自身的脚本就挂在这个解释器上，
#   官方文档明确不建议替换。ansible-core 2.17 起要求目标机 Python >= 3.7
#   （2.18+ 要求 >= 3.8），于是用最新版 ansible 连上去，gather_facts 第一步就失败：
#
#     SyntaxError: future feature annotations is not defined
#
#   Ubuntu 24.04 的系统 python 是 3.12，不需要为 Ansible 做任何妥协。
#   要继续用 ALinux3：把 image_os 改成 alibaba_cloud_linux_3，
#   并把 ansible-core 钉在 2.16（见 ansible/requirements.txt 里写的原因）。
#
# ★ 为什么用正则查而不是写死镜像 ID
#
#   阿里云会持续发布同名新构建（只有日期后缀变），写死的 ID 过一阵就查不到新版；
#   写正则前缀能一直查到当前地域的最新构建。代价是：**镜像有新版时 plan 会显示重建**。
#   如果要求 plan 恒为空，先 apply 一次，把 output image_id_used 的值填进
#   terraform.tfvars 的 image_id —— 之后就固定在这一版上。
# ---------------------------------------------------------------------------

locals {
  image_name_regex = {
    ubuntu_24_04          = "^ubuntu_24_04_x64_20G_alibase_"
    alibaba_cloud_linux_3 = "^aliyun_3_x64_20G_alibase_"
  }

  # try() 兜一层：查不到时走 ecs.tf 里 precondition 的中文提示，
  # 而不是 provider 抛一句 "Invalid index"。
  resolved_image_id = (
    var.image_id != "" ? var.image_id : try(data.alicloud_images.this[0].images[0].id, null)
  )
}

data "alicloud_images" "this" {
  count = var.image_id == "" ? 1 : 0

  # owners = system 表示只要公共镜像（阿里云官方），不要自建/共享/市场镜像
  owners      = "system"
  name_regex  = local.image_name_regex[var.image_os]
  most_recent = true
}
