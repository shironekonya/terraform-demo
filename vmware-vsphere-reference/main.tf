# ===========================================================================
# 参考版本：VMware vSphere / ESXi + Terraform 官方 provider
#
# 用途：作为对照学习。如果你以后从 Workstation 迁到 vCenter/ESXi，
#       代码长这样，而且比 vmrun 方案规范得多（有真正的 state 追踪、
#       能拿到 IP、能做依赖编排）。
#
# 前提：需要 vCenter 或已开启 API 的 ESXi（ESXi 免费版锁了 API，不可用）
# ===========================================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    vsphere = {
      source  = "hashicorp/vsphere"
      version = "~> 2.8"
    }
  }
}

provider "vsphere" {
  user                 = var.vsphere_user
  password             = var.vsphere_password
  vsphere_server       = var.vsphere_server
  allow_unverified_ssl = true # 实验环境自签证书，生产请关掉
}

# ---------------------------------------------------------------------------
# 变量
# ---------------------------------------------------------------------------
variable "vsphere_user" {
  type      = string
  sensitive = true
  default   = "administrator@vsphere.local"
}

variable "vsphere_password" {
  type      = string
  sensitive = true
}

variable "vsphere_server" {
  type    = string
  default = "vcenter.lab.local"
}

variable "datacenter" {
  type    = string
  default = "DC"
}

variable "cluster" {
  type    = string
  default = "Cluster"
}

variable "datastore" {
  type    = string
  default = "datastore1"
}

variable "network" {
  type    = string
  default = "VM Network"
}

variable "template_name" {
  type    = string
  default = "ubuntu-2204-template"
}

variable "vm_count" {
  type    = number
  default = 2
}

variable "vm_prefix" {
  type    = string
  default = "tf-node"
}

variable "vm_cpus" {
  type    = number
  default = 2
}

variable "vm_memory_mb" {
  type    = number
  default = 2048
}

variable "vm_disk_gb" {
  type    = number
  default = 40
}

# ---------------------------------------------------------------------------
# 数据源：定位基础设施
# ---------------------------------------------------------------------------
data "vsphere_datacenter" "dc" {
  name = var.datacenter
}

data "vsphere_compute_cluster" "cluster" {
  name          = var.cluster
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_datastore" "ds" {
  name          = var.datastore
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_network" "net" {
  name          = var.network
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_virtual_machine" "template" {
  name          = var.template_name
  datacenter_id = data.vsphere_datacenter.dc.id
}

# ---------------------------------------------------------------------------
# 两台虚拟机
# ---------------------------------------------------------------------------
resource "vsphere_virtual_machine" "node" {
  count = var.vm_count

  name             = "${var.vm_prefix}-${format("%02d", count.index + 1)}"
  resource_pool_id = data.vsphere_compute_cluster.cluster.resource_pool_id
  datastore_id     = data.vsphere_datastore.ds.id
  folder           = var.datacenter
  num_cpus         = var.vm_cpus
  memory           = var.vm_memory_mb
  guest_id         = data.vsphere_virtual_machine.template.guest_id

  # 用 DHCP 省事；要做固定 IP 就换 static 块
  wait_for_guest_net_timeout = 5

  network_interface {
    network_id = data.vsphere_network.net.id
  }

  disk {
    label            = "disk0"
    size             = var.vm_disk_gb
    thin_provisioned = data.vsphere_virtual_machine.template.disks[0].thin_provisioned
    eagerly_scrub    = data.vsphere_virtual_machine.template.disks[0].eagerly_scrub
  }

  clone {
    template_uuid = data.vsphere_virtual_machine.template.id

    customize {
      linux_options {
        host_name = "${var.vm_prefix}-${format("%02d", count.index + 1)}"
        domain    = "lab.local"
      }
      network_interface {
        ipv4_address = cidrhost("10.20.0.0/24", count.index + 101)
        ipv4_netmask = 24
      }
      ipv4_gateway = "10.20.0.1"
    }
  }
}

# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
output "vms" {
  value = {
    for vm in vsphere_virtual_machine.node : vm.name => {
      id       = vm.id
      ipv4     = vm.default_ip_address
      cpus     = vm.num_cpus
      memory   = vm.memory
      hostname = vm.name
    }
  }
}
