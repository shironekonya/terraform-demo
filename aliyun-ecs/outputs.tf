output "region" {
  description = "当前地域"
  value       = var.region
}

output "zone_id" {
  description = "实际使用的可用区"
  value       = local.zone_id
}

output "vpc_id" {
  description = "VPC ID"
  value       = alicloud_vpc.main.id
}

output "vswitch_id" {
  description = "交换机 ID"
  value       = alicloud_vswitch.main.id
}

output "security_group_id" {
  description = "安全组 ID"
  value       = alicloud_security_group.web.id
}

output "instance_ids" {
  description = "所有 ECS 实例 ID"
  value       = { for k, v in alicloud_instance.web : k => v.id }
}

output "instance_private_ips" {
  description = "内网 IP"
  value       = { for k, v in alicloud_instance.web : k => v.private_ip }
}

output "instance_public_ips" {
  description = "公网 IP（未分配公网带宽时为空）"
  value       = { for k, v in alicloud_instance.web : k => v.public_ip }
}

output "ssh_commands" {
  description = "直接可用的 SSH 连接命令（使用密钥对时）"
  # 该值来自敏感的 ssh_public_key 变量，Terraform 要求显式声明 sensitive。
  # 声明后 terraform output 默认不显示明文，需用 terraform output -json 查看。
  sensitive = true
  value = var.ssh_public_key != "" ? {
    for k, v in alicloud_instance.web :
    k => "ssh -i <你的私钥路径> root@${v.public_ip}"
  } : {}
}

output "web_urls" {
  description = "nginx 访问地址"
  value = {
    for k, v in alicloud_instance.web :
    k => "http://${v.public_ip}"
    if v.public_ip != ""
  }
}

output "summary" {
  description = "创建结果汇总"
  value = {
    for k, v in alicloud_instance.web : k => {
      id            = v.id
      name          = v.instance_name
      private_ip    = v.private_ip
      public_ip     = v.public_ip
      instance_type = v.instance_type
    }
  }
}
