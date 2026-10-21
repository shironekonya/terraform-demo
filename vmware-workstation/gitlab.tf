# ===========================================================================
# GitLab 层：流水线入口（部署在 K3s 从节点，Omnibus 实体服务）
#
# 你现在的完整链路：
#
#   git push                                                    [从节点]
#      |
#      v
#   GitLab  --Push Hook-->  Jenkins 构建  --推送镜像-->  Harbor   [主节点]
#                                                          |
#                                                          | PUSH_ARTIFACT
#                                                          v
#                                                    Harbor webhook
#                                                          |
#                                                          v
#                                                    Jenkins 部署任务
#                                                          |
#                                                          v
#                                                      K3s 集群
#
# ★ 安装方式：GitLab 是直接装在系统里的（Omnibus 包），不是 Docker 容器。
#   所以本文件只管 API 层的两件事：
#     1. GitLab 项目（gitlab-project.py）—— 仓库在不在
#     2. Push webhook（同一个脚本）        —— 推代码后怎么通知 Jenkins
#   服务进程本身归 systemctl + gitlab-ctl 管，Terraform 只做只读盘点
#   （见 inventory.tf 的 inventory_agent_gitlab）。
#
# ★★ 最需要注意的一点：GitLab 默认禁止 webhook 回调私有网段。
#    官方从 10.6 起拦截 127.0.0.1 / 10.0.0.0/8 / 172.16.0.0/12 / 192.168.0.0/16，
#    而你的整条链路都在 192.168.x.x 里。不显式放行，push 之后 webhook 会被
#    静默拦截 —— 现象是「推了代码但流水线没动静」，且 GitLab 界面无明显报错。
#    脚本里做了放行 + 验证，详见 scripts/gitlab-project.py 顶部注释。
# ===========================================================================

locals {
  do_gitlab_cicd = var.manage_gitlab_cicd

  # ---------------------------------------------------------------------------
  # GitLab 的对外地址（仅作为 API 调用基址）
  #
  # 这个值必须与 gitlab.rb 里的 external_url 一致：GitLab 对 host 不匹配的
  # 请求可能返回重定向，用同一个地址最省心。
  # ---------------------------------------------------------------------------
  gitlab_url = var.gitlab_external_url != "" ? var.gitlab_external_url : "http://${local.agent.host}"

  # 项目在 URL 中的完整路径，以及创建时用的 slug（最后一段）
  gl_project_path = var.gitlab_project_path != "" ? var.gitlab_project_path : var.gitlab_project_name
  gl_project_slug = reverse(split("/", local.gl_project_path))[0]

  # ---------------------------------------------------------------------------
  # 要加进 GitLab 出站放行白名单的条目，形如 192.168.193.147:8080
  # 从 webhook 地址里取 host:port 部分（变量已校验带 http(s):// 前缀）
  # ---------------------------------------------------------------------------
  gl_lan_entry = var.gitlab_webhook_endpoint_url != "" ? regex("^https?://([^/]+)", var.gitlab_webhook_endpoint_url)[0] : ""

  # ---------------------------------------------------------------------------
  # 两个 payload
  #
  # 这里定义成 **HCL 对象**（不在这里 jsonencode），因为它们要作为字段值嵌进
  # 脚本配置里 —— 如果先 jsonencode 成字符串再嵌，Python 侧 json.loads 拿到的
  # 会是一个「字符串」而不是「对象」，PUT/POST 时就会发错东西。
  # 真正需要文本形式的地方（哈希指纹）再单独 jsonencode。
  #
  # 可选字段用 merge 条件加入：GitLab 对「传空字符串」和「不传」的处理不同，
  # 比如分支过滤传空串会被当成「过滤空分支名」而不是「不过滤」。
  # ---------------------------------------------------------------------------
  gl_project_payload = {
    name                   = var.gitlab_project_name
    path                   = local.gl_project_slug
    visibility             = var.gitlab_project_visibility
    initialize_with_readme = true
  }

  gl_hook_payload = merge(
    {
      url                     = var.gitlab_webhook_endpoint_url
      push_events             = true
      tag_push_events         = false
      merge_requests_events   = false
      enable_ssl_verification = var.gitlab_enable_ssl_verification
      description             = "Terraform managed - trigger Jenkins build on push"
    },
    var.gitlab_webhook_token != "" ? { token = var.gitlab_webhook_token } : {},
    var.gitlab_push_branch_filter != "" ? { push_events_branch_filter = var.gitlab_push_branch_filter } : {},
  )

  # ---------------------------------------------------------------------------
  # 脚本配置：整份打包成 JSON，base64 编码后替换脚本里的单个占位符
  # ---------------------------------------------------------------------------
  gitlab_project_config = {
    gitlab_url      = local.gitlab_url
    token           = var.gitlab_api_token
    project_name    = var.gitlab_project_name
    project_path    = local.gl_project_path
    hook_url        = var.gitlab_webhook_endpoint_url
    lan_entry       = local.gl_lan_entry
    allow_local     = var.gitlab_allow_local_requests
    do_test         = var.gitlab_webhook_test_on_apply
    project_payload = local.gl_project_payload
    hook_payload    = local.gl_hook_payload
  }

  gitlab_project_script = replace(
    file("${path.module}/scripts/gitlab-project.py"),
    "__WF_CONFIG_B64__",
    base64encode(jsonencode(local.gitlab_project_config)),
  )
}

# ---------------------------------------------------------------------------
# 关于 GitLab 服务本身：明确说清为什么不接管
#
# GitLab 是 Omnibus 包安装的系统服务，服务生命周期归 systemctl 管、
# 配置归 /etc/gitlab/gitlab.rb 管。用 null_resource + local-exec 去改
# gitlab.rb 是反模式：没有 provider 支撑的 read，state 里只有
# 「脚本跑过没有」的标记，配置漂移了 Terraform 根本看不见。
# 这类配置变更更适合交给 Ansible 管理 gitlab.rb 模板 + gitlab-ctl reconfigure。
# ---------------------------------------------------------------------------
resource "null_resource" "gitlab_service_notice" {
  count = local.do_gitlab_cicd ? 0 : 1

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      echo "----------------------------------------------------------------"
      echo "GitLab 服务未被 Terraform 接管（这是刻意的设计）"
      echo ""
      echo "你的 GitLab 是 Omnibus 包安装的实体服务，不是容器："
      echo "  - 服务启停  : sudo gitlab-ctl start|stop|restart / systemctl"
      echo "  - 健康总览  : sudo gitlab-ctl status"
      echo "  - 日志      : sudo gitlab-ctl tail"
      echo "  - 配置      : /etc/gitlab/gitlab.rb（改完需 gitlab-ctl reconfigure）"
      echo ""
      echo "Terraform 能安全接管的是它上面的 API 层：项目 + webhook。"
      echo "开启方式：manage_gitlab_cicd = true（见 outputs 里的 next_steps）。"
      echo "----------------------------------------------------------------"
    EOT
  }
}

# ---------------------------------------------------------------------------
# GitLab 项目 + Push Webhook（流水线入口）
#
# 这一层是最值得变成代码的部分：
#   - webhook 配错时现象是「推了代码但流水线没动静」
#   - GitLab 界面不会明显报错（回调失败只记在自己的日志里）
#   - 手工排查要登 GitLab 翻 webhook 详情页，很费时间
# 变成代码之后，配置漂移在 apply 阶段就能看到。
# ---------------------------------------------------------------------------
resource "null_resource" "gitlab_project" {
  count = local.do_gitlab_cicd ? 1 : 0

  triggers = {
    gitlab_url    = local.gitlab_url
    project_path  = local.gl_project_path
    project_vis   = var.gitlab_project_visibility
    hook_url      = var.gitlab_webhook_endpoint_url
    lan_entry     = local.gl_lan_entry
    allow_local   = tostring(var.gitlab_allow_local_requests)
    branch_filter = var.gitlab_push_branch_filter
    ssl_verify    = tostring(var.gitlab_enable_ssl_verification)
    do_test       = tostring(var.gitlab_webhook_test_on_apply)

    # payload 指纹：webhook 的目标地址、分支过滤、ssl 选项任一变化都会让它变，
    # 从而触发脚本重跑（比逐个字段写 trigger 更不容易漏）
    payload_hash = sha1(jsonencode(local.gl_hook_payload))

    agent_host = local.agent.host
    ssh_user   = local.agent.user
    ssh_key    = local.agent.ssh_key
    ssh_port   = tostring(var.ssh_port)

    script_hash = filesha1("${path.module}/scripts/gitlab-project.py")
    config_hash = sha1(jsonencode(local.gitlab_project_config))

    # destroy 只能读 self.triggers：清理 webhook 需要这两项
    cleanup   = tostring(var.gitlab_cleanup_on_destroy)
    api_token = var.gitlab_api_token
  }

  depends_on = [null_resource.inventory_agent_gitlab]

  lifecycle {
    precondition {
      condition     = var.gitlab_api_token != ""
      error_message = "manage_gitlab_cicd = true 时必须设置 gitlab_api_token（需要 api scope 的管理员 Token）。"
    }

    precondition {
      condition     = var.gitlab_webhook_endpoint_url != ""
      error_message = "manage_gitlab_cicd = true 时必须设置 gitlab_webhook_endpoint_url（Jenkins 构建触发地址）。"
    }

    precondition {
      condition     = local.gl_lan_entry != ""
      error_message = "无法从 gitlab_webhook_endpoint_url 解析出主机名，请检查地址格式（需以 http:// 或 https:// 开头）。"
    }

    precondition {
      # 这个校验和 PULL_ARTIFACT 那条是同一类防护：把「配错了也不报错」
      # 的隐患提前变成 apply 阶段的硬错误。
      condition     = !can(regex("(localhost|127\\.0\\.0\\.1)", var.gitlab_webhook_endpoint_url))
      error_message = "GitLab 在从节点、Jenkins 在主节点，webhook 地址不能用 localhost，否则 GitLab 会去请求它自己。"
    }
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      GITLAB_PROJECT_SCRIPT = local.gitlab_project_script
    }

    command = <<-EOT
      set -euo pipefail
      echo "===== 同步 GitLab 项目与 Push Webhook ====="

      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=yes \
               -i "${local.agent.ssh_key}" -p ${var.ssh_port} \
               "${local.agent.user}@${local.agent.host}" 'command -v python3' >/dev/null 2>&1; then
        echo "  ✗ 从节点上没有 python3，无法执行同步脚本" >&2
        echo "     安装：sudo apt-get install -y python3   （Ubuntu / Debian）" >&2
        exit 1
      fi

      printf '%s' "$GITLAB_PROJECT_SCRIPT" | ssh \
        -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 \
        -i "${local.agent.ssh_key}" -p ${var.ssh_port} \
        "${local.agent.user}@${local.agent.host}" 'python3 -u -'

      echo "===== GitLab 流水线入口同步完成 ====="
    EOT
  }

  # -------------------------------------------------------------------------
  # 销毁：默认保留 webhook（gitlab_cleanup_on_destroy = false）
  #
  # 删掉 webhook 等于掐掉流水线入口，而你多半只是想把 Terraform 撤掉。
  #
  # 注意这里不能直接用 apply 阶段拿到的 hook id —— 它在 apply 时才产生，
  # 写不进 triggers。所以按 URL 反查，和 Harbor webhook 的处理方式一致。
  # -------------------------------------------------------------------------
  provisioner "local-exec" {
    when        = destroy
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -uo pipefail
      if [ "${self.triggers.cleanup}" != "true" ]; then
        echo "[keep] GitLab webhook「${self.triggers.hook_url}」按配置保留，未删除。"
        echo "       如需删除，请到 GitLab 项目 -> Settings -> Webhooks 手工操作，"
        echo "       或把 gitlab_cleanup_on_destroy 设为 true。"
        exit 0
      fi

      echo "[cleanup] 删除 GitLab webhook「${self.triggers.hook_url}」"

      ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=yes \
          -i "${self.triggers.ssh_key}" -p ${self.triggers.ssh_port} \
          "${self.triggers.ssh_user}@${self.triggers.agent_host}" '
        API="${self.triggers.gitlab_url}/api/v4"
        TOKEN="${self.triggers.api_token}"
        PPROJ="${self.triggers.project_path}"
        HURL="${self.triggers.hook_url}"

        ENC=$(printf "%s" "$PPROJ" | sed "s|/|%2F|g")
        PID=$(curl -sS -k -H "PRIVATE-TOKEN: $TOKEN" "$API/projects/$ENC" \
              | sed -n "s/.*\"id\":\([0-9]*\).*/\1/p" | head -1)

        if [ -z "$PID" ]; then
          echo "  未找到项目 $PPROJ，无需删除"
          exit 0
        fi

        HOOKS=$(curl -sS -k -H "PRIVATE-TOKEN: $TOKEN" "$API/projects/$PID/hooks")
        HID=$(printf "%s" "$HOOKS" | tr "}" "\n" | grep -F "\"url\":\"$HURL\"" \
              | sed -n "s/.*\"id\":\([0-9]*\).*/\1/p" | head -1)

        if [ -n "$HID" ]; then
          curl -sS -k -X DELETE -H "PRIVATE-TOKEN: $TOKEN" \
            "$API/projects/$PID/hooks/$HID" >/dev/null
          echo "  已删除 webhook id=$HID"
        else
          echo "  未找到该 webhook，无需删除"
        fi
      ' 2>/dev/null || echo "  [warn] 连不上从节点，跳过清理"
    EOT
  }
}
