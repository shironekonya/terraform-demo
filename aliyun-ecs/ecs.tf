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
  image_id          = local.resolved_image_id
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
  #
  # ★ 这段脚本要能跑在 Terraform 支持的两种发行版上，所以按**包管理器**分支，
  #   而不是按 os-release 里的名字判断 —— 少解析一层就少一处出错的地方。
  #   Ubuntu / Debian 走 apt，Alibaba Cloud Linux / RHEL 走 dnf/yum。
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    if command -v apt-get >/dev/null 2>&1; then
      export DEBIAN_FRONTEND=noninteractive
      # 首次启动时 cloud-init 可能正持有 dpkg 锁，等它而不是直接失败
      apt-get -o DPkg::Lock::Timeout=180 update -y
      apt-get -o DPkg::Lock::Timeout=180 install -y nginx
    elif command -v dnf >/dev/null 2>&1; then
      dnf install -y nginx
    elif command -v yum >/dev/null 2>&1; then
      yum install -y nginx
    fi
    systemctl enable --now nginx
    # ★ 两种发行版的默认站点根目录不同：Debian/Ubuntu 是 /var/www/html，
    #   RHEL 系（含 Alibaba Cloud Linux）是 /usr/share/nginx/html。
    #   只写一个路径的话，另一种发行版上打开 80 看到的是发行版自带欢迎页，
    #   而「看到 hello from ...」正是这一层的验收判据 —— 它会变成假通过。
    page="hello from ${each.key} - created by Terraform"
    for d in /var/www/html /usr/share/nginx/html; do
      mkdir -p "$d"
      printf '%s\n' "$page" > "$d/index.html"
    done
  EOT

  tags = merge(local.common_tags, {
    Name  = "${local.name_prefix}-ecs-${each.key}"
    Index = each.key
  })

  lifecycle {
    # 只忽略 user_data：改脚本不该把机器重建掉（重建会换公网 IP、丢数据）。
    # 脚本改动要生效，得手动重置实例，或 terraform apply -replace。
    #
    # ★ image_id 不在这里忽略 —— 换镜像等于换操作系统，必须重建实例：
    #   同一个 user_data 在 apt 与 dnf 上走的路径不同，硬留着旧机器只会留下
    #   一台"配置说是 Ubuntu、实际还是 ALinux3"的机器。让 plan 明着说要重建，
    #   比悄悄按旧镜像继续跑更好。
    ignore_changes = [user_data]

    precondition {
      condition     = local.resolved_image_id != null
      error_message = "没查到可用镜像：region=${var.region} / image_os=${var.image_os}。确认该地域存在这个系列，或直接在 tfvars 里指定 image_id。"
    }
  }
}
