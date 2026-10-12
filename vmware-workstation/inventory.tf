# ===========================================================================
# 第一阶段：只读盘点
#
# 你机器上已经有在跑的服务，所以第一步不该是「改」，而是「看清」。
# 这些资源全部只读，跑多少次都不会改变机器上任何东西。
#
# 这一层的价值：把「机器上现在到底有什么」变成 Terraform 的输出，
# 后续要做任何变更时，心里有底。
# ===========================================================================

locals {
  server = var.k3s_server
  agent  = var.k3s_agent

  # SSH 公共参数，避免在每个脚本里重复拼
  ssh_opts_server = "-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes -i ${local.server.ssh_key} -p ${var.ssh_port}"
  ssh_opts_agent  = "-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes -i ${local.agent.ssh_key} -p ${var.ssh_port}"

  # ---------------------------------------------------------------------------
  # Jenkins 容器里必须存在的「宿主资源挂载点」
  #
  # 这些是 compose 文件里特意挂进去的，少任何一个都会让部署任务失败，
  # 而 Jenkins 界面本身看不出任何异常。所以盘点（inventory）和核对（containers）
  # 都逐个确认它们。定义在这里，两个文件共用一份，避免清单各自漂移。
  # ---------------------------------------------------------------------------
  jenkins_host_mounts = [
    "/var/run/docker.sock",
    "/host-bin/k3s",
    "/host-bin/kubectl",
    "/etc/rancher/k3s",
    "/root/.kube",
  ]

  # ---------------------------------------------------------------------------
  # 盘点脚本的渲染
  #
  # 四个 section 共用同一份 scripts/inventory.py，只是注入的配置不同
  # （配置里带 section 名）。所以用 for 表达式一次渲染，不重复四遍。
  #
  # 配置以 base64(JSON) 注入：字符集安全，不需要任何转义。
  # ---------------------------------------------------------------------------
  inventory_sections = {
    base       = "base"
    containers = "containers"
    k3s        = "k3s"
    gitlab     = "gitlab"
  }

  inventory_scripts = {
    for key, section in local.inventory_sections :
    key => replace(
      file("${path.module}/scripts/inventory.py"),
      "__WF_CONFIG_B64__",
      base64encode(jsonencode({
        section     = section
        host_mounts = local.jenkins_host_mounts
      })),
    )
  }
}

# ---------------------------------------------------------------------------
# 盘点 1：两台机器的基础信息（OS、内核、内存、Docker 版本）
# ---------------------------------------------------------------------------
resource "null_resource" "inventory_nodes" {
  triggers = {
    server_host = local.server.host
    agent_host  = local.agent.host
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      INVENTORY_SCRIPT = local.inventory_scripts.base
    }

    command = <<-EOT
      set -uo pipefail
      echo "==================== 节点基础信息 ===================="

      for entry in "server:${local.server.host}:${local.server.user}:${local.server.ssh_key}" \
                   "agent:${local.agent.host}:${local.agent.user}:${local.agent.ssh_key}"; do
        ROLE="$${entry%%:*}"
        REST="$${entry#*:}"
        HOST="$${REST%%:*}"
        REST="$${REST#*:}"
        USER="$${REST%%:*}"
        KEY="$${REST#*:}"

        echo ""
        echo "---------- [$${ROLE}] $${USER}@$${HOST} ----------"

        if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes \
                 -i "$${KEY}" -p ${var.ssh_port} "$${USER}@$${HOST}" true 2>/dev/null; then
          echo "  ✗ SSH 连不上。请检查 IP / 用户名 / 私钥路径，或先手工 ssh 一次接受主机指纹。"
          continue
        fi

        if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes \
                 -i "$${KEY}" -p ${var.ssh_port} "$${USER}@$${HOST}" \
                 'command -v python3' >/dev/null 2>&1; then
          echo "  ✗ 远端没有 python3，跳过。安装：sudo apt-get install -y python3"
          continue
        fi

        printf '%s' "$INVENTORY_SCRIPT" | ssh \
          -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes \
          -i "$${KEY}" -p ${var.ssh_port} "$${USER}@$${HOST}" 'python3 -u -'
      done
      echo ""
      echo "======================================================"
    EOT
  }
}

# ---------------------------------------------------------------------------
# 盘点 2：主节点上正在运行的容器（Jenkins / Harbor 的真实状态）
#
# 这是最关键的一步 —— 它会告诉你：
#   - Jenkins 的容器名、镜像版本、端口映射、挂载（尤其那四项宿主资源挂载）
#   - Jenkins 是不是 compose 创建的，以及 compose 文件究竟在哪个路径
#   - Harbor 那几个容器是否都在跑
#   - 宿主机上都有哪些 compose 工程
#
# ★ 关于「compose 文件在哪」这一项：容器自己记着答案。
#   docker inspect 的 com.docker.compose.project.config_files 标签就是
#   创建它时用的文件路径。容器上的标签是事实来源，比翻目录找文件可靠。
#
# 拿到真实信息后，回头对照 variables.tf 里的默认值核对。
# 你贴出的 compose 已经让大部分默认值定下来了，这里主要用于**复核**。
# ---------------------------------------------------------------------------
resource "null_resource" "inventory_containers" {
  triggers = {
    server_host = local.server.host
  }

  depends_on = [null_resource.inventory_nodes]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      INVENTORY_SCRIPT = local.inventory_scripts.containers
    }

    command = <<-EOT
      set -uo pipefail
      echo "============ 主节点容器盘点 ============"

      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes \
               -i "${local.server.ssh_key}" -p ${var.ssh_port} \
               "${local.server.user}@${local.server.host}" 'command -v docker >/dev/null 2>&1'; then
        echo "✗ 主节点连不上，或未安装 Docker。"
        exit 0
      fi
      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes \
               -i "${local.server.ssh_key}" -p ${var.ssh_port} \
               "${local.server.user}@${local.server.host}" \
               'command -v python3' >/dev/null 2>&1; then
        echo "✗ 远端没有 python3，跳过。安装：sudo apt-get install -y python3"
        exit 0
      fi

      printf '%s' "$INVENTORY_SCRIPT" | ssh \
        -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
        -i "${local.server.ssh_key}" -p ${var.ssh_port} \
        "${local.server.user}@${local.server.host}" 'python3 -u -' \
        || echo "✗ 远程盘点失败"

      echo ""
      echo "========================================"
    EOT
  }
}

# ---------------------------------------------------------------------------
# 盘点 3：K3s 集群状态（确认一主一从是否健康）
# 只读：kubectl get 不会改变集群任何状态
# ---------------------------------------------------------------------------
resource "null_resource" "inventory_k3s" {
  triggers = {
    server_host = local.server.host
  }

  depends_on = [null_resource.inventory_nodes]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      INVENTORY_SCRIPT = local.inventory_scripts.k3s
    }

    command = <<-EOT
      set -uo pipefail
      echo "============ K3s 集群状态 ============"

      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes \
               -i "${local.server.ssh_key}" -p ${var.ssh_port} \
               "${local.server.user}@${local.server.host}" \
               'command -v k3s >/dev/null 2>&1 || command -v kubectl >/dev/null 2>&1'; then
        echo "✗ 主节点未检测到 k3s/kubectl，跳过。"
        exit 0
      fi
      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes \
               -i "${local.server.ssh_key}" -p ${var.ssh_port} \
               "${local.server.user}@${local.server.host}" \
               'command -v python3' >/dev/null 2>&1; then
        echo "✗ 远端没有 python3，跳过。安装：sudo apt-get install -y python3"
        exit 0
      fi

      printf '%s' "$INVENTORY_SCRIPT" | ssh \
        -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
        -i "${local.server.ssh_key}" -p ${var.ssh_port} \
        "${local.server.user}@${local.server.host}" 'python3 -u -' \
        || echo "✗ 远程查询失败"

      echo ""
      echo "========================================"
    EOT
  }
}

# ---------------------------------------------------------------------------
# 盘点 4：从节点上的 GitLab（真实状态）
#
# 与主节点那份分开，因为从节点的角色不同：
#   主节点 -> Jenkins + Harbor（流水线的构建与制品侧）
#   从节点 -> GitLab（流水线的入口）
#
# ★ 关键前提：GitLab 是 Omnibus 包安装的**实体服务**，不是 Docker 容器。
#   所以探测顺序是先判断安装方式，再按对应方式盘点：
#     - 有 gitlab-ctl        -> Omnibus 实体服务（你当前的情况）
#     - 只有 docker 里有     -> 容器部署（万一哪天换了方式也能看出来）
#
# 这里同样只读。Omnibus 模式下重点看五件事：
#   1. gitlab-ctl status：各组件（puma/postgres/redis/gitaly...）是否都在跑
#   2. gitlab.rb 里的 external_url：与 Terraform 侧 gitlab_external_url 对齐
#   3. 版本号：决定 webhook 相关 API 行为
#   4. /var/opt/gitlab 数据目录占用：仓库都在这，盯紧磁盘
#   5. 内存：GitLab + K3s agent 共存，内存是最紧的资源
# ---------------------------------------------------------------------------
resource "null_resource" "inventory_agent_gitlab" {
  triggers = {
    agent_host = local.agent.host
  }

  depends_on = [null_resource.inventory_nodes]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    environment = {
      INVENTORY_SCRIPT = local.inventory_scripts.gitlab
    }

    command = <<-EOT
      set -uo pipefail
      echo "============ 从节点 GitLab 盘点 ============"

      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes \
               -i "${local.agent.ssh_key}" -p ${var.ssh_port} \
               "${local.agent.user}@${local.agent.host}" true 2>/dev/null; then
        echo "✗ 从节点连不上。请检查 IP / 用户名 / 私钥路径。"
        exit 0
      fi
      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes \
               -i "${local.agent.ssh_key}" -p ${var.ssh_port} \
               "${local.agent.user}@${local.agent.host}" \
               'command -v python3' >/dev/null 2>&1; then
        echo "✗ 远端没有 python3，跳过。安装：sudo apt-get install -y python3"
        exit 0
      fi

      printf '%s' "$INVENTORY_SCRIPT" | ssh \
        -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
        -i "${local.agent.ssh_key}" -p ${var.ssh_port} \
        "${local.agent.user}@${local.agent.host}" 'python3 -u -' \
        || echo "✗ 远程盘点失败"

      echo ""
      echo "========================================"
    EOT
  }
}
