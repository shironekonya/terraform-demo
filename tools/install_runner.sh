#!/usr/bin/env bash
# ===========================================================================
# install_runner.sh —— 在 192.168.193.147 上装一个 GitLab Runner（shell executor）
#
# 为什么 runner 装在 147 而不是 GitLab 那台（139）：
#
#   流水线要干的事是「terraform + ansible 去改云机器和本机」。
#   147 上已经有：terraform 1.16、ansible-core 2.21、docker、以及 ECS 的 SSH key，
#   而且它和 GitLab(139) 在同一个内网段。runner 放在这里，等于
#   "工具链在哪儿，执行就在哪儿" —— 不需要在 CI 镜像里重装一遍这些。
#
# 为什么是 shell executor 而不是 docker executor：
#
#   docker executor 每次都要拉一个镜像，而镜像里没有 terraform 的 provider 缓存、
#   没有 ~/.ssh、没有 ~/.aliyun —— 于是 CI 里第一件事变成"先配环境"，
#   和"用 CI 收敛变更"这个目的正好相反。shell executor 直接用宿主机工具链，
#   代价是隔离性差：**这套配置只适合单机、自用、可信仓库**。
#
# 用法：
#   sudo GITLAB_URL=http://192.168.193.139:8000 \
#        RUNNER_TOKEN=glrt-xxxxxxxxxxxx \
#        ./install_runner.sh
#
#   RUNNER_TOKEN 有两种来源，脚本都认：
#     · 新建 runner 页面给的「认证 token」（glrt- 开头）—— 推荐
#     · 旧版注册 token（项目/群组 Settings → CI/CD → Runners）
#
# 幂等：重复执行不会重复注册；已注册就只做一次 verify。
# ===========================================================================
set -euo pipefail

GITLAB_URL="${GITLAB_URL:-http://192.168.193.139:8000}"
RUNNER_TOKEN="${RUNNER_TOKEN:-}"
RUNNER_TAG="${RUNNER_TAG:-wf-infra}"
RUNNER_DESC="${RUNNER_DESC:-$(hostname)-shell}"
CONFIG=/etc/gitlab-runner/config.toml

die() { echo "✗ $*" >&2; exit 1; }
say() { echo "── $*"; }

[[ $EUID -eq 0 ]] || die "要用 root 跑（shell executor 会以 root 身份执行 terraform / ansible）"

# ---------------------------------------------------------------------------
# 1. 装二进制（apt 源）—— 已装就跳过
# ---------------------------------------------------------------------------
if command -v gitlab-runner >/dev/null 2>&1; then
  say "gitlab-runner 已安装：$(gitlab-runner --version | head -1)"
else
  say "安装 gitlab-runner（packages.gitlab.com）"
  # 147 能直达 packages.gitlab.com（实测 200），所以走官方源；
  # 万一哪天不通，再退化成手工下 deb —— 但不要在脚本里悄悄换源。
  curl -sSL "https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh" | bash
  DEBIAN_FRONTEND=noninteractive apt-get install -y gitlab-runner
fi

# ---------------------------------------------------------------------------
# 2. 注册 —— 同一个 GitLab 只注册一次
# ---------------------------------------------------------------------------
if grep -qs "url = \"${GITLAB_URL}\"" "$CONFIG"; then
  say "已经注册过 ${GITLAB_URL}，跳过注册"
else
  [[ -n "$RUNNER_TOKEN" ]] || die "没有 RUNNER_TOKEN：GitLab → Settings → CI/CD → Runners 里取一个"

  # 两种 token 的传参位置不同，混用会得到一句很含糊的 "invalid token"。
  # 另一处差异：glrt-（authentication token）模式下 tag/locked/access-level 等
  # 配置**只能在 GitLab 服务端改**（建 runner 的地方），register 命令带这些参数
  # 会直接 FATAL —— 所以旧式 registration-token 才传，glrt- 一律不传。
  if [[ "$RUNNER_TOKEN" == glrt-* ]]; then
    TOKEN_FLAG="--token"
    EXTRA_ARGS=()
  else
    TOKEN_FLAG="--registration-token"
    EXTRA_ARGS=(--tag-list "$RUNNER_TAG" --run-untagged=false --locked=false)
  fi

  say "注册 runner（executor=shell，token 类型：${RUNNER_TOKEN:0:5}…）"
  gitlab-runner register --non-interactive \
    --url "$GITLAB_URL" \
    "$TOKEN_FLAG" "$RUNNER_TOKEN" \
    --executor shell \
    --shell bash \
    --description "$RUNNER_DESC" \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
fi

# ---------------------------------------------------------------------------
# 3. 收敛配置
#
#   concurrent = 1 不是性能考虑，是正确性考虑：
#   Terraform 的 state 锁能挡住并发 apply，但 ansible 侧没有锁；
#   两个 job 同时跑同一台机器，改的是同一批文件，谁最后写谁赢 ——
#   这种"并发"没有任何收益，只会让失败变得难复现。
# ---------------------------------------------------------------------------
say "收敛 $CONFIG · 探测工具链路径"
TOOL_PATHS="$(python3 - <<'PY'
import os, pathlib, shutil

dirs = []
for name in ("ansible-playbook", "terraform", "python3"):
    found = shutil.which(name) or shutil.which(name, path="/root/.local/bin:" + os.environ.get("PATH", ""))
    if not found:
        continue
    parent = str(pathlib.Path(found).parent)
    if parent not in dirs:
        dirs.append(parent)
print(":".join(dirs))
PY
)"
[[ -n "$TOOL_PATHS" ]] || die "连 python3 / terraform / ansible-playbook 都探测不到 —— 先把工具链装好再跑这个脚本"
say "  PATH 前缀：$TOOL_PATHS"

python3 - "$CONFIG" "$TOOL_PATHS" <<'PY'
import re, sys, pathlib

path = pathlib.Path(sys.argv[1])
extra = sys.argv[2]
text = path.read_text() if path.exists() else ""

if re.search(r"^concurrent\s*=", text, re.M):
    text = re.sub(r"^concurrent\s*=.*$", "concurrent = 1", text, count=1, flags=re.M)
else:
    text = "concurrent = 1\n" + text

full_path = extra + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
env_line = '  environment = ["PATH=%s"]' % full_path
if re.search(r"^\s*environment\s*=", text, re.M):
    # 已有一条就替换 —— 保留两份 PATH 会让第二条静默失效
    text = re.sub(r"^\s*environment\s*=.*$", env_line, text, count=1, flags=re.M)
else:
    text = text.replace(
        '  executor = "shell"',
        '  executor = "shell"\n' + env_line,
        1,
    )

path.write_text(text)
print(text)
PY

# ---------------------------------------------------------------------------
# 4. 起服务并自证
# ---------------------------------------------------------------------------
say "启动服务"
gitlab-runner install --user root --working-directory /root/gitlab-runner 2>/dev/null || true
systemctl enable --now gitlab-runner
sleep 2

say "服务状态"
systemctl is-active gitlab-runner
gitlab-runner verify 2>&1 | tail -5

# ---------------------------------------------------------------------------
# 5. 自证：服务的运行用户必须读得到 /root 下的收敛文件
#
# runner 若以 gitlab-runner 用户跑（系统包默认），而 /root 是 700，
# gray:apply 会在"找不到 /root/.wf-aliyun.tfvars"上失败 —— 那个报错
# 离真因（服务的 User= 不对）十万八千里，所以在这里当场验一次。
# verify valid 只代表 token 有效，不代表文件可见。
# ---------------------------------------------------------------------------
if [ -f /root/.wf-aliyun.tfvars ] || [ -f /root/.wf-aliyun.env ]; then
  SVC_USER="$(systemctl show -p User --value gitlab-runner 2>/dev/null || echo gitlab-runner)"
  if [ "${SVC_USER}" != "root" ]; then
    cat <<WARN

  ⚠ gitlab-runner 服务以 ${SVC_USER} 运行，读不到 /root（700）下的收敛文件 ——
    gray:apply 会失败在"找不到 /root/.wf-aliyun.tfvars"，看似 tfvars 没收敛，
    实际是服务用户不对。切 root（一次性）：

      sudo mkdir -p /etc/systemd/system/gitlab-runner.service.d
      printf '[Service]\nUser=root\n' | sudo tee /etc/systemd/system/gitlab-runner.service.d/override.conf
      sudo systemctl daemon-reload && sudo systemctl restart gitlab-runner

WARN
  else
    say "服务以 root 运行，/root 下的收敛文件可见"
  fi
fi

cat <<EOF

✓ 完成。接下来在 GitLab 侧确认三件事：

  1. Settings → CI/CD → Runners 里，${RUNNER_DESC} 是绿色（online）
  2. 项目里配好 CI/CD Variables：
       ALICLOUD_ACCESS_KEY / ALICLOUD_SECRET_KEY   （Masked）
       WF_SSH_KEY_FILE                             （File 类型，ECS 私钥）
  3. 147 上这份仓库的工作区必须是干净的 ——
     runner 直接在 ${CI_PROJECT_DIR:-/data/terraform-demo-ci} 里跑，脏工作区会让 plan 的差异说不清。

  自测：随便改一行 README 推到默认分支，看 validate 两个 job 是否变绿。
EOF
