# ---------------------------------------------------------------------------
# 前置校验：在动任何东西之前，先把「必填项是否合理」检查掉
# 这样能把错误提前暴露，而不是等 clone 到一半才失败
# ---------------------------------------------------------------------------
locals {
  # 统一用正斜杠，避免 Windows 反斜杠在 shell 里被当成转义符
  vmrun = replace(var.vmrun_path, "\\", "/")
  tpl   = replace(var.template_vmx, "\\", "/")
  cdir  = replace(var.clone_dir, "\\", "/")

  # 生成每台虚机的目标路径
  vms = {
    for name in var.vm_names : name => {
      name = name
      dir  = "${local.cdir}/${name}"
      vmx  = "${local.cdir}/${name}/${name}.vmx"
    }
  }
}

# 校验 vmrun 存在
resource "null_resource" "precheck_vmrun" {
  triggers = {
    vmrun = local.vmrun
  }

  lifecycle {
    precondition {
      condition     = fileexists(var.vmrun_path)
      error_message = "找不到 vmrun.exe：${var.vmrun_path}。请确认 VMware Workstation 已安装，或修改 vmrun_path 变量指向实际路径。"
    }
  }
}

# 校验模板 .vmx 存在
resource "null_resource" "precheck_template" {
  triggers = {
    template = local.tpl
  }

  depends_on = [null_resource.precheck_vmrun]

  lifecycle {
    precondition {
      condition     = fileexists(var.template_vmx)
      error_message = "找不到模板虚拟机：${var.template_vmx}。请先手动创建一台模板虚拟机，并把路径填入 template_vmx 变量。"
    }
  }
}
