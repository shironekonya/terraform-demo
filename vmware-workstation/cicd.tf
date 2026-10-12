# ===========================================================================
# CI/CD 流水线接管（第三阶段）
#
# 你已有的链路：
#
#   git push
#      |
#      v
#   Jenkins 构建 ----推送镜像----> Harbor
#                                   |
#                                   | PUSH_ARTIFACT
#                                   v
#                             Harbor webhook
#                                   |
#                                   v
#                             Jenkins 部署任务
#
# 这一层把上面「配置关系」变成代码：Harbor 项目、Harbor webhook、Jenkins 任务。
#
# 与前一层的区别：
#   containers.tf 管「进程在不在跑」
#   cicd.tf       管「流水线怎么连起来」
#
# ★ 本文件最重要的设计是 webhook_event_types 的取值限制。
#   监听 PULL_ARTIFACT 会造成部署死循环，详见 variables.tf 中的说明。
# ===========================================================================

locals {
  do_cicd = var.manage_cicd

  # ---------------------------------------------------------------------------
  # 脚本配置
  #
  # 整份配置打包成 JSON，base64 编码后替换脚本里的单个占位符。
  # 为什么用 base64：字符集只有 [A-Za-z0-9+/=]，不含引号/反斜杠/换行/$，
  # 既不用转义，也不可能意外截断 Python 字符串字面量。
  #
  # 对比旧写法：原来 Harbor 脚本要 7 个 replace()、Jenkins 任务脚本要 6 个，
  # 每个字段一个占位符，还得保证占位符之间不构成前缀关系。现在每个脚本一个。
  #
  # 用 jsonencode 而不是手工拼 JSON：Harbor 密码、Jenkins Token 里出现
  # 引号或反斜杠时，手工拼会产出非法 JSON，而报错只有「400 Bad Request」。
  # ---------------------------------------------------------------------------
  harbor_config = {
    api          = var.harbor_url
    project      = var.harbor_project
    user         = var.harbor_admin_user
    password     = var.harbor_admin_password
    webhook_name = var.harbor_webhook_name
    endpoint     = var.webhook_endpoint_url
    auth_header  = var.webhook_auth_header
    event_types  = var.webhook_event_types
  }

  harbor_script = replace(
    file("${path.module}/scripts/harbor-webhook.py"),
    "__WF_CONFIG_B64__",
    base64encode(jsonencode(local.harbor_config)),
  )

  jenkins_job_config = {
    jenkins_url = var.jenkins_url
    user        = var.jenkins_admin_user
    token       = var.jenkins_api_token
    job_name    = var.jenkins_deploy_job_name
    repo_url    = var.jenkins_job_repo_url
    branch      = var.jenkins_job_branch
    jenkinsfile = var.jenkinsfile_path
  }

  jenkins_job_script = replace(
    file("${path.module}/scripts/jenkins-job.py"),
    "__WF_CONFIG_B64__",
    base64encode(jsonencode(local.jenkins_job_config)),
  )
}

# ---------------------------------------------------------------------------
# Harbor 项目 + Webhook 策略
#
# 这一步是整条链路里最容易手工配错、也最难排查的环节，最值得变成代码。
# ---------------------------------------------------------------------------
resource "null_resource" "harbor_webhook" {
  count = local.do_cicd ? 1 : 0

  triggers = {
    project      = var.harbor_project
    harbor_url   = var.harbor_url
    webhook_name = var.harbor_webhook_name
    endpoint     = var.webhook_endpoint_url
    events       = join(",", var.webhook_event_types)
    server_host  = local.server.host
    ssh_user     = local.server.user
    ssh_key      = local.server.ssh_key
    ssh_port     = tostring(var.ssh_port)
    # 脚本内容哈希 + 配置哈希：任一变化都要重跑
    script_hash = filesha1("${path.module}/scripts/harbor-webhook.py")
    config_hash = sha1(jsonencode(local.harbor_config))

    # 下面两项只有销毁脚本用得到。
    # 再次提醒：destroy provisioner 只能读 self.triggers，不能读 var，
    # 所以销毁需要的参数必须预先放进来。
    cleanup     = tostring(var.cicd_cleanup_on_destroy)
    harbor_pass = var.harbor_admin_password
  }

  # 快速失败：缺关键配置就报错，而不是等脚本跑到一半才失败
  lifecycle {
    precondition {
      condition     = var.webhook_endpoint_url != ""
      error_message = "manage_cicd = true 时必须设置 webhook_endpoint_url，否则 Harbor 不知道该把事件推到哪里。"
    }

    precondition {
      condition     = var.harbor_admin_password != ""
      error_message = "manage_cicd = true 时必须设置 harbor_admin_password（Harbor 管理员密码）。"
    }

    precondition {
      condition     = !contains(var.webhook_event_types, "PULL_ARTIFACT")
      error_message = "不能监听 PULL_ARTIFACT：部署任务自身要拉镜像，会造成 webhook 无限循环。"
    }
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]

    # 把渲染好的脚本通过环境变量传进去，避免在 heredoc 里做转义。
    # 脚本里有大量 $ 和引号，直接内联进 command 会一团糟。
    environment = {
      HARBOR_SCRIPT = local.harbor_script
    }

    command = <<-EOT
      set -euo pipefail
      echo "===== 同步 Harbor 项目与 webhook 策略 ====="

      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=yes \
               -i "${local.server.ssh_key}" -p ${var.ssh_port} \
               "${local.server.user}@${local.server.host}" 'command -v python3' >/dev/null 2>&1; then
        echo "  ✗ 主节点上没有 python3，无法执行同步脚本" >&2
        echo "     安装：sudo apt-get install -y python3   （Ubuntu / Debian）" >&2
        exit 1
      fi

      printf '%s' "$HARBOR_SCRIPT" | ssh \
        -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 \
        -i "${local.server.ssh_key}" -p ${var.ssh_port} \
        "${local.server.user}@${local.server.host}" 'python3 -u -'

      echo "===== Harbor 同步完成 ====="
    EOT
  }

  # -------------------------------------------------------------------------
  # 销毁：默认什么都不做（cicd_cleanup_on_destroy = false）
  #
  # 理由见变量说明 —— 你的 webhook 是手工配好的、流水线正靠它跑着，
  # Terraform 一 destroy 就删掉会直接打断业务。
  # 所以默认只提示，不动手。
  # -------------------------------------------------------------------------
  provisioner "local-exec" {
    when        = destroy
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -uo pipefail
      if [ "${self.triggers.cleanup}" != "true" ]; then
        echo "[keep] Harbor webhook「${self.triggers.webhook_name}」按配置保留，未删除。"
        echo "       如需删除，请到 Harbor 界面手工操作，或把 cicd_cleanup_on_destroy 设为 true。"
        exit 0
      fi

      echo "[cleanup] 删除 Harbor webhook「${self.triggers.webhook_name}」"
      ID=$(curl -s -k -u "admin:${self.triggers.harbor_pass}" \
        "${self.triggers.harbor_url}/api/v2.0/projects/${self.triggers.project}/webhook/policies?page_size=100" \
        | tr '}' '\n' | grep -F "\"name\":\"${self.triggers.webhook_name}\"" \
        | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -1)

      if [ -n "$ID" ]; then
        curl -s -k -X DELETE -u "admin:${self.triggers.harbor_pass}" \
          "${self.triggers.harbor_url}/api/v2.0/projects/${self.triggers.project}/webhook/policies/$ID" >/dev/null
        echo "[cleanup] 已删除 webhook id=$ID"
      else
        echo "[cleanup] 未找到该 webhook，无需删除"
      fi
    EOT
  }
}

# ---------------------------------------------------------------------------
# Jenkins 部署任务（可选）
#
# 默认关闭：Jenkins 任务更适合由 Pipeline-as-Code 管理 ——
# Jenkinsfile 放在应用仓库里，任务只做「指向仓库的壳」。
# 如果你已手工建好任务，保持 manage_jenkins_job = false 即可。
# ---------------------------------------------------------------------------
resource "null_resource" "jenkins_job" {
  count = local.do_cicd && var.manage_jenkins_job ? 1 : 0

  triggers = {
    jenkins_url = var.jenkins_url
    job_name    = var.jenkins_deploy_job_name
    repo_url    = var.jenkins_job_repo_url
    branch      = var.jenkins_job_branch
    server_host = local.server.host
    ssh_user    = local.server.user
    ssh_key     = local.server.ssh_key
    ssh_port    = tostring(var.ssh_port)
    script_hash = filesha1("${path.module}/scripts/jenkins-job.py")
    config_hash = sha1(jsonencode(local.jenkins_job_config))
  }

  depends_on = [null_resource.harbor_webhook]

  lifecycle {
    precondition {
      condition     = var.jenkins_job_repo_url != ""
      error_message = "manage_jenkins_job = true 时必须设置 jenkins_job_repo_url（部署任务的 Git 仓库地址）。"
    }

    precondition {
      condition     = var.jenkins_api_token != ""
      error_message = "manage_jenkins_job = true 时必须设置 jenkins_api_token（Jenkins API Token，不是登录密码）。"
    }
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      JENKINS_JOB_SCRIPT = local.jenkins_job_script
    }

    command = <<-EOT
      set -euo pipefail
      echo "===== 同步 Jenkins 部署任务 ====="

      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=yes \
               -i "${local.server.ssh_key}" -p ${var.ssh_port} \
               "${local.server.user}@${local.server.host}" 'command -v python3' >/dev/null 2>&1; then
        echo "  ✗ 主节点上没有 python3，无法执行同步脚本" >&2
        echo "     安装：sudo apt-get install -y python3   （Ubuntu / Debian）" >&2
        exit 1
      fi

      printf '%s' "$JENKINS_JOB_SCRIPT" | ssh \
        -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 \
        -i "${local.server.ssh_key}" -p ${var.ssh_port} \
        "${local.server.user}@${local.server.host}" 'python3 -u -'

      echo "===== Jenkins 任务同步完成 ====="
    EOT
  }
}

# ---------------------------------------------------------------------------
# 未启用时的提示
# ---------------------------------------------------------------------------
resource "null_resource" "cicd_notice" {
  count = local.do_cicd ? 0 : 1

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      echo "----------------------------------------------------------------"
      echo "CI/CD 配置未被 Terraform 管理（manage_cicd = false）"
      echo ""
      echo "这通常是正确的：你的流水线已经在跑，没必要急着让 Terraform 接管。"
      echo ""
      echo "想接管的话，按顺序做："
      echo "  1. 在 terraform.tfvars 填入："
      echo "       harbor_admin_password   Harbor 管理员密码"
      echo "       webhook_endpoint_url    Harbor 回调 Jenkins 的地址"
      echo "                              ★ 必须是主节点 IP，不能写 localhost"
      echo "                                因为 Harbor 在容器里，看不到宿主机的 localhost"
      echo "  2. 把 manage_cicd 改为 true"
      echo "  3. terraform apply"
      echo ""
      echo "接管后 Terraform 会保证："
      echo "  - Harbor 项目存在"
      echo "  - webhook 只监听 PUSH_ARTIFACT（防止部署死循环）"
      echo "  - webhook 回调地址与认证头符合期望"
      echo "----------------------------------------------------------------"
    EOT
  }
}
