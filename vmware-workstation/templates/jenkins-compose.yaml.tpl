# ===========================================================================
# Jenkins compose 栈 —— 期望状态模板
#
# ★ 本文件是模板，不是真正生效的 compose 文件。
#   由 containers.tf 通过 templatefile() 渲染，结果用于：
#     1) 与磁盘上的 compose 文件做「语义比对」（只报告差异）
#     2) jenkins_manage_compose = true 时，覆写成真正的 compose 文件
#        （覆写前会留 .bak 备份）
#
# ---------------------------------------------------------------------------
# 关于 $${name} 占位符
#
# 这里的 $${name} 是 Terraform 的 templatefile() 变量，取值见 containers.tf
# 里 templatefile(...) 的第二个参数。写法比原来那一长串
# replace(x, "__IMAGE__", ...) 可读得多，而且变量名写错会在 plan 阶段直接报错。
#
# 如果你更希望继续手工维护真正的 compose 文件，把 jenkins_manage_compose
# 设为 false（默认），Terraform 就只核对、不覆写。
#
# ---------------------------------------------------------------------------
# 本模板对应你原来的 /data/jenkins-data/docker-compose.yaml，
# 只把可以参数化的部分换成了变量，挂载结构保持原样。
# ===========================================================================
version: '3.8'
services:
  jenkins:
    image: ${image}
    container_name: ${container_name}
    restart: ${restart_policy}
    privileged: true
    user: root
    ports:
      - "${http_port}:8080"
      - "${agent_port}:50000"
    volumes:
      - ${home_dir}:/var/jenkins_home
      - /var/run/docker.sock:/var/run/docker.sock
      - /usr/bin/docker:/usr/bin/docker
      # 单独挂载两个二进制，不覆盖容器 /usr/local/bin
      - /usr/local/bin/k3s:/host-bin/k3s:ro
      - /usr/local/bin/kubectl:/host-bin/kubectl:ro
      - /etc/rancher/k3s:/etc/rancher/k3s:ro
      - /root/.kube:/root/.kube:ro
    environment:
      JAVA_TOOL_OPTIONS: "-Djava.security.egd=file:/dev/./urandom"
      TZ: ${tz}
      JENKINS_UC: ${uc_mirror}
      JENKINS_UC_DOWNLOAD: ${uc_mirror}
    deploy:
      resources:
        limits:
          cpus: '${cpu_limit}'
          memory: ${memory_limit}
