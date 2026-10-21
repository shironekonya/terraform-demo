variable "k3s_server" {
  description = <<-EOT
    K3s 主节点（server）的 SSH 连接信息。

    ★ 这台机器上的 Jenkins 与 Harbor 都是 **docker-compose 部署的**，
      不属于 K3s 集群的工作负载，而是独立于 K8s 之外的容器栈：
        Jenkins -> 单服务 compose 栈，容器名 jenkins
        Harbor  -> 官方默认配置，多容器 compose 栈（core/portal/db/redis/...）

    「compose 管理」这个事实对 Terraform 的边界影响很大：
    容器该长什么样，compose 文件里已经写清了 —— 那是**另一份声明式配置**。
    两个声明式工具管同一个对象，就会互相打架（见 containers.tf 顶部说明）。
  EOT
  type = object({
    name    = string
    host    = string
    user    = string
    ssh_key = string # 私钥路径
  })
  default = {
    name    = "k3s-server"
    host    = "192.168.193.147"
    user    = "ubuntu"
    ssh_key = "~/.ssh/id_ed25519"
  }
}

variable "k3s_agent" {
  description = "K3s 从节点（agent）的 SSH 连接信息。仅做只读盘点，不改动其配置。"
  type = object({
    name    = string
    host    = string
    user    = string
    ssh_key = string
  })
  default = {
    name    = "k3s-agent"
    host    = "192.168.193.139"
    user    = "ubuntu"
    ssh_key = "~/.ssh/id_ed25519"
  }
}

variable "ssh_port" {
  description = "SSH 端口"
  type        = number
  default     = 22
}

# ===========================================================================
# Jenkins（docker-compose 栈）
#
# ★★ 下面的默认值全部来自你实际在跑的 /data/jenkins-data/docker-compose.yaml。
#    之前这里的默认值是猜的（8080 端口 / 命名卷 jenkins_home），
#    和现实不符 —— 而这类「默认值不对」的代价很具体：
#
#      端口写 8080  ->  Harbor / GitLab 的 webhook 全部打到空端口，
#                       现象是「推了代码但流水线没动静」，且两边都不报错
#      卷名写错     ->  一旦开启接管，脚本会新建一个空卷、挂载错位置，
#                       看起来容器起来了，实际 Jenkins 配置和任务全「丢」了
#
#    所以这里的每一项都对着真实 compose 文件核过。
# ===========================================================================
variable "jenkins_container_name" {
  description = "Jenkins 容器名（compose 里 container_name 的值），脚本靠它定位容器"
  type        = string
  default     = "jenkins"
}

variable "jenkins_image" {
  description = <<-EOT
    Jenkins 镜像。默认取自你实际在用的版本。

    ★ 建议永远写明确版本号，不要用 latest：
      你现在的任务、插件、构建历史都绑在这个大版本上，
      latest 某天自动跳到下一个 LTS 大版本，可能直接导致任务起不来。
      升级应该是一次有意识的动作，而不是某次 pull 的副作用。
  EOT
  type        = string
  default     = "jenkins/jenkins:2.555.3-lts"
}

variable "jenkins_http_port" {
  description = <<-EOT
    Jenkins Web 对外端口（宿主机端口）。你实际用的是 **8081**，不是 8080。

    ★★ 这是整份配置里最容易引发「静默失效」的一个值。

    你的 Jenkins 容器把「宿主 8081」映射到「容器内 8080」。所有从外部
    访问 Jenkins 的地址都必须用 **8081**：
      - Harbor webhook 的回调地址（webhook_endpoint_url）
      - GitLab webhook 的回调地址（gitlab_webhook_endpoint_url）
      - 浏览器访问

    写 8080 的后果：请求打到宿主机一个没人监听的端口，连接被拒。
    而 Harbor / GitLab 都不把这个失败当作致命错误 ——
    它们只在各自的 webhook 投递记录里记一笔，页面主体毫无异常。
    于是现象就是「代码推上去了 / 镜像推上去了，但流水线毫无反应」。

    代码里对此加了守卫：如果 webhook 地址里的端口与这个值不一致，
    terraform apply 的安全检查段落会直接标红提示。
  EOT
  type        = number
  default     = 8081
}

variable "jenkins_agent_port" {
  description = "Jenkins 构建代理通信端口（JNLP），映射到容器内 50000"
  type        = number
  default     = 50000
}

variable "jenkins_home_dir" {
  description = <<-EOT
    Jenkins 数据目录在宿主机上的路径。会 bind mount 到容器内 /var/jenkins_home。

    ★ 注意这是 **bind mount 到宿主机目录**，不是命名卷（你原来的 compose 就是这么写的）。
      这个区别很关键：

        bind mount  -> 数据就在你指定的目录里，一眼可见、方便备份、
                       但**没有任何保护**：脚本/误操作都可能删掉它
        命名卷      -> 数据在 /var/lib/docker/volumes 下，docker rm 不会碰它

      也就是说，你这个部署的「容器重建不丢数据」只成立于
      「不删宿主机目录」这个前提。所以本配置做的事一律不碰这个目录。

    ★ 顺带一提：你的 compose 文件本身也放在这个目录下
      （/data/jenkins-data/docker-compose.yaml），所以它和 Jenkins 数据在一起。
      备份这个目录时，compose 文件会一并被备份进去 —— 这是好事。
  EOT
  type        = string
  default     = "/data/jenkins-data"
}

variable "jenkins_compose_dir" {
  description = <<-EOT
    Jenkins 的 compose 文件所在目录。
    脚本会读取该目录下的 docker-compose.yaml 来做核对。

    默认与 jenkins_home_dir 相同 —— 因为你的 compose 文件就放在数据目录里。
    如果两者不一致，改成 compose 文件实际所在的目录即可。
  EOT
  type        = string
  default     = "/data/jenkins-data"
}

variable "jenkins_manage_compose" {
  description = <<-EOT
    Jenkins 的 compose 文件漂移时，是否由 Terraform 覆写并重启栈。默认 false。

    ★ 这是本文件里最重要的一个安全选择，请务必理解它。

    背景：你的 Jenkins 已经有一份 compose 文件 ——
    它本身就是一份声明式配置，记录了镜像、端口、挂载、环境变量、
    以及那几个特意挂进去的宿主二进制（k3s / kubectl / .kube）。
    两个声明式工具去管同一个对象，必然互相打架：
    Terraform 按自己的模板覆写，就会抹掉你手工加的挂载。

    所以本配置分三档，默认停在第 2 档：

      档 1  manage_containers = false
            完全不碰，只做只读盘点（默认起点）

      档 2  manage_containers = true, jenkins_manage_compose = false
            ★ 只核对、只报告。逐项打印「期望 vs 实际」，有差异就列出来，
              但一行都不改。适合确认 Terraform 的理解和现实是否一致。

      档 3  manage_containers = true, jenkins_manage_compose = true
            真正接管：按模板渲染 compose 文件 -> 备份原文件 -> 写入 -> up -d。
            只有当你决定「compose 文件以后由 Terraform 生成」时才用这一档。

    ★ 无论哪一档，都不会执行 docker compose down -v，也不会删数据目录。
      重建栈用 docker compose up -d，容器换代但数据目录原样保留。
  EOT
  type        = bool
  default     = false
}

variable "jenkins_stack_down_on_destroy" {
  description = <<-EOT
    terraform destroy 时是否停止 Jenkins 栈。默认 false。

    理由同 cicd_cleanup_on_destroy：你很可能只是想撤掉 Terraform
    这层管理，而不是想让 CI 停摆。所以默认什么都不做。

    改成 true 的话，行为是 docker compose stop（容器保留，可用 start 恢复），
    **不是** down -v —— 本配置在任何情况下都不会删除数据。
  EOT
  type        = bool
  default     = false
}

# --- compose 模板里会被渲染进去的几项（对应你 compose 文件的对应字段）---
variable "jenkins_tz" {
  description = "容器时区。对应 compose 里的 TZ 环境变量"
  type        = string
  default     = "Asia/Shanghai"
}

variable "jenkins_uc_mirror" {
  description = <<-EOT
    插件更新中心镜像。对应 compose 里的 JENKINS_UC / JENKINS_UC_DOWNLOAD。

    ★ 保留你原来的华为云镜像 —— 这个设置在国内环境下是必要的，
      去掉之后 Jenkins 装插件会频繁超时，而且报错信息（连接超时）
      不会提示你「是镜像没配」。
  EOT
  type        = string
  default     = "https://mirrors.huaweicloud.com/jenkins"
}

variable "jenkins_cpu_limit" {
  description = "容器 CPU 限额。对应 compose 里 deploy.resources.limits.cpus。写字符串，便于填 2.5 这类值"
  type        = string
  default     = "2"
}

variable "jenkins_memory_limit" {
  description = <<-EOT
    容器内存限额。对应 compose 里 deploy.resources.limits.memory。

    ⚠️ 你设的是 2G。这项限制在 Compose V2 下会真实生效（cgroup 限制），
       所以要注意：一旦构建任务内存峰值超过 2G，容器会被 OOM Kill，
       现象是「构建到一半突然整个 Jenkins 没了」，而日志里可能什么都没有。
       如果遇到构建莫名中断，先把这项调大试试。
  EOT
  type        = string
  default     = "2G"
}

# ===========================================================================
# Harbor（docker-compose 栈，官方默认配置）
#
# 你的 Harbor 是默认配置部署 —— 也就是 harbor.yml 里什么都没改，
# 用官方 install.sh 生成的 docker-compose.yml 起来的。
# 默认配置下这就是一个约 9 个容器的栈：
#   nginx / harbor-portal / harbor-core / registry / registryctl /
#   harbor-db / redis / harbor-jobservice / harbor-log
#
# 结论很明确：**不要用 Terraform 接管 Harbor 容器层**。
# 官方根本没有单容器发行版，任何「改成单容器」的尝试都要重建整个
# 镜像仓库（包括你所有已推送的镜像和镜像的元数据）。
# Harbor 的配置归 harbor.yml + docker compose，Terraform 只盘点不动它。
# ===========================================================================
variable "manage_harbor" {
  description = <<-EOT
    是否让 Terraform 接管 Harbor 容器层。

    ★ 这里**恒为 false**，请不要改。原因不是保守，而是技术上不成立：

    1. Harbor 官方没有单容器发行版。默认配置下它就是一个 ~9 容器的
       compose 栈，容器之间有固定的依赖与启动顺序（db/redis 先起，
       core 依赖它们，nginx 在最前面）。用 Terraform 逐个 docker run
       去复刻这个栈，等于手写一遍 Harbor 的编排逻辑 —— 失败概率极高。

    2. 代价不对称。Harbor 里存着你所有已构建的镜像，以及镜像的标签、
       签名、扫描结果、复制策略。栈被搞坏不是「停一会儿」，而是
       整条流水线的制品全没了，包括正在被 K3s 拉取的镜像。

    3. 没有收益。这个栈已经跑得很稳，Harbor 的日常变更（项目、
       用户、保留策略）都在 API 层，而 API 层本配置是管的
       （见 manage_cicd）。

    所以这个变量保留在此，只是为了让「为什么不管」这个判断有据可查，
    而不是一个沉默的省略。
  EOT
  type        = bool
  default     = false
}

variable "harbor_http_port" {
  description = "Harbor Web 端口（默认配置即 80）。仅用于输出访问地址，Terraform 不会去改它"
  type        = number
  default     = 80
}

variable "harbor_https_port" {
  description = "Harbor HTTPS 端口（默认配置即 443）"
  type        = number
  default     = 443
}

# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------
variable "manage_containers" {
  description = <<-EOT
    是否让 Terraform 接管主节点的 Jenkins compose 栈。

    false = 只做只读盘点（默认起点）
    true  = 核对 compose 栈；是否真的覆写文件由 jenkins_manage_compose 再决定

    ★ 注意这个开关管的是 **Jenkins compose 栈**，不是「一台台容器」。
      你的 Jenkins 是 docker-compose 部署的，容器长什么样 compose 文件
      已经写清楚了 —— 所以这里的接管方式是「核对/覆写 compose 文件」，
      而不是「用 docker run 重新造一个容器」。

      （之前本配置用的是 docker run 方案，那在你这套环境里会**出事**：
        它会再起一个同名 Jenkins 容器，和你 compose 起来的那个
        抢 8081 / 50000 端口，结果是新容器起不来、旧容器被搞乱。）

    Harbor 的容器栈这一层无论如何都不接管，理由见 manage_harbor 的说明。
  EOT
  type        = bool
  default     = false
}

variable "container_restart_policy" {
  description = <<-EOT
    容器重启策略。你的 Jenkins 实际用的是 always。

    always 与 unless-stopped 的差别值得知道，因为它会在「有人手工停了容器」
    之后产生不同的行为：
      always        -> 宿主机重启后一定拉起；手工 stop 后若 daemon 重启也会拉起
      unless-stopped-> 手工 stop 就保持停着，不会被自动拉起（更适合做实验）
    你这个是 always，配置里就跟着写 always —— 保持与实际一致是本文件的原则。
  EOT
  type        = string
  default     = "always"

  validation {
    condition     = contains(["no", "on-failure", "always", "unless-stopped"], var.container_restart_policy)
    error_message = "重启策略只能是 no / on-failure / always / unless-stopped 之一。"
  }
}

# ===========================================================================
# CI/CD 流水线接管
#
# 你已有的链路：
#   git push -> Jenkins 构建 -> 推送镜像到 Harbor
#            -> Harbor 触发 webhook -> Jenkins 执行部署
#
# 这一层做的事：把「在网页上手工点的那些配置」变成代码 ——
#   Harbor 项目、Harbor webhook、Jenkins 流水线任务。
#
# manage_cicd 默认 false，理由同 manage_containers：
# 你流水线已经在跑，先看清现状，再决定是否让 Terraform 接管。
# ===========================================================================
variable "manage_cicd" {
  description = "是否让 Terraform 接管 CI/CD 配置（Harbor 项目/webhook、Jenkins 任务）"
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Harbor API
# ---------------------------------------------------------------------------
variable "harbor_url" {
  description = <<-EOT
    Harbor API 地址。脚本在主节点上执行，所以用 localhost。
    （Harbor 端口映射到宿主机 80，从宿主机访问 localhost 即可）
  EOT
  type        = string
  default     = "http://localhost"
}

variable "harbor_project" {
  description = "存放应用镜像的 Harbor 项目名（命名空间）"
  type        = string
  default     = "apps"
}

variable "harbor_admin_user" {
  description = "Harbor 管理员用户名"
  type        = string
  default     = "admin"
}

variable "harbor_admin_password" {
  description = "Harbor 管理员密码"
  type        = string
  sensitive   = true
  default     = ""
}

# ---------------------------------------------------------------------------
# Jenkins API
# ---------------------------------------------------------------------------
variable "jenkins_url" {
  description = <<-EOT
    Jenkins API 地址。脚本在主节点上执行，所以用 localhost。

    ★ 端口是 **8081**（宿主机侧），不是 8080 ——
      8080 是容器内部的端口，从宿主机访问不到。
  EOT
  type        = string
  default     = "http://localhost:8081"
}

variable "jenkins_admin_user" {
  description = "Jenkins 用户名"
  type        = string
  default     = "admin"
}

variable "jenkins_api_token" {
  description = <<-EOT
    Jenkins API Token（不是登录密码）。
    在 Jenkins -> 用户 -> Configure -> API Token 里生成。
    用 Token 而不是密码，是因为 Token 可单独吊销，泄露影响面更小。
  EOT
  type        = string
  sensitive   = true
  default     = ""
}

# ---------------------------------------------------------------------------
# Harbor Webhook
# ---------------------------------------------------------------------------
variable "harbor_webhook_name" {
  description = "Harbor webhook 策略名称，用于幂等识别（按名字找已有策略）"
  type        = string
  default     = "jenkins-deploy-trigger"
}

variable "webhook_endpoint_url" {
  description = <<-EOT
    webhook 回调地址，即 Harbor 收到镜像推送后去调用的 URL。

    ★ 这里必须写「主节点的 IP」，不能写 localhost。
    原因：Harbor 运行在容器里，从 Harbor 容器内部访问 localhost 是它自己，
    根本到不了 Jenkins。必须用宿主机 IP 才能出容器。

    典型值：
      http://192.168.193.147:8081/generic-webhook-trigger/invoke?token=<你的token>
      （注意端口是 8081，对应宿主机的映射端口；写成 8080 会打到空端口）
    （需要 Jenkins 安装 Generic Webhook Trigger 插件）
  EOT
  type        = string
  default     = ""
}

variable "webhook_auth_header" {
  description = "webhook 请求携带的认证头。Harbor 会原样附加到请求上，用于让 Jenkins 校验来源"
  type        = string
  sensitive   = true
  default     = ""
}

variable "webhook_event_types" {
  description = <<-EOT
    ★ 这可能是整份配置里最容易出事的一个变量。

    可选项（Harbor 2.x）：
      PUSH_ARTIFACT        推送镜像        <- 通常只想要这个
      PULL_ARTIFACT        拉取镜像        <- ★ 危险，见下方说明
      DELETE_ARTIFACT      删除镜像
      SCANNING_COMPLETED   扫描完成
      SCANNING_FAILED      扫描失败
      SCANNING_STOPPED     扫描中断
      QUOTA_EXCEED         配额超限
      QUOTA_WARNING        配额预警
      REPLICATION          复制状态变化
      TAG_RETENTION        标签清理完成

    为什么 PULL_ARTIFACT 危险：
    部署任务本身就要「拉取」镜像。如果 webhook 监听了 PULL_ARTIFACT，
    那么 部署 -> 拉镜像 -> 触发 webhook -> 再部署 -> 再拉镜像 ...
    就会形成无限循环，把 Jenkins 和 K3s 一起拖死。

    所以默认只监听 PUSH_ARTIFACT。这是本配置最重要的一处防护。
  EOT
  type        = list(string)
  default     = ["PUSH_ARTIFACT"]

  validation {
    condition     = !contains(var.webhook_event_types, "PULL_ARTIFACT")
    error_message = "不允许监听 PULL_ARTIFACT：部署任务自身会拉取镜像，会造成 webhook 无限循环。详见变量说明。"
  }
}

# ---------------------------------------------------------------------------
# Jenkins 任务
# ---------------------------------------------------------------------------
variable "manage_jenkins_job" {
  description = <<-EOT
    是否由 Terraform 创建/维护 Jenkins 的部署任务。

    默认 false。原因：Jenkins 任务更适合用 Pipeline-as-Code 管理 ——
    把 Jenkinsfile 放在应用仓库里，任务只做「指向仓库」的壳。
    如果你已经手工建好了任务，保持 false 即可，Terraform 不碰它。
  EOT
  type        = bool
  default     = false
}

variable "jenkins_deploy_job_name" {
  description = "Jenkins 部署任务名"
  type        = string
  default     = "deploy-app"
}

variable "jenkins_job_repo_url" {
  description = "部署任务对应的 Git 仓库地址（Jenkinsfile 所在仓库）"
  type        = string
  default     = ""
}

variable "jenkins_job_branch" {
  description = "要构建的分支"
  type        = string
  default     = "*/main"
}

variable "jenkinsfile_path" {
  description = "Jenkinsfile 在仓库中的路径"
  type        = string
  default     = "Jenkinsfile"
}

variable "cicd_cleanup_on_destroy" {
  description = <<-EOT
    terraform destroy 时是否删除 Harbor webhook / Jenkins 任务。

    ★ 默认 false，这是刻意的安全选择。

    原因：你这些配置是**先手工建好、流水线已经在跑**的。如果 Terraform
    接管后一 destroy 就把它删掉，会直接打断你正在工作的流水线。

    只有当你确认「这些配置完全由 Terraform 创建、删掉也无所谓」时，
    才改成 true。
  EOT
  type        = bool
  default     = false
}

# ===========================================================================
# GitLab（部署在 K3s 从节点，Omnibus 实体服务）
#
# ★ 安装方式说明：GitLab 是**直接装在系统里的**（Omnibus 包，apt/yum 安装），
#   不是 Docker 容器。这一点决定了 Terraform 的边界：
#
#     - 没有容器可管：不存在 docker run / 端口映射 / bind mount 那套东西，
#       服务生命周期归 systemctl + gitlab-ctl 管，Terraform 不掺和
#     - 配置在 /etc/gitlab/gitlab.rb 里，改动需要 gitlab-ctl reconfigure，
#       这类变更交给 Chef/Ansible 更合适，不适合用 null_resource 硬写
#     - API 层不受影响：GitLab 的 REST API 与安装方式无关，
#       所以「项目 + webhook」这层照常声明化
#
# 它是整条流水线的**入口**：
#
#   git push -> GitLab -> (Push Hook) -> Jenkins -> Harbor -> Jenkins -> K3s
#
# 两个关键差异（与 Jenkins/Harbor 相比）：
#   1. GitLab 不在主节点上，声明脚本要 ssh 到【从节点】执行
#   2. GitLab 回调 Jenkins 时，地址必须写【主节点 IP】
# ===========================================================================

# ---------------------------------------------------------------------------
# GitLab 地址（API 层用）
# ---------------------------------------------------------------------------
variable "gitlab_external_url" {
  description = <<-EOT
    GitLab 的对外地址，仅作为调用 REST API 的基址（Terraform 不会去改它）。
    留空则自动取 http://<从节点IP>。

    ★ 这个值必须与 /etc/gitlab/gitlab.rb 里的 external_url 一致：
      GitLab 对 host 不匹配的请求可能返回重定向，导致 API 调用失败。
      如果你的 external_url 带端口或 https，这里要写成一样的形式。
  EOT
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# GitLab API
# ---------------------------------------------------------------------------
variable "gitlab_api_token" {
  description = <<-EOT
    GitLab Personal Access Token，需要 api scope，且需要管理员权限
    （改「本地网络放行」是实例级设置，非管理员改不了）。

    获取方式（任选其一）：
      1. 界面：GitLab -> 用户设置 -> Access Tokens -> 勾选 api -> 生成
      2. 命令行（在从节点上执行，会直接打印出 token）：
         sudo gitlab-rails runner \
           "u=User.find_by_username('root'); \
            t=u.personal_access_tokens.new(scopes: ['api'], name: 'terraform-managed', \
            expires_at: 365.days.from_now); t.save!; puts t.token"

    ★ 为什么这个值有字符集限制：
      脚本内需要用 sed 把这个值注入到一段 Ruby 代码里，
      sed 的替换串对部分字符敏感。限制成 [A-Za-z0-9_-] 就能彻底避免这个问题，
      而 GitLab 自己生成的 token 本来就符合这个字符集。
  EOT
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition     = var.gitlab_api_token == "" || can(regex("^[A-Za-z0-9_-]{20,64}$", var.gitlab_api_token))
    error_message = "GitLab token 只允许字母/数字/下划线/连字符，长度 20~64。GitLab 生成的 token 天然满足。"
  }
}

variable "gitlab_root_user" {
  description = "GitLab 管理员用户名，用于定位要挂 token 的账号"
  type        = string
  default     = "root"
}

# ---------------------------------------------------------------------------
# GitLab 项目与 Webhook（流水线入口）
# ---------------------------------------------------------------------------
variable "manage_gitlab_cicd" {
  description = <<-EOT
    是否让 Terraform 管理 GitLab 的项目与 webhook。

    默认 false。这一层管的是「代码推上来了，怎么通知 Jenkins」——
    它是整条流水线的起点，配错了现象是「push 之后什么都没发生」，
    而且 GitLab 不会报错（回调失败只记在自己的日志里），很难排查。
    所以特别值得变成代码。
  EOT
  type        = bool
  default     = false
}

variable "gitlab_project_name" {
  description = "应用仓库的项目名"
  type        = string
  default     = "demo-app"
}

variable "gitlab_project_path" {
  description = <<-EOT
    项目在 URL 中的路径（决定 git clone 地址）。
    留空则与项目名相同。
  EOT
  type        = string
  default     = ""
}

variable "gitlab_project_visibility" {
  description = "项目可见性：private / internal / public"
  type        = string
  default     = "private"

  validation {
    condition     = contains(["private", "internal", "public"], var.gitlab_project_visibility)
    error_message = "可见性只能是 private / internal / public。实验环境建议 private。"
  }
}

variable "gitlab_webhook_endpoint_url" {
  description = <<-EOT
    GitLab 收到 push 后去调用的 URL，即 Jenkins 的构建触发地址。

    典型值：
      http://192.168.193.147:8081/generic-webhook-trigger/invoke?token=<构建任务的token>

    ★ 端口必须是 **8081**（Jenkins 宿主端口），不是 8080。
      写 8080 的失效方式极其隐蔽：GitLab 把这次投递记为失败，
      但页面主体没有任何异常，你的第一反应会是去查 Jenkins 任务。
      代码里加了守卫：地址端口与 jenkins_http_port 不一致时，
      apply 输出的安全检查段落会直接标红。

    ★ 必须写「主节点 IP」。GitLab 在从节点上，
      写 localhost 会指向 GitLab 自己，永远到不了 Jenkins。
      代码里已加校验，填 localhost / 127.0.0.1 会直接报错。
  EOT
  type        = string
  default     = ""

  validation {
    condition = var.gitlab_webhook_endpoint_url == "" || (
      can(regex("^https?://", var.gitlab_webhook_endpoint_url)) &&
      !can(regex("(localhost|127\\.0\\.0\\.1)", var.gitlab_webhook_endpoint_url))
    )
    error_message = <<-EOT
      地址必须是以 http:// 或 https:// 开头的完整 URL，且不能是 localhost / 127.0.0.1。
      原因一：GitLab 和 Jenkins 不在同一台机器上，写 localhost 会指向 GitLab 自己。
      原因二：脚本要从这个地址里解析出 host:port 加入 GitLab 的出站放行白名单，
             没有协议前缀就解析不出来。
      正确示例：http://192.168.193.147:8081/generic-webhook-trigger/invoke?token=xxx
    EOT
  }
}

variable "gitlab_webhook_token" {
  description = <<-EOT
    webhook 的 secret token。GitLab 会在请求头 X-Gitlab-Token 里原样带上它，
    Jenkins 侧可据此校验「这个请求确实来自 GitLab」。

    注意与上面的 URL 里那个 token 是两回事：
      - URL 里的 token  : Jenkins Generic Webhook Trigger 用它路由到具体任务
      - 这里的 token    : GitLab 用来向 Jenkins 证明自己的身份
  EOT
  type        = string
  sensitive   = true
  default     = ""
}

variable "gitlab_push_branch_filter" {
  description = <<-EOT
    只在匹配的分支推送上触发 webhook。留空 = 所有分支都触发。

    可用通配符，例如 main、release/*、*-stable。
    ★ 建议不要留空：否则任何人推任何临时分支都会触发一次构建，
      在实验环境里很容易把自己烦到。
  EOT
  type        = string
  default     = ""
}

variable "gitlab_enable_ssl_verification" {
  description = <<-EOT
    GitLab 回调时是否校验 Jenkins 的证书。

    默认 false —— 因为内网 Jenkins 通常是 http 或自签证书，开校验会直接失败。
    ★ 生产环境应改为 https + true。
  EOT
  type        = bool
  default     = false
}

variable "gitlab_allow_local_requests" {
  description = <<-EOT
    是否允许 GitLab 向本地/私有网段发起 webhook 请求。默认 true。

    ★★ 这可能是接入 GitLab 时最容易踩的一个坑。

    GitLab 从 10.6 起，默认**禁止** webhook 请求以下地址：
      127.0.0.1、::1、0.0.0.0
      10.0.0.0/8、172.16.0.0/12、192.168.0.0/16
      IPv6 site-local
    目的是防 SSRF —— 防止有人用 webhook 去打内网服务。

    而你的实验环境整条链路都在 192.168.x.x 里。也就是说，
    不打开这个开关，git push 之后 webhook 会被 GitLab 静默拦截，
    Jenkins 什么都不会收到，GitLab 界面上也不会有明显报错 ——
    现象就是「推了代码但流水线没动静」，极难排查。

    代码里做了两层处理：
      1. 打开全局开关 allow_local_requests_from_web_hooks_and_services
      2. 把 Jenkins 的 host:port 加进 outbound 放行白名单
  EOT
  type        = bool
  default     = true
}

variable "gitlab_webhook_test_on_apply" {
  description = <<-EOT
    配置完 webhook 后，是否调用 GitLab 的 webhook 测试接口实打实发一次。

    ★ 默认 false，这是刻意的。

    好处：这是唯一能真正证明「GitLab 能打到 Jenkins」的方法 ——
    它会返回 Jenkins 的真实响应码，比只看配置有效得多。

    代价：测试发出的是真实的 push 事件，会**真的触发一次 Jenkins 构建**，
    进而推镜像到 Harbor，再触发 Harbor webhook 去部署。也就是跑一整条流水线。

    想验证链路是否通，把它临时改成 true 跑一次 apply，验证完再改回 false。
  EOT
  type        = bool
  default     = false
}

variable "gitlab_cleanup_on_destroy" {
  description = <<-EOT
    terraform destroy 时是否删除 GitLab 上的 webhook。默认 false。

    理由同 cicd_cleanup_on_destroy：
    删掉 webhook 等于掐断流水线入口，而你很可能只是想把 Terraform 撤掉、
    而不是想让流水线停摆。
  EOT
  type        = bool
  default     = false
}

