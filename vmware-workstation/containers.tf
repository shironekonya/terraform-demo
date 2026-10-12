# ===========================================================================
# 第二阶段：Jenkins compose 栈的核对与接管
#
# ★ 你贴出的 docker-compose.yaml 推翻了这个文件原来的设计，值得记录一下原因。
#
# 原设计（错的）：
#   用 null_resource + local-exec 执行 `docker run`，从零造一个 Jenkins 容器。
#   这在「Jenkins 是个裸 docker run 容器」的前提下成立。
#
# 现实：
#   Jenkins 是 **docker-compose 部署的**。/data/jenkins-data/docker-compose.yaml
#   就是它的声明式配置 —— 容器名、端口映射、bind mount、环境变量，
#   还特意挂了几个宿主资源：
#
#       /var/run/docker.sock    -> 在宿主机上构建镜像
#       /usr/bin/docker         -> 容器内直接用宿主 docker CLI
#       /usr/local/bin/k3s      -> 直接操作宿主 K3s
#       /usr/local/bin/kubectl  -> 部署到 K3s
#       /etc/rancher/k3s        -> 读 K3s 配置
#       /root/.kube             -> 读 kubeconfig
#
# 原来的 docker run 写法在你这里为什么必然出事：
#   1. 它会再造一个叫 jenkins 的容器，与 compose 起的那个抢 8081 / 50000 端口
#      —— 新容器起不来，旧容器还可能被搞乱
#   2. 它不知道那几个宿主挂载。即使容器起来了，每个部署任务都会失败，
#      报 kubectl: command not found，而且现象很有迷惑性（Jenkins 界面一切正常）
#   3. 它管的是「容器」，不是「声明容器的 compose 文件」——
#      而后者才是这套部署真正的源头
#
# ---------------------------------------------------------------------------
# 现在的设计：不造容器，改为核对/接管 compose 栈
#
# 核心判断：两个声明式工具管同一个对象，必须明确谁说了算。
#   compose 文件本身就是一份声明式配置。Terraform 若无条件按模板覆写，
#   就会抹掉你手工加的挂载 —— 所以默认是「compose 文件说了算，
#   Terraform 只核对」，接管（覆写）是一个需要显式打开的动作。
#
# 核对分三层（详见 scripts/jenkins-compose.py）：
#   A. 运行中的容器 vs 期望        —— 抓「容器被手工改过」
#   B. 磁盘 compose 文件 vs 期望   —— 抓「文件不是期望的那份」，用语义比对
#   C. 处置                        —— verify 只报告；reconcile 才动手
#
# 安全阀：manage_containers 默认 false；即使打开，jenkins_manage_compose
# 默认也是 false（只核对不覆写）。两级分开是有意的，见 variables.tf 里的说明。
#
# ★ 反复看到核对报告的方法：
#   null_resource 的 provisioner 只在 triggers 变化时才重跑，所以配置没变时
#   第二次 apply 不会重新打印报告。想强制重看一次，用：
#     terraform apply -replace=null_resource.jenkins_stack
#   这是 Terraform 的标准做法，不需要为它加任何变量。
# ===========================================================================

locals {
  # 是否落到实例上：只有 manage_containers 打开才核对 compose 栈
  do_jenkins = var.manage_containers

  # 核对模式还是接管模式
  jenkins_mode = var.jenkins_manage_compose ? "reconcile" : "verify"

  jenkins_compose_path = "${var.jenkins_compose_dir}/docker-compose.yaml"

  # ---------------------------------------------------------------------------
  # 期望的 compose 内容
  #
  # 用 templatefile() 而不是一长串 replace()：模板里直接写 ${image} 这样的
  # 具名变量，比 __IMAGE__ 这类占位符可读，而且变量名写错会在 plan 阶段
  # 直接报错（replace 写错只会静默不替换）。
  # ---------------------------------------------------------------------------
  expected_compose = templatefile("${path.module}/templates/jenkins-compose.yaml.tpl", {
    image          = var.jenkins_image
    container_name = var.jenkins_container_name
    restart_policy = var.container_restart_policy
    http_port      = var.jenkins_http_port
    agent_port     = var.jenkins_agent_port
    home_dir       = var.jenkins_home_dir
    tz             = var.jenkins_tz
    uc_mirror      = var.jenkins_uc_mirror
    cpu_limit      = var.jenkins_cpu_limit
    memory_limit   = var.jenkins_memory_limit
  })

  # ---------------------------------------------------------------------------
  # 脚本配置
  #
  # 整份配置打包成 JSON，再 base64 编码后替换脚本里的单个占位符。
  #
  # 为什么用 base64 而不是直接把 JSON 拼进 Python 源码：
  #   base64 的字符集只有 [A-Za-z0-9+/=]，不含引号/反斜杠/换行/$，
  #   所以既不用转义，也不可能意外截断 Python 字符串或 HCL 模板。
  #   代价是渲染后的脚本人看不懂那串配置 —— 需要时用 WF_CONFIG_JSON
  #   环境变量跑，见 scripts/jenkins-compose.py 顶部说明。
  #
  # 对比旧写法：原来每个字段都要一个 replace()（10 个渲染 compose、
  # 7 个渲染脚本），还得小心占位符之间不构成前缀关系。现在只剩一个。
  # ---------------------------------------------------------------------------
  jenkins_script_config = {
    container        = var.jenkins_container_name
    image            = var.jenkins_image
    http_port        = var.jenkins_http_port
    agent_port       = var.jenkins_agent_port
    inner_http_port  = 8080
    inner_agent_port = 50000
    home_dir         = var.jenkins_home_dir
    compose_dir      = var.jenkins_compose_dir
    restart_policy   = var.container_restart_policy
    mode             = local.jenkins_mode
    expected_compose = local.expected_compose
    host_mounts      = local.jenkins_host_mounts
  }

  # 命名提醒：本模块内 local 名必须全局唯一。cicd.tf 里另有 jenkins_job_script
  # （那是 Jenkins「任务」的脚本），所以这里叫 jenkins_compose_script。
  jenkins_compose_script = replace(
    file("${path.module}/scripts/jenkins-compose.py"),
    "__WF_CONFIG_B64__",
    base64encode(jsonencode(local.jenkins_script_config)),
  )
}

# ---------------------------------------------------------------------------
# Jenkins compose 栈：核对 / 接管
# ---------------------------------------------------------------------------
resource "null_resource" "jenkins_stack" {
  count = local.do_jenkins ? 1 : 0

  # ⚠️ triggers 有两重作用，别只当成「变更指纹」：
  #   1. 变更检测：这些值变了才会重新执行 provisioner
  #   2. destroy 阶段唯一能读的数据源：销毁 provisioner 只能引用 self.*，
  #      所以清理脚本需要的参数必须全部预先放进来
  #
  #   踩过这个坑：脚本里写了 self.triggers.xxx，但 triggers 里没定义这个 key
  #   报错信息指向的很偏，不好查。改动 destroy 脚本时，先把用到
  #   self.triggers.* 全列出来，再回头核对 triggers 有定义 —— 顺序反了必然漏。
  triggers = {
    container   = var.jenkins_container_name
    image       = var.jenkins_image
    http_port   = tostring(var.jenkins_http_port)
    agent_port  = tostring(var.jenkins_agent_port)
    home_dir    = var.jenkins_home_dir
    compose_dir = var.jenkins_compose_dir
    restart     = var.container_restart_policy
    mode        = local.jenkins_mode

    # 配置指纹：期望值（含渲染后的 compose 内容）全在 jenkins_script_config 里，
    # 所以这一个哈希就能覆盖原先的 expected_hash + 各字段 trigger。
    # 另留脚本哈希，保证「只改了脚本逻辑」也会触发重跑。
    config_hash = sha1(jsonencode(local.jenkins_script_config))
    script_hash = filesha1("${path.module}/scripts/jenkins-compose.py")

    # --- 以下几项只有 destroy 脚本用得到，见上面说明 ---
    server_host     = local.server.host
    ssh_user        = local.server.user
    ssh_key         = local.server.ssh_key
    ssh_port        = tostring(var.ssh_port)
    down_on_destroy = tostring(var.jenkins_stack_down_on_destroy)
  }

  # 依赖盘点：先看清现状，再谈核对
  depends_on = [null_resource.inventory_containers]

  lifecycle {
    precondition {
      condition     = var.jenkins_http_port > 0 && var.jenkins_http_port <= 65535
      error_message = "jenkins_http_port 必须是有效端口号。"
    }

    precondition {
      condition     = var.jenkins_home_dir != "" && startswith(var.jenkins_home_dir, "/")
      error_message = "jenkins_home_dir 必须是绝对路径（如 /data/jenkins-data）—— 它是 bind mount 的源目录，相对路径会被 docker 拒绝。"
    }

    precondition {
      condition     = var.jenkins_compose_dir != "" && startswith(var.jenkins_compose_dir, "/")
      error_message = "jenkins_compose_dir 必须是绝对路径（如 /data/jenkins-data）。"
    }

    # 端口撞车的预防：Jenkins 的 8081 与 Harbor 的 80 在实际部署里不冲突，
    # 但把这两个值配成一样就是个必错的配置，在这里先拦掉。
    precondition {
      condition     = var.jenkins_http_port != var.harbor_http_port
      error_message = "jenkins_http_port 不能与 harbor_http_port 相同，否则两个服务会抢同一个宿主端口。"
    }
  }

  # -------------------------------------------------------------------------
  # 核对 / 接管
  #
  # 脚本与配置都通过环境变量传给本机 bash（多行环境变量已实测可靠：
  # 引号、$、反引号、base64 都能原样保留），避免在 heredoc 里做转义。
  #
  # 远端执行用 `python3 -u -`：
  #   -u 关掉输出缓冲，否则报告会整段憋到最后才出现，
  #   与 ssh 通道的错误输出顺序错乱，看起来像「卡住了」。
  # -------------------------------------------------------------------------
  provisioner "local-exec" {
    interpreter = ["bash", "-c"]

    environment = {
      JENKINS_COMPOSE_SCRIPT = local.jenkins_compose_script
    }

    command = <<-EOT
      set -euo pipefail

      if [ "${local.jenkins_mode}" = "verify" ]; then
        echo "===== 核对 Jenkins compose 栈（只读模式：只报告，不改动）====="
      else
        echo "===== 接管 Jenkins compose 栈（reconcile：差异处会覆写并重启）====="
      fi

      # 先探测 python3，避免把「远端没有解释器」误报成脚本业务错误
      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=yes \
               -i "${local.server.ssh_key}" -p ${var.ssh_port} \
               "${local.server.user}@${local.server.host}" 'command -v python3' >/dev/null 2>&1; then
        echo "  ✗ 主节点上没有 python3，无法执行核对脚本" >&2
        echo "     安装：sudo apt-get install -y python3   （Ubuntu / Debian）" >&2
        exit 1
      fi

      printf '%s' "$JENKINS_COMPOSE_SCRIPT" | ssh \
        -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 \
        -i "${local.server.ssh_key}" -p ${var.ssh_port} \
        "${local.server.user}@${local.server.host}" 'python3 -u -'

      echo "===== 核对结束 ====="
    EOT
  }

  # -------------------------------------------------------------------------
  # 销毁：默认什么都不做
  #
  # ★ 默认不停止 Jenkins 栈。理由：你多半只是想把 Terraform 这层管理撤掉，
  #   而不是想让 CI 停摆 —— 这和 cicd_cleanup_on_destroy 是同一个判断。
  #
  # ★ 即使打开，也只用 compose stop（容器保留，start 即可恢复），
  #   绝不用 down -v —— 本配置在任何情况下都不删数据。
  #
  # ★ 注意：destroy provisioner 不能引用 var，所以 down_on_destroy、
  #   ssh_* 这些都必须从 self.triggers 读。
  # -------------------------------------------------------------------------
  provisioner "local-exec" {
    when        = destroy
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -uo pipefail

      if [ "${self.triggers.down_on_destroy}" != "true" ]; then
        echo "[keep] Jenkins 栈按配置保持运行，未做任何改动。"
        echo "       （jenkins_stack_down_on_destroy = false；如需停止请改为 true）"
        exit 0
      fi

      echo "[stop] 停止 Jenkins 栈（容器保留，数据目录 ${self.triggers.home_dir} 不动）"

      if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=yes \
               -i "${self.triggers.ssh_key}" -p ${self.triggers.ssh_port} \
               "${self.triggers.ssh_user}@${self.triggers.server_host}" true 2>/dev/null; then
        echo "  [warn] 连不上主节点，跳过（栈可能仍在运行）"
        exit 0
      fi

      ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
          -i "${self.triggers.ssh_key}" -p ${self.triggers.ssh_port} \
          "${self.triggers.ssh_user}@${self.triggers.server_host}" '
        FILE="${self.triggers.compose_dir}/docker-compose.yaml"
        if [ ! -f "$FILE" ]; then
          echo "  找不到 compose 文件: $FILE"
          exit 0
        fi
        if docker compose version >/dev/null 2>&1; then
          docker compose -f "$FILE" stop
        else
          docker-compose -f "$FILE" stop
        fi
        echo "  已停止。恢复方式: docker compose -f $FILE start"
        echo "  数据目录 ${self.triggers.home_dir} 未改动。"
      ' 2>/dev/null || echo "  [warn] 远程停止失败，请手工检查"
    EOT
  }
}

# ---------------------------------------------------------------------------
# Harbor：明确不接管（不是「暂未接管」）
#
# 你的 Harbor 是官方默认配置部署，也就是 harbor.yml 未改动、
# 用 install.sh 生成的 docker-compose.yml 起来的，约 9 个容器：
#   nginx / harbor-portal / harbor-core / registry / registryctl /
#   harbor-db / redis / harbor-jobservice / harbor-log
#
# 这个资源只是个提示器，把「为什么不管」打印到 apply 输出里，
# 免得以后有人看到代码里没有 Harbor 而以为是遗漏。
# 详见 variables.tf 中 manage_harbor 的说明。
# ---------------------------------------------------------------------------
resource "null_resource" "harbor_notice" {
  count = !var.manage_harbor ? 1 : 0

  triggers = {
    http_port  = tostring(var.harbor_http_port)
    https_port = tostring(var.harbor_https_port)
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      echo "----------------------------------------------------------------"
      echo "Harbor：容器层不由 Terraform 管理（这是刻意的）"
      echo ""
      echo "  你的 Harbor 是官方默认配置部署 —— 一个约 9 容器的 compose 栈："
      echo "    nginx / harbor-portal / harbor-core / registry / registryctl"
      echo "    harbor-db / redis / harbor-jobservice / harbor-log"
      echo "  端口: HTTP ${var.harbor_http_port} / HTTPS ${var.harbor_https_port}"
      echo ""
      echo "  不接管的三条理由："
      echo "    1. 官方没有单容器发行版，用 docker run 复刻这个栈等于手写一遍"
      echo "       Harbor 的编排逻辑，失败概率很高"
      echo "    2. 代价不对称 —— 栈被搞坏不是「停一会儿」，而是整条流水线的"
      echo "       制品（所有已推送镜像 + 标签 + 扫描结果）全没了"
      echo "    3. 没有收益 —— Harbor 的日常变更都在 API 层，而那层本配置是管的"
      echo ""
      echo "  Harbor 的容器与配置：继续用 docker compose 与 harbor.yml 管。"
      echo "  Harbor 的项目与 webhook 策略：由 manage_cicd 接管。"
      echo "----------------------------------------------------------------"
    EOT
  }
}
