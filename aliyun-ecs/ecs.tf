# ---------------------------------------------------------------------------
# SSH 密钥对（仅当传入了 ssh_public_key 时创建）
# ---------------------------------------------------------------------------
resource "alicloud_ecs_key_pair" "this" {
  count = var.ssh_public_key != "" ? 1 : 0

  key_pair_name = "${local.name_prefix}-key"
  public_key    = var.ssh_public_key

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# ECS 实例
# 用 for_each 而不是 count：以后删中间某台时不会导致后面全部重建
# ---------------------------------------------------------------------------
resource "alicloud_instance" "web" {
  for_each = toset([for i in range(var.instance_count) : tostring(i + 1)])

  instance_name     = "${local.name_prefix}-ecs-${each.key}"
  description       = "demo instance managed by Terraform"
  host_name         = "demo-ecs-${each.key}"
  instance_type     = var.instance_type
  image_id          = var.image_id
  availability_zone = local.zone_id

  vswitch_id      = alicloud_vswitch.main.id
  security_groups = [alicloud_security_group.web.id]

  system_disk_category = var.system_disk_category
  system_disk_size     = var.system_disk_size

  internet_max_bandwidth_out = var.internet_max_bandwidth_out
  internet_charge_type       = var.internet_max_bandwidth_out > 0 ? "PayByTraffic" : null

  # 二选一：优先用密钥对，其次用密码；两者都没给则后续用控制台重置凭证
  key_name = var.ssh_public_key != "" ? alicloud_ecs_key_pair.this[0].key_pair_name : null
  password = var.ssh_public_key == "" && var.instance_password != "" ? var.instance_password : null

  # 按量付费，学习 demo 用完即释放，避免包年包月退订麻烦
  instance_charge_type = "PostPaid"

  # 关闭释放保护，方便 terraform destroy 一把清干净
  deletion_protection = false

  # 实例创建后自动下发一段 init 脚本，装个 nginx 验证可用性
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    if command -v dnf >/dev/null 2>&1; then
      dnf install -y nginx
      systemctl enable --now nginx
    elif command -v yum >/dev/null 2>&1; then
      yum install -y nginx
      systemctl enable --now nginx
    fi
    echo "hello from ${each.key} - created by Terraform" > /usr/share/nginx/html/index.html
  EOT

  tags = merge(local.common_tags, {
    Name  = "${local.name_prefix}-ecs-${each.key}"
    Index = each.key
  })

  lifecycle {
    # 阿里云没有 user_data_replace_on_change 这类参数（那是 AWS 的写法）。
    # 这里的做法是：忽略 user_data 与 image_id 的变化，避免改动脚本或镜像时
    # 直接重建实例（重建会换 IP、丢数据）。需要生效时再手动重置实例。
    ignore_changes = [image_id, user_data]
  }
}
