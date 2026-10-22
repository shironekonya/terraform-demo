variable "region" {
  description = "阿里云地域"
  type        = string
  default     = "cn-hangzhou"
}

variable "environment" {
  description = "环境标识，用于统一命名和打标签"
  type        = string
  default     = "demo"
}

variable "vpc_cidr" {
  description = "VPC 网段"
  type        = string
  default     = "10.10.0.0/16"
}

variable "vswitch_cidr" {
  description = "交换机网段，必须是 VPC 网段的子集"
  type        = string
  default     = "10.10.1.0/24"
}

variable "zone_id" {
  description = "可用区。留空则自动取该地域第一个可用区"
  type        = string
  default     = ""
}

variable "instance_count" {
  description = "要创建的 ECS 数量"
  type        = number
  default     = 1

  validation {
    condition     = var.instance_count >= 1 && var.instance_count <= 10
    error_message = "instance_count 需在 1~10 之间（demo 用途，防止误开出大量实例）。"
  }
}

variable "instance_type" {
  description = "实例规格。ecs.e-c1m1.large 属经济型，适合 demo，按量付费成本低"
  type        = string
  default     = "ecs.e-c1m1.large"
}

variable "image_os" {
  description = "镜像的发行版族。留空 image_id 时按它查当前地域的最新构建。ubuntu_24_04 的系统 python 是 3.12，现代 ansible 可直接管理；alibaba_cloud_linux_3 的系统 python 是 3.6，只能用 ansible-core <= 2.16"
  type        = string
  default     = "ubuntu_24_04"

  validation {
    condition     = contains(["ubuntu_24_04", "alibaba_cloud_linux_3"], var.image_os)
    error_message = "image_os 只支持 ubuntu_24_04（默认）与 alibaba_cloud_linux_3。"
  }
}

variable "image_id" {
  description = "镜像 ID。非空则直接使用（钉死某一版镜像用这个）；留空则按 image_os 查询当前地域最新构建"
  type        = string
  default     = ""
}

variable "system_disk_category" {
  description = "系统盘类型"
  type        = string
  default     = "cloud_essd"
}

variable "system_disk_size" {
  description = "系统盘大小 GB"
  type        = number
  default     = 40
}

variable "internet_max_bandwidth_out" {
  description = "公网出带宽 Mbps。0 表示不分配公网 IP"
  type        = number
  default     = 5
}

variable "ssh_public_key" {
  description = "可选：SSH 公钥内容，传入则用密钥对登录而非密码"
  type        = string
  default     = ""
  sensitive   = true
}

variable "instance_password" {
  description = "可选：实例密码。8-30 位，须含大小写字母、数字、特殊符号中的三类"
  type        = string
  default     = ""
  sensitive   = true
}

variable "ssh_allowed_cidr" {
  description = "允许 SSH 的来源网段，建议收敛到自己的公网 IP/32"
  type        = string
  default     = "0.0.0.0/0"
}
