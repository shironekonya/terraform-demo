# ---------------------------------------------------------------------------
# 端口一致性检查用的中间值
#
# 为什么专门抽出来：webhook 地址里的端口必须与 Jenkins 实际监听端口一致，
# 而「端口不匹配」是这套架构里最隐蔽的一种失效 ——
# Harbor / GitLab 都不会把它当致命错误，界面主体也没有异常，
# 现象仅仅是「代码推上去了但流水线毫无反应」。
# 所以这里把检查显式做出来，输出在 safety_checks 里。
#
# 命名说明：本模块的 local 名是全局唯一的，所以这里统一用 hook_* 前缀，
# 避免与其他文件里的同名 local 冲突（Terraform 会直接报 Duplicate local）。
# ---------------------------------------------------------------------------
locals {
  # Jenkins 实际监听的 宿主:端口
  jenkins_addr = "${local.server.host}:${var.jenkins_http_port}"
  jenkins_port = tostring(var.jenkins_http_port)

  # 从 webhook 地址里提取端口号；地址里没写端口时为空串
  hook_port_harbor = (
    can(regex("^https?://[^/]+:([0-9]+)", var.webhook_endpoint_url))
    ? regex("^https?://[^/]+:([0-9]+)", var.webhook_endpoint_url)[0]
    : ""
  )

  hook_port_gitlab = (
    can(regex("^https?://[^/]+:([0-9]+)", var.gitlab_webhook_endpoint_url))
    ? regex("^https?://[^/]+:([0-9]+)", var.gitlab_webhook_endpoint_url)[0]
    : ""
  )
}

# ---------------------------------------------------------------------------
# 拓扑输出：把「你的环境长什么样」明确打印出来
# ---------------------------------------------------------------------------
output "topology" {
  description = "本环境拓扑"
  value = {
    k3s_server = {
      role = "K3s server（主节点）"
      host = local.server.host
      runs = [
        "K3s 控制平面",
        "Jenkins（docker-compose 栈，单服务）",
        "Harbor（docker-compose 栈，官方默认配置，约 9 个容器）",
      ]
      # 在这里把「地址」明确写出来，因为整条流水线的 webhook 配置都依赖它们，
      # 而端口写错是这套架构里最隐蔽的失效原因（详见 safety_checks）
      jenkins_addr      = "${local.server.host}:${var.jenkins_http_port}"
      harbor_addr       = "${local.server.host}:${var.harbor_http_port}"
      pipeline_position = "构建与制品侧：构建镜像、存镜像、执行部署"
    }
    k3s_agent = {
      role = "K3s agent（从节点）"
      host = local.agent.host
      runs = [
        "K3s 工作负载",
        "GitLab（Omnibus 实体服务，非容器）",
      ]
      gitlab_addr       = local.gitlab_url
      pipeline_position = "流水线入口：代码托管，push 后触发 Jenkins"
    }
  }
}

# ---------------------------------------------------------------------------
# 管理范围：明确说清 Terraform 管了什么、没管什么
# 这是本方案最重要的输出 —— 避免对 Terraform 能力产生错误预期
# ---------------------------------------------------------------------------
output "management_scope" {
  description = "Terraform 的管理边界"
  value = {
    managed = compact([
      var.manage_containers
      ? (
        var.jenkins_manage_compose
        ? "Jenkins compose 栈（★ 接管模式：差异处会覆写 compose 文件并重启栈）"
        : "Jenkins compose 栈（核对模式：逐项比对并报告，不改动任何东西）"
      )
      : "",
      var.manage_cicd ? "Harbor 项目与 webhook 策略（主节点，声明式同步）" : "",
      var.manage_cicd && var.manage_jenkins_job ? "Jenkins 部署任务（主节点，声明式同步）" : "",
      var.manage_gitlab_cicd ? "GitLab 项目与 Push webhook（从节点，声明式同步）" : "",
    ])

    not_managed = compact([
      "虚拟机本身（VMware Workstation 的职责，Terraform 无法接管）",
      "K3s 集群及集群内工作负载（k3s 自身机制管理）",
      "Jenkins 容器的日常运行（归 docker compose 管；Terraform 只在接管模式下覆写它的 compose 文件）",
      "Harbor 容器栈（官方是 ~9 容器的 compose 部署，没有单容器版，技术上无法接管 —— 见 manage_harbor 说明）",
      "GitLab 服务本身（Omnibus 系统包安装，归 systemctl + gitlab-ctl 管，Terraform 只做只读盘点）",
      var.manage_cicd ? "" : "Harbor / Jenkins 侧的流水线配置（默认不接管）",
      var.manage_gitlab_cicd ? "" : "GitLab 项目与 webhook（默认不接管）",
    ])

    # 只有真正会改动机器状态的开关才让它变成「变更模式」。
    # manage_containers = true 而 jenkins_manage_compose = false 时，
    # 行为仍然是纯只读的（只核对），所以不该标成变更模式。
    current_mode = (
      var.jenkins_manage_compose || var.manage_cicd || var.manage_gitlab_cicd
      ? "★ 变更模式：apply 会真实同步配置"
      : "◇ 只读模式：仅盘点与核对，不改动任何东西"
    )
  }
}

# ---------------------------------------------------------------------------
# CI/CD 流水线状态
# ---------------------------------------------------------------------------
output "cicd_pipeline" {
  description = "CI/CD 链路的构成与关键配置"
  value = {
    enabled = {
      gitlab_entry = var.manage_gitlab_cicd
      harbor_side  = var.manage_cicd
    }

    # 完整链路，按数据流方向排列
    flow = [
      "1. [从节点] git push 到 GitLab",
      "2. [从节点] GitLab 发出 Push Hook，回调主节点的 Jenkins",
      "3. [主节点] Jenkins 构建镜像并推送到 Harbor",
      "4. [主节点] Harbor 收到 PUSH_ARTIFACT 事件",
      "5. [主节点] Harbor webhook 回调 Jenkins",
      "6. [主节点] Jenkins 执行部署，K3s 拉取新镜像",
    ]

    gitlab = {
      url           = local.gitlab_url
      project       = local.gl_project_path
      hook_url      = var.gitlab_webhook_endpoint_url != "" ? var.gitlab_webhook_endpoint_url : "（未设置）"
      branch_filter = var.gitlab_push_branch_filter != "" ? var.gitlab_push_branch_filter : "（全部分支）"
      managed       = var.manage_gitlab_cicd
    }

    harbor = {
      api      = var.harbor_url
      project  = var.harbor_project
      webhook  = var.harbor_webhook_name
      events   = var.webhook_event_types
      endpoint = var.webhook_endpoint_url != "" ? var.webhook_endpoint_url : "（未设置）"
    }

    jenkins = {
      api       = var.jenkins_url
      web       = "http://${local.jenkins_addr}"
      container = var.jenkins_container_name
      job       = var.jenkins_deploy_job_name
      managed   = var.manage_jenkins_job
      repo      = var.jenkins_job_repo_url != "" ? var.jenkins_job_repo_url : "（未设置）"
      # 容器内部 8080 vs 宿主 8081 是这套配置里最容易搞错的一处，
      # 所以直接在输出里把两者都写出来
      port_map = "${var.jenkins_http_port}（宿主）-> 8080（容器内）"
    }

    # 把风险显式打印出来，而不是藏在注释里
    #
    # 每个三元表达式都用括号包住：HCL 中换行会终止一个完整表达式，
    # 不包括号时下一行的 ? 会被当成非法 token。
    safety_checks = [
      # --- Harbor 侧 ---
      (
        contains(var.webhook_event_types, "PULL_ARTIFACT")
        ? "✗ 危险：Harbor webhook 监听了 PULL_ARTIFACT，会造成部署死循环"
        : "✓ Harbor webhook 未监听 PULL_ARTIFACT，无部署死循环风险"
      ),

      (
        length(var.webhook_event_types) == 1
        ? "✓ Harbor webhook 只监听 PUSH_ARTIFACT，事件来源单一"
        : "⚠ Harbor webhook 监听了 ${length(var.webhook_event_types)} 种事件，注意可能产生重复触发"
      ),

      # --- GitLab 侧 ---
      (
        var.gitlab_allow_local_requests
        ? "✓ GitLab 已允许回调私有网段（实验环境必须，否则 webhook 被静默拦截）"
        : "✗ 危险：GitLab 未放行私有网段，webhook 会被 SSRF 防护拦截，流水线不会触发"
      ),

      (
        can(regex("(localhost|127\\.0\\.0\\.1)", var.gitlab_webhook_endpoint_url))
        ? "✗ 危险：GitLab webhook 指向 localhost，会请求 GitLab 自己而不是 Jenkins"
        : "✓ GitLab webhook 指向的是真实主机地址"
      ),

      (
        var.gitlab_push_branch_filter == ""
        ? "⚠ GitLab webhook 未设置分支过滤，任何分支的 push 都会触发构建"
        : "✓ GitLab webhook 仅对分支「${var.gitlab_push_branch_filter}」的 push 触发"
      ),

      # --- 端口一致性（真实踩过的坑，不是理论风险）---
      # 这几个检查的由来：本配置早期的默认端口是猜的 8080，而实际是 8081。
      # 如果 webhook 地址照抄默认值，回调会打到宿主机上没人监听的端口，
      # 而 Harbor / GitLab 都不把这当致命错误 —— 现象只是「流水线毫无反应」。
      # 所以值得每次 apply 都显式核对一遍。
      (
        var.webhook_endpoint_url == ""
        ? "⚠ Harbor webhook 地址尚未设置，无法校验端口"
        : local.hook_port_harbor == ""
        ? "⚠ Harbor webhook 地址里没有显式端口 —— 若走反代则正常，否则请补上 :${local.jenkins_port}"
        : local.hook_port_harbor == local.jenkins_port
        ? "✓ Harbor webhook 端口与 Jenkins 一致（${local.jenkins_port}）"
        : "✗ 危险：Harbor webhook 端口是 ${local.hook_port_harbor}，而 Jenkins 实际在 ${local.jenkins_port} —— 回调会被直接拒绝"
      ),

      (
        var.gitlab_webhook_endpoint_url == ""
        ? "⚠ GitLab webhook 地址尚未设置，无法校验端口"
        : local.hook_port_gitlab == ""
        ? "⚠ GitLab webhook 地址里没有显式端口 —— 若走反代则正常，否则请补上 :${local.jenkins_port}"
        : local.hook_port_gitlab == local.jenkins_port
        ? "✓ GitLab webhook 端口与 Jenkins 一致（${local.jenkins_port}）"
        : "✗ 危险：GitLab webhook 端口是 ${local.hook_port_gitlab}，而 Jenkins 实际在 ${local.jenkins_port} —— 回调会被直接拒绝"
      ),

      # --- Jenkins compose 栈 ---
      (
        var.manage_containers
        ? (
          var.jenkins_manage_compose
          ? "★ Jenkins compose 栈处于接管模式：有差异时会覆写 docker-compose.yaml 并重启栈（覆写前自动备份）"
          : "◇ Jenkins compose 栈处于核对模式：逐项比对并报告，不改动任何文件"
        )
        : "◇ Jenkins compose 栈未被核对（manage_containers = false，只做容器盘点）"
      ),

      (
        var.jenkins_home_dir == var.jenkins_compose_dir
        ? "ℹ compose 文件与 Jenkins 数据在同一目录（${var.jenkins_home_dir}）—— 备份该目录会连带备份 compose 文件"
        : "ℹ compose 文件（${var.jenkins_compose_dir}）与数据目录（${var.jenkins_home_dir}）不同，备份时别漏了 compose 文件"
      ),

      # --- 通用 ---
      (
        var.cicd_cleanup_on_destroy || var.gitlab_cleanup_on_destroy
        ? "⚠ destroy 会删除部分 webhook，请确认这些配置完全由 Terraform 创建"
        : "✓ destroy 保留全部既有 webhook，不会打断正在运行的流水线"
      ),
    ]
  }
}

# ---------------------------------------------------------------------------
# 访问入口
# ---------------------------------------------------------------------------
output "access_points" {
  description = "各服务访问地址"
  value = {
    gitlab_web = local.gitlab_url

    # ★ 这几个地址是「环境事实」，与是否被 Terraform 接管无关。
    #   之前这里写成「未接管就显示提示文字」，其实不好 ——
    #   端口本身就是排查流水线问题最重要的信息，不该被条件藏起来。
    jenkins_web = "http://${local.jenkins_addr}"

    harbor_web = (
      var.harbor_http_port == 80
      ? "http://${local.server.host}"
      : "http://${local.server.host}:${var.harbor_http_port}"
    )

    k3s_api = "https://${local.server.host}:6443"

    notes = {
      jenkins_agent_port = "JNLP 构建代理端口（宿主 ${var.jenkins_agent_port} -> 容器 50000）"
      jenkins_inner_port = "注意：Jenkins 容器内部监听 8080，宿主映射到 ${var.jenkins_http_port} —— 所有外部地址都必须用宿主端口"
      harbor_https_port  = "Harbor HTTPS 端口：${var.harbor_https_port}"
    }
  }
}

# ---------------------------------------------------------------------------
# GitLab 相关提示
# ---------------------------------------------------------------------------
output "gitlab_notes" {
  description = "接入 GitLab 时最需要注意的几点"
  value = {
    # 1. 安装方式带来的管理边界
    install_mode = <<-EOT
      你的 GitLab 是 Omnibus 包安装的实体服务（不是 Docker 容器）。
      因此 Terraform 的边界是：
        - 服务启停/配置（gitlab.rb）归系统管：sudo gitlab-ctl status / restart
          改配置后需要 sudo gitlab-ctl reconfigure 生效
        - gitlab.rb 的变更管理更适合交给 Ansible（模板化 + reconfigure）
        - Terraform 只接管 API 层：项目 + webhook（manage_gitlab_cicd）
    EOT

    # 2. 平台差异带来的「静默失败」风险
    local_network_block = <<-EOT
      GitLab 默认禁止 webhook 回调私有网段（10.0.0.0/8、172.16.0.0/12、192.168.0.0/16）。
      你的实验链路整条都在私有网段内，所以「本地网络放行」必须打开，
      否则 git push 之后 remote 看不到任何反应，GitLab 界面也不会有明显报错。
      脚本会自动开启并回读验证，见 terraform apply 输出中的「本地网络放行设置」段落。
    EOT

    # 3. 数据安全
    data_safety = <<-EOT
      GitLab 数据在 /var/opt/gitlab（仓库、数据库、上传文件都在这）。
      ★ 实体服务的「重建容器不丢数据」优势不存在了 —— 磁盘坏 = 数据没。
      请定期跑 sudo gitlab-backup create，并确认 /var/opt/gitlab/backups
      里有备份文件。盘点输出会打印备份目录现状。
    EOT

    # 4. 与 K3s 共存
    co_located_with_k3s = <<-EOT
      GitLab 和 K3s agent 在同一台机器上。GitLab 官方建议 4GB 内存起步，
      而 K3s agent 上的业务 Pod 也在吃内存 —— 这台从节点是全链路里
      资源最紧张的一台。terraform apply 的盘点输出会打印内存与数据目录占用。
    EOT
  }
}

# ---------------------------------------------------------------------------
# Jenkins / Harbor 的 compose 现实
#
# 这一节的存在意义：把「哪些东西归 compose 管、哪些归 Terraform 管」
# 以及两个安全事实明确打印出来，避免以后对边界产生错误预期。
# ---------------------------------------------------------------------------
output "compose_notes" {
  description = "compose 部署的事实与管理边界（含两个安全提醒）"
  value = {
    jenkins_stack = <<-EOT
      Jenkins 是 docker-compose 部署的（不是裸 docker run）：
        compose 文件 : ${var.jenkins_compose_dir}/docker-compose.yaml
        容器名       : ${var.jenkins_container_name}
        镜像         : ${var.jenkins_image}
        端口         : ${var.jenkins_http_port}（宿主）-> 8080（容器内）
        数据         : ${var.jenkins_home_dir}（bind mount 到 /var/jenkins_home）
        重启策略     : ${var.container_restart_policy}

      ★ 这份 compose 文件本身就是声明式配置 —— 所以本配置默认只核对、不覆写。
        两个声明式工具争一个对象，必须明确谁说了算，见 jenkins_manage_compose。
      ★ 想再重看一次核对结果（triggers 未变时不会自动重跑）：
          terraform apply -replace=null_resource.jenkins_stack
    EOT

    harbor_stack = <<-EOT
      Harbor 是官方默认配置的 compose 部署，约 9 个容器：
        nginx / harbor-portal / harbor-core / registry / registryctl
        harbor-db / redis / harbor-jobservice / harbor-log
      端口：HTTP ${var.harbor_http_port} / HTTPS ${var.harbor_https_port}

      Terraform 不接管它的容器层 —— 官方没有单容器发行版，
      而栈被搞坏的代价是整条流水线的制品（所有镜像 + 标签 + 扫描结果）全没。
      它的容器与配置继续归 docker compose + harbor.yml 管；
      项目与 webhook 策略则由 manage_cicd 接管。
    EOT

    # 两个安全事实 + 一个资源限制提醒。
    # 放在输出里而不是注释里，是因为这些属于「知道了就该评估一下」的级别。
    security = [
      "⚠ Jenkins 以 root + privileged 运行，并挂载了宿主 /var/run/docker.sock。这意味着任何能提交 Jenkins 任务的人实际上都能拿到宿主 root 权限（构建脚本里 docker run -v /:/host 就够了）。实验环境可以接受，但这台机器上还跑着 Harbor 和 K3s，值得知道这条边界。",
      "⚠ Jenkins 数据是 bind mount 到 ${var.jenkins_home_dir} 的，没有「docker rm 不碰命名卷」那层保护 —— 目录被删或被改，配置、插件、构建历史一起没。建议把它纳入备份。",
      "ℹ 容器限额 ${var.jenkins_memory_limit} 内存 / ${var.jenkins_cpu_limit} CPU（Compose V2 下会真实生效）：构建内存峰值超限会被 OOM Kill，现象是「构建到一半整个 Jenkins 消失」且容器日志没有内容。",
    ]
  }
}

output "next_steps" {
  description = "操作建议"
  value = <<-EOT
    ${var.jenkins_manage_compose || var.manage_cicd || var.manage_gitlab_cicd
  ? "当前是【变更模式】，apply 会真实同步配置。"
: "当前是【只读模式】，apply 不会改动机器上任何东西。"}

    推荐推进顺序（每一步都可独立验证，出问题好定位）：

    第 1 步 · 看清现状（默认状态）
      terraform apply
      → 盘点输出会打印主节点的：容器清单（含 compose 工程名与 compose 文件路径）、
        Jenkins 那四项宿主资源挂载是否都在、Jenkins 的 compose 文件内容摘要、
        Harbor 各容器状态；以及从节点的 GitLab 服务健康、内存与磁盘占用。

    第 2 步 · 让 Terraform 核对 Jenkins compose 栈（仍然不改动任何东西）
      # terraform.tfvars
      manage_containers = true
      # jenkins_manage_compose 保持 false
      terraform apply
      → 脚本逐项比对「运行中的容器」「磁盘上的 compose 文件」与期望值，
        列出差异，但一行都不改。用来确认 Terraform 的理解与你的现实一致。
      → 想再跑一次核对（triggers 没变时不会自动重跑）：
          terraform apply -replace=null_resource.jenkins_stack

    第 3 步 · 接管 GitLab 的「项目 + webhook」（流水线入口）
      manage_gitlab_cicd = true
      gitlab_api_token   = "..."   # 需要 api scope + 管理员权限
      gitlab_webhook_endpoint_url = "http://${local.jenkins_addr}/generic-webhook-trigger/invoke?token=..."
      terraform apply
      ★ 地址里的端口必须是 ${local.jenkins_port}（Jenkins 的宿主端口），不能写 8080。

    第 4 步 · 验证入口真的通
      把 gitlab_webhook_test_on_apply 临时设为 true 跑一次 apply，
      它会向 Jenkins 实发一次 push 事件并返回真实响应码。
      注意：这真的会触发一次完整流水线，验证完记得改回 false。

    ⚠ 关于 jenkins_manage_compose = true（接管模式）
      那一步会让 Terraform 按模板覆写你的 docker-compose.yaml（覆写前自动备份）。
      只有当你决定「compose 文件以后由 Terraform 生成」时才开。
      如果继续手工维护那份文件，就让它保持 false —— 核对模式本身同样有价值。

    排查速查：
      Jenkins 栈状态    : ssh <主节点> "docker compose -f ${var.jenkins_compose_dir}/docker-compose.yaml ps"
      Jenkins 容器日志  : ssh <主节点> "docker logs --tail 100 ${var.jenkins_container_name}"
      Jenkins 配置核对  : terraform apply -replace=null_resource.jenkins_stack
      GitLab 服务状态   : ssh <从节点> "sudo gitlab-ctl status"
      GitLab 服务日志   : ssh <从节点> "sudo gitlab-ctl tail"
      GitLab webhook 记录: 项目 -> Settings -> Webhooks -> Recent events
      Harbor   webhook 记录: 项目 -> Webhooks -> 看投递状态
      Jenkins 触发日志  : 任务 -> 构建历史 -> Console Output
  EOT
}
