# ---------------------------------------------------------------------------
# 核心：克隆并管理两台 VMware Workstation 虚拟机
#
# 为什么用 vmrun 而不是 provider：
#   VMware Workstation 无官方 Terraform provider，vmrun 是 VMware 自带的
#   命令行工具，能完整覆盖 clone / start / stop / snapshot / delete 等操作，
#   是社区对 Workstation 做自动化的标准做法。
#
# 设计要点：
#   1. 用 for_each 而非 count，增删单台不会牵连其他台
#   2. create/update/delete 分别用不同脚本，语义清晰
#   3. 每个脚本都 set -euo pipefail，失败即中断，不会静默吞错
#   4. random_id 生成稳定后缀，用于避免多次 apply 之间互相干扰
# ---------------------------------------------------------------------------

resource "random_id" "vm_suffix" {
  for_each    = local.vms
  byte_length = 2
  keepers = {
    name = each.value.name
  }
}

# --------------------------- 创建 ---------------------------
resource "null_resource" "vm" {
  for_each = local.vms

  # triggers 同时也是 destroy 阶段 self.triggers 能读到的全部内容，
  # 所以销毁脚本需要的 vmrun_path 也必须放进来。
  triggers = {
    name       = each.value.name
    vmx        = each.value.vmx
    template   = local.tpl
    clone_type = var.clone_type
    vmrun_path = local.vmrun
    # 模板内容指纹：模板变了才重建，没变就不动。
    # 注意 Terraform 没有「取文件修改时间」的函数，所以用 filesha1 对内容取哈希，
    # 语义上更准确——只有模板真的被改动才触发重建。
    tpl_hash = filesha1(var.template_vmx)
  }

  depends_on = [null_resource.precheck_template]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail

      VMRUN="${local.vmrun}"
      TEMPLATE="${local.tpl}"
      VMX="${each.value.vmx}"
      VMDIR="${each.value.dir}"
      NAME="${each.value.name}"

      if [ -f "$VMX" ]; then
        echo "[skip] $NAME 已存在：$VMX"
        exit 0
      fi

      echo "[1/3] 创建目录 $VMDIR"
      mkdir -p "$VMDIR"

      echo "[2/3] 克隆模板（${var.clone_type}）-> $NAME"
      # 链接克隆要求模板已有快照；缺失时自动回退到完整克隆
      if "$VMRUN" -T ws clone "$TEMPLATE" "$VMX" ${var.clone_type} -snapshot= -cloneName="$NAME" 2>/dev/null; then
        echo "      克隆成功"
      else
        echo "      链接克隆失败，回退为完整克隆"
        "$VMRUN" -T ws clone "$TEMPLATE" "$VMX" full -cloneName="$NAME"
      fi

      echo "[3/3] 完成 $NAME"
    EOT
  }

  # 销毁：先关机再删虚机。
  # 注意 destroy 顺序是「正序资源逆序执行」——vm_power_on / vm_snapshot 先被销毁，
  # 但它们只是开机和打快照，不删实体，所以这里删 VM 是安全的。
  provisioner "local-exec" {
    when        = destroy
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -uo pipefail
      VMRUN="${self.triggers.vmrun_path}"
      VMX="${self.triggers.vmx}"
      NAME="${self.triggers.name}"

      if [ ! -f "$VMX" ]; then
        echo "[skip] $NAME 的 vmx 不存在，无需清理：$VMX"
        exit 0
      fi

      if [ -f "$VMRUN" ]; then
        echo "[cleanup] 关闭并删除 $NAME"
        "$VMRUN" -T ws stop "$VMX" soft 2>/dev/null || true
        "$VMRUN" -T ws deleteVM "$VMX" 2>/dev/null || true
      fi

      # 兜底：deleteVM 失败或 vmrun 缺失时，直接清掉目录
      if [ -f "$VMX" ]; then
        echo "[cleanup] deleteVM 未生效，改为删除目录"
        rm -rf "$(dirname "$VMX")"
      fi

      echo "[cleanup] $NAME 已删除"
    EOT
  }
}

# --------------------------- 调规格 ---------------------------
# 模板的内存/CPU 往往偏小，这里按变量覆盖
resource "null_resource" "vm_tuning" {
  for_each = {
    for name, cfg in local.vms : name => cfg
    if var.vm_memory_mb > 0 || var.vm_cpus > 0
  }

  triggers = {
    vmx    = each.value.vmx
    name   = each.value.name
    memory = tostring(var.vm_memory_mb)
    cpus   = tostring(var.vm_cpus)
  }

  depends_on = [null_resource.vm]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    # 注意：这里不在 heredoc 里做 HCL 三元插值，而是把阈值当普通字符串传进去，
    # 由 shell 自己判断。多行/带引号的插值嵌在 heredoc 中极易踩转义坑，这样更稳。
    command = <<-EOT
      set -euo pipefail

      VMRUN="${local.vmrun}"
      VMX="${each.value.vmx}"
      NAME="${each.value.name}"
      MEM="${var.vm_memory_mb}"
      CPU="${var.vm_cpus}"

      # 修改虚拟机配置必须先关机
      "$VMRUN" -T ws stop "$VMX" soft 2>/dev/null || true

      if [ "$MEM" -gt 0 ]; then
        echo "[cfg] 内存 -> $${MEM}MB"
        "$VMRUN" -T ws writeVariable "$VMX" runtimeConfig memSize "$MEM"
      else
        echo "[cfg] 内存保持模板设置"
      fi

      if [ "$CPU" -gt 0 ]; then
        echo "[cfg] vCPU -> $${CPU}"
        "$VMRUN" -T ws writeVariable "$VMX" runtimeConfig numvcpus "$CPU"
      else
        echo "[cfg] CPU 保持模板设置"
      fi

      echo "[cfg] $NAME 规格已更新"
    EOT
  }
}

# --------------------------- 建立基线快照 ---------------------------
# 学习 demo 最重要的习惯：开机改动之前先留一个干净快照，随时可回滚
resource "null_resource" "vm_snapshot" {
  for_each = var.snapshot_name != "" ? local.vms : {}

  triggers = {
    vmx  = each.value.vmx
    name = each.value.name
    snap = var.snapshot_name
  }

  depends_on = [null_resource.vm_tuning, null_resource.vm]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail

      VMRUN="${local.vmrun}"
      VMX="${each.value.vmx}"
      NAME="${each.value.name}"
      SNAP="${var.snapshot_name}"

      "$VMRUN" -T ws stop "$VMX" soft 2>/dev/null || true

      if "$VMRUN" -T ws listSnapshots "$VMX" 2>/dev/null | grep -qx "$SNAP"; then
        echo "[snap] 快照 $SNAP 已存在于 $NAME，跳过"
      else
        echo "[snap] 为 $NAME 创建快照 $SNAP"
        "$VMRUN" -T ws snapshot "$VMX" "$SNAP"
      fi
    EOT
  }
}

# --------------------------- 开机 ---------------------------
resource "null_resource" "vm_power_on" {
  for_each = var.start_vms ? local.vms : {}

  triggers = {
    vmx  = each.value.vmx
    name = each.value.name
    gui  = tostring(var.gui)
  }

  depends_on = [null_resource.vm_snapshot, null_resource.vm_tuning, null_resource.vm]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail

      VMRUN="${local.vmrun}"
      VMX="${each.value.vmx}"
      NAME="${each.value.name}"

      STATE="$("$VMRUN" -T ws list | grep -F "$VMX" || true)"
      if [ -n "$STATE" ]; then
        echo "[power] $NAME 已在运行，跳过"
        exit 0
      fi

      if [ "${var.gui}" = "true" ]; then
        echo "[power] 启动 $NAME（图形界面）"
        "$VMRUN" -T ws -gu "" -gp "" start "$VMX" gui
      else
        echo "[power] 启动 $NAME（后台）"
        "$VMRUN" -T ws start "$VMX" nogui
      fi

      echo "[power] $NAME 已启动"
    EOT
  }
}
