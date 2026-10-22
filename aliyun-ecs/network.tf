# ---------------------------------------------------------------------------
# 可用区：如果没指定 zone_id，就自动取当前地域的第一个可用区
# ---------------------------------------------------------------------------
data "alicloud_zones" "available" {
  available_resource_creation = "VSwitch"
}

locals {
  zone_id = var.zone_id != "" ? var.zone_id : data.alicloud_zones.available.zones[0].id

  # 统一命名前缀：demo-hangzhou-xxx
  name_prefix = "${var.environment}-${replace(var.region, "-", "")}"

  common_tags = {
    Environment = var.environment
    ManagedBy   = "Terraform"
    Project     = "cloud-vmware-demo"
  }
}

# ---------------------------------------------------------------------------
# VPC + 交换机
# ---------------------------------------------------------------------------
resource "alicloud_vpc" "main" {
  vpc_name   = "${local.name_prefix}-vpc"
  cidr_block = var.vpc_cidr

  tags = local.common_tags
}

resource "alicloud_vswitch" "main" {
  vpc_id       = alicloud_vpc.main.id
  cidr_block   = var.vswitch_cidr
  zone_id      = local.zone_id
  vswitch_name = "${local.name_prefix}-vswitch"

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# 安全组
# 注意：阿里云安全组有默认 deny all 的入方向策略，这里只开必要端口
# ---------------------------------------------------------------------------
resource "alicloud_security_group" "web" {
  security_group_name = "${local.name_prefix}-sg"
  description         = "demo security group managed by Terraform"
  vpc_id              = alicloud_vpc.main.id

  tags = local.common_tags
}

# SSH
resource "alicloud_security_group_rule" "ssh" {
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "intranet"
  policy            = "accept"
  port_range        = "22/22"
  priority          = 1
  security_group_id = alicloud_security_group.web.id
  cidr_ip           = var.ssh_allowed_cidr
  description       = "SSH"
}

# HTTP
resource "alicloud_security_group_rule" "http" {
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "intranet"
  policy            = "accept"
  port_range        = "80/80"
  priority          = 10
  security_group_id = alicloud_security_group.web.id
  cidr_ip           = "0.0.0.0/0"
  description       = "HTTP"
}

# HTTPS
resource "alicloud_security_group_rule" "https" {
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "intranet"
  policy            = "accept"
  port_range        = "443/443"
  priority          = 10
  security_group_id = alicloud_security_group.web.id
  cidr_ip           = "0.0.0.0/0"
  description       = "HTTPS"
}

# 容器栈端口
# Ansible 的 app_stack role 把主机 wf_stack_port（默认 8080）映射到容器 80。
# 80 已经被 user_data 装的系统 nginx 占用，所以栈刻意错开 —— 两处端口必须一起看：
# 这里是安全组，ansible/inventory/group_vars/cloud.yml 里是 wf_stack_port。
resource "alicloud_security_group_rule" "app_stack" {
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "intranet"
  policy            = "accept"
  port_range        = "${var.app_stack_port}/${var.app_stack_port}"
  priority          = 11
  security_group_id = alicloud_security_group.web.id
  cidr_ip           = "0.0.0.0/0"
  description       = "app_stack"
}

# ICMP：方便 ping 排障
resource "alicloud_security_group_rule" "icmp" {
  type              = "ingress"
  ip_protocol       = "icmp"
  nic_type          = "intranet"
  policy            = "accept"
  port_range        = "-1/-1"
  priority          = 20
  security_group_id = alicloud_security_group.web.id
  cidr_ip           = var.vpc_cidr
  description       = "ICMP from VPC only"
}
