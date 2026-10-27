# LOGIC：实现与操作逻辑

README 的展开：第一部分讲代码怎么组织、边界怎么定，第二部分讲怎么跑、怎么验、怎么排错。
每条设计决定都和它要防的失败场景写在一起。

## 目录

**第一部分 · 实现逻辑**

- [仓库结构与职责划分](#仓库结构与职责划分)
- [本地环境拓扑](#本地环境拓扑已存在不由-terraform-创建)
- [Terraform 管不了已存在的虚机](#核心概念terraform-管不了已存在的虚机)
- [分层边界（九条）](#分层边界谁管什么不越界)
- [CI 是唯一触发器](#cicd-是唯一触发器把变更收敛成一条路径)
- [跨层衔接与豁免登记](#跨层衔接默认零条每条都要写出来)
- [state 放哪](#state-放哪本地--远程含加锁)
- [本地部分的设计考虑](#本地部分的代码设计考虑)
- [vSphere 参考版本](#vsphere-参考版本vmware-vsphere-reference)
- [两个环境怎么配合](#两个环境怎么配合使用)

**第二部分 · 操作逻辑**

- [启动路径](#启动路径按什么顺序跑)
- [阶段一 · 阿里云](#阶段一--阿里云部分)
- [阶段二 · 本地三档递进](#阶段二--本地部分三档递进)
- [阶段三 · CI/CD 流水线](#阶段三--cicd-流水线)
- [阶段四 · GitLab 流水线入口](#阶段四--gitlab-流水线入口)
- [阶段五 · Ansible](#阶段五--ansible批量管理云服务器与本地服务器)
- [环境要求](#环境要求)
- [验证状态](#验证状态)
- [踩坑清单](#踩坑清单)
- [成本与风险提示](#成本与风险提示)

两条流水线别混淆：`.gitlab-ci.yml` 改的是**环境**（基础设施变更，唯一路径）；
阶段三/四（Harbor webhook → Jenkins、GitLab push）管的是**应用交付**。两者不能互相调用。

---

# 第一部分 · 实现逻辑

## 仓库结构与职责划分

```
terraform-demo/
├── aliyun-ecs/                  # ① 阿里云：VPC / 交换机 / 安全组 / ECS
│   ├── versions.tf / variables.tf / network.tf / ecs.tf / outputs.tf
│   ├── backend.tf.example       #   远程 state + TableStore 锁（默认不生效）
│   └── terraform.tfvars.example
│
├── vmware-workstation/          # ② 本地：管理已存在机器上的容器栈与流水线
│   ├── inventory.tf             #   只读盘点：节点、容器、compose 栈、K3s、GitLab 状态
│   ├── containers.tf            #   Jenkins compose 栈的核对/接管
│   ├── cicd.tf                  #   Harbor 项目/webhook、Jenkins 任务（幂等）
│   ├── gitlab.tf                #   GitLab 项目、Push webhook（幂等）
│   ├── templates/jenkins-compose.yaml.tpl
│   ├── scripts/                 #   inventory / jenkins-compose / harbor-webhook /
│   │                            #   jenkins-job / gitlab-project（全是 Python，只读标准库）
│   └── archive-vm-clone/        #   停用旧方案（已登记豁免）
│
├── vmware-vsphere-reference/    # ③ vSphere provider 写法参考（已登记豁免，跑不起来）
├── ansible/                     # ④ 批量管理：①的云机器 + ②的存量机器
│   ├── ansible.cfg              #   控制端必须是 Linux / macOS / WSL（依赖 os.fork）
│   ├── inventory/               #   hosts.yml（本地，手写）、hosts.cloud.yml（生成物）
│   │   └── group_vars/          #   必须在清单旁，放错位置会被静默忽略（踩坑 24）
│   ├── roles/guard|audit|baseline|docker_engine|app_stack
│   ├── playbooks/site.yml       #   唯一入口，五幕；verify.yml 纯只读
│   └── tools/                   #   gen_cloud_inventory / check_guard / selftest
├── tools/check_boundaries.py    # ⑤ 九条边界的静态校验
├── .gitlab-ci.yml               # 变更的唯一触发器
└── boundaries.yml               # 跨层衔接声明（默认零条）+ 豁免登记
```

①②③ 各自独立管理 state，可以分别 `apply` / `destroy`。`ansible/` 是没有 state 的编排层，
复用 ① 的云机器 IP 和 ② 的存量机器，刻意不与 Terraform 争抢同一个对象。
`tools/`、`.gitlab-ci.yml`、`boundaries.yml` 是边界的裁判与载体，不属于任何一层。

## 本地环境拓扑（已存在，不由 Terraform 创建）

```
┌──────────────────────────────────────────────┐
│  K3s 主节点（server） 192.168.193.147          │
│  · K3s 控制平面                               │
│  · Jenkins ← docker-compose 栈（宿主 8081）    │
│  · Harbor  ← docker-compose 栈（~9 容器，80/443）│
└──────────────────────────────────────────────┘
                   │
┌──────────────────────────────────────────────┐
│  K3s 从节点（agent）  192.168.193.139          │
│  · K3s 工作负载                               │
│  · GitLab  ← Omnibus 实体服务（系统包安装）     │
└──────────────────────────────────────────────┘
```

三个服务都不属于 K8s 工作负载。部署方式决定 Terraform 管到哪一层：

| 服务 | 部署方式 | Terraform 管到哪一层 |
|---|---|---|
| Jenkins | docker-compose | 只核对 compose 栈；默认不覆写文件 |
| Harbor | docker-compose（官方默认配置） | 容器层完全不碰；只管项目与 webhook |
| GitLab | Omnibus 实体服务 | 服务本身完全不碰；只管 API 层（项目 + webhook） |

原则：**凡是已经有一份自己的声明式配置的东西（compose 文件、gitlab.rb），Terraform 不去当第二个源头。**

两台机器角色不同：主节点是构建与制品侧（Jenkins、Harbor），从节点是流水线入口（GitLab）。
声明文件的脚本分别 `ssh` 到对应节点执行；Harbor/GitLab 回调 Jenkins 都写**主节点 IP + 8081**。

## 核心概念：Terraform 管不了已存在的虚机

VMware Workstation 没有官方 provider（HashiCorp 只维护 `vsphere`，面向 vCenter/ESXi）；
用 `null_resource` 也无法 import —— import 的前提是有对应 resource 类型且有 `read` 实现，
`null_resource` 只是"脚本跑过没有"的标记。所以分层是：

| 层次 | 归谁管 | 原因 |
|---|---|---|
| 虚拟机本身 | VMware Workstation（手工 / vmrun） | 无 provider，无法 import |
| 操作系统配置 | 配置管理工具（Ansible） | 不是 Terraform 的强项 |
| 容器进程 | docker compose | compose 文件本身就是声明式配置 |
| 容器之上的配置（Harbor 项目/webhook、Jenkins 任务、GitLab 项目/webhook） | Terraform（`vmware-workstation/`） | 走 HTTP API，与安装方式无关 |

Terraform 只能管它「创造得出来」、或有 provider 支持 read、或只有它一个声明式源头的东西。

## 分层边界：谁管什么，不越界

| # | 边界 | 一句话理由 |
|---|---|---|
| B1 | Terraform 管「资源是否存在、拓扑是否正确」 | 它擅长资源存在性，不擅长机器内部状态 |
| B2 | Ansible 管「机器 / 服务内部状态」 | 幂等 + 可重复执行 |
| B3 | K8s 工作负载用 K8s 工具，不进 Terraform | 发布节奏和基础设施节奏是两件事 |
| B4 | 不拿 Ansible 当 Terraform 的触发器 | 否则 apply 报成功、后半截失败，state 里记的却是成功 |
| B5 | state 放远程后端 + 开状态锁 | 本地 state + 无锁 = 并发 apply 互相覆盖，产生 destroy 不掉的幽灵资源 |
| B6 | Python / Shell 只做胶水，不替代状态管理 | 生成 tfvars、解析 output、健康检查可以；碰 state 文件不行 |
| B7 | CI/CD 是变更的唯一触发器 | 否则「以谁的本地环境为准」永远没有答案 |
| B8 | 跨层衔接显式声明，且只能只读单向 | 默认零条 + 每条写出来给人 review |
| B9 | 宿主机虚拟化平台不进仓库运行路径 | 控制端会被钉死在一台机器上，不可复现、上不了 CI |

每条边界都配了可执行的静态检查（`tools/check_boundaries.py`，纯标准库）：

```bash
python3 tools/check_boundaries.py                      # 逐条报状态
python3 tools/check_boundaries.py --selftest           # 69 个用例，证明检查器会响
python3 tools/check_boundaries.py --require-remote-state   # CI 用：没有远程后端直接失败
python3 tools/check_boundaries.py --require-ci             # CI 用：没有流水线定义直接失败
```

| 规则 | 拦什么 |
|---|---|
| B1 | `*.tf` 或胶水脚本里调 `ansible-playbook`（只看会被执行的命令，注释与提示文本不算） |
| B2 | `ansible/` 里跑 `terraform apply / destroy / import / state rm…`；只允许 `output / show / plan / validate / fmt / version` |
| B3 | `kubernetes_deployment` / `helm_release` / `kubectl_manifest` 等闭集（`kubernetes_namespace` 这类周边对象不算） |
| B4 | `backend "local"`；oss 后端配了锁表缺 endpoint；backend 里硬编码凭证；缺锁表给提示（并发保护靠 runner `concurrent = 1`） |
| B5 | 脚本里 `open('terraform.tfstate')`、provisioner 碰 state 文件 |
| B6 | provisioner / 胶水脚本里 `apt-get install`、`systemctl restart`（`user_data` 豁免：那是实例启动配置） |
| B7 | 没有流水线定义；TF 与 Ansible 在同一个 job；自动变更 job 既没有人工门也没限定默认分支；没有回滚 job；`site.yml` 不带 `--limit`；碰 local 的 job 没有人工放行；解锁开关被悄悄带上；一键绕过流水线的入口脚本；job 没有 `tags` 也没有 `default.tags` 兜底 |
| B8 | 代码里有跨层调用但没声明；声明 `mode: write`；`to` 不是 terraform；缺 `why`；声明了但代码里找不到；子集之外的 YAML 写法 |
| B9 | `vsphere_virtual_machine` / `provider "vsphere"` / `vmrun` 等调用 / `.vmx` `.vmdk` 路径；豁免清单缺 `why`、登记不存在的路径 |

判定原则：**只看会被执行的命令。** 提示文本里的 `apt-get install` 不是越界，
连提示都算的话检查器第一天就会被无视、删掉。

当前状态：B1~B9 全部通过。`aliyun-ecs` / `vmware-workstation` 已配 `backend.tf`
（同一个 OSS 桶 `cloud-native-demo`，不同 prefix），桶由 `state-bootstrap/` 用本地 state 自举一次。
状态锁不配 OTS，并发保护由 runner `concurrent = 1` 承担 —— 加第二个 runner 前必须补锁。

### CI 是唯一触发器：把变更收敛成一条路径

`.gitlab-ci.yml` 是仓库里唯一能改动环境的路径，全自动蓝绿灰度：

```
git push
  → gray:slot     算槽位：哪边有机器哪边在服务（active），这次建另一边（new）
  → gray:apply    在新槽位整套建资源（旧槽位在另一个 workspace 里，物理碰不到）
  → gray:configure  ansible 初始化新机器（版本 = commit 短 SHA）
  → gray:health   请求新机器 nginx：200 且 <meta wf-release> = 本次 commit
  → gray:cutover  过了 → 销毁旧槽位，新机器成为现网
     gray:rollback 没过 → 销毁新槽位（when: on_failure），旧机器毫发无损
  → gray:notify   收场报告（通知本体 = 流水线状态；可配 NOTIFY_WEBHOOK）
```

blue / green 各是一个 terraform workspace（OSS state 按 workspace 前缀隔离），
资源名带槽位后缀（`name_prefix` 拼 `terraform.workspace`，否则两套资源的 key pair 撞名）。

三条设计约束：

1. **Terraform 与 Ansible 是两个独立 job，谁都不调用谁。** `gray:apply` 把 `terraform output -json`
   存成 `.ci-artifacts/tf-outputs.json`，`gray:configure` 用 `gen_cloud_inventory.py --from-output-json`
   消费。Ansible 那个 job 里没有 state、没有云凭证 —— 越界不是「约好了不做」，而是做不到。
2. **全自动变更的两条硬护栏**（替代人工门的安全网，由 B7 盯着）：变更 job 必须限定默认分支；
   流水线必须存在回滚 job。`teardown`（整组销毁）等不可逆动作仍保留人工门。
3. **Ansible 调用一律显式 `--limit`**，碰 `local` 的 job 再叠一道 `when: manual`，
   与 Ansible 侧的 `local_mutation_allowed` 形成两道门。

### 跨层衔接：默认零条，每条都要写出来

现实里总有一两次数据要跨层（最典型：云机器的 IP 只有 Terraform 知道）。形态是
默认零条 + 显式声明，放在 `boundaries.yml`：

```yaml
handoffs:
  - from: ansible
    to: terraform        # 只能填 terraform
    mode: read           # 只能填 read
    where: ansible/tools/gen_cloud_inventory.py
    why: "云服务器的 IP 只有 Terraform 知道……"
```

能被声明的只有「某一层只读地读 Terraform 的输出」这一种形状；反方向（调 Ansible）是 B1 的地盘。
没有声明文件 = 一条都不允许。方向钉死后「单向」是结构性保证，环构造不出来 ——
所以**没有**成环检测：一个永远不会响的检查比没有检查更糟。

声明文件的解析器刻意写窄（顶层键 0 缩进、列表项 2、字段 4），子集之外的写法一律报错并给行号。
猜错的后果是「声明被静默忽略」，比没有声明更危险。

流水线路径不需要任何声明（跨层调用数是 0）；这份清单管的是人在自己机器上手工跑的路径。

### 豁免登记：不参与运行的东西也得登记在册

不参与运行的代码（停用旧方案、语法参考）不必遵守运行时边界，但「谁不参与运行」不能靠目录名猜：
改名会静默失效，反过来真在跑的代码塞进叫 `archive-` 的目录就永远查不到。所以豁免登记在册：

```yaml
exemptions:
  - path: vmware-workstation/archive-vm-clone
    why: "停用的旧方案……"
  - path: vmware-vsphere-reference
    why: "语法参考……"
```

两条纪律：豁免的语义是「不参与运行」，对**所有**检查生效，不搞每条规则一个跳过开关；
豁免清单自身也被检查（缺 `why`、登记不存在的路径都会报）。判据是路径段不是字符串前缀（踩坑 22）。

### state 放哪：本地 → 远程（含加锁）

本地 state 的两个后果：并发 apply 互相覆盖（资源真实存在但 state 里没有 —— 幽灵资源，持续计费）；
state 里是明文凭证，散落在每个人电脑上。单人练手够用，多人协作必须换：

```bash
# 1) 建底层资源：专用 OSS 桶（本项目由 state-bootstrap/ 建，桶名 cloud-native-demo）
#    状态锁（可选）：OTS 实例 + 锁表（LockID 列，String 类型）
# 2) 启用后端
cd aliyun-ecs
cp backend.tf.example backend.tf      # 填掉占位符
terraform init -migrate-state
# 3) 验证
terraform plan                        # 必须是 "No changes"，否则别继续 apply
```

凭证走环境变量（`ALICLOUD_ACCESS_KEY` / `ALICLOUD_SECRET_KEY`），不要写进 `backend.tf`。
本地 VMware 部分用同一个 OSS 桶 + 不同 prefix；不许依赖公有云时等价替换是 `pg` / `consul` / 内网 MinIO。

### 为什么 K8s 工作负载不写进 Terraform

| 用谁管 | 适合的节奏 |
|---|---|
| Terraform | 基础设施生命周期：VPC、ECS、集群、安全组 |
| K8s YAML + git | 应用编排，和代码一起 review |
| Helm | 带参数的发布 |
| ArgoCD / Flux | 多环境持续交付、自动纠偏 |

`terraform apply` 一次可能动 20 个资源；应用发布要灰度、回滚、看指标，塞进前者会出现
「改一个镜像 tag 导致 plan 里 3 个资源要重建」。本仓库 `kubectl` 只出现在只读盘点里，不越界。

## 本地部分的代码设计考虑

- **盘点与变更分离**：`inventory.tf` 全只读；变更由 `manage_containers` 开关控制，默认 `false`，第一次 `apply` 只盘点。
- **不"创建容器"，只"核对编排栈"**：Jenkins 由 compose 管，compose 文件本身就是声明式配置。
  Terraform 再 `docker run` 一个同名容器，除了端口冲突和丢掉 6 个宿主资源挂载没有别的结果。
  所以 `containers.tf` 里只有一个 `null_resource.jenkins_stack`：核对运行态、核对 compose 文件、按档位决定动不动手。
- **触发器放配置指纹**：`triggers` 里是 `sha1(配置)`、`filesha1(脚本)` 和关键字段。放时间戳/随机值会导致每次 apply 都重建。
- **SSH 参数必须进 triggers**：destroy provisioner 只能引用 `self.*`（连 `interpreter` 里放变量路径都报
  `Invalid reference from destroy provisioner`），所以 `ssh_key`、`ssh_user`、`ssh_port`、`down_on_destroy` 都预先放进 triggers。
- **脚本走 stdin**：JSON 配置 base64 后注入脚本，`ssh 'python3 -u -'` 送入远端，不落中间文件。
- **绝不写/删用户的 compose 文件**：只读 `/data/jenkins-data/docker-compose.yaml`；
  接管档先备份成 `.bak.<时间戳>` 再覆盖；`destroy` 最多 `compose stop`（且要显式开关），
  永远不会 `down -v`，不碰 `/data/jenkins-data` 目录本身。

脚本用 Python 不用 shell 的原因：手工解析 JSON（`tr | grep | sed`）假设字段顺序固定；
Go 模板一行流转义地狱；HCL heredoc 里 `\\$2`、`$${x}` 双重转义。改写后每个脚本只有一个
`__WF_CONFIG_B64__` 占位符，且支持 `WF_CONFIG_JSON` 环境变量离线调试（不碰真机器）。

base64 包一层配置的理由是字符集：产物只含 `[A-Za-z0-9+/=]`，不需要任何转义，
也不会被 HCL 当成模板插值。代价是渲染后配置不可读 —— 排查时用 `WF_CONFIG_JSON` 方式跑。

compose 模板用 `templatefile()` 不用链式 `replace()`：变量名写错在 plan 阶段直接报错，
`replace()` 写错只会静默不替换。注意模板**注释里**的 `${...}` 也会被解析，字面量要写 `$${...}`。

## 两个环境怎么配合使用

目前两边独立。想打通时的方向：

| 打通方式 | 做法 |
|---|---|
| 网络层 | Workstation 桥接模式，虚机与宿主机同网段 |
| 安全 | 安全组只放行宿主机公网 IP，不开 `0.0.0.0/0` |
| 网段 | 阿里云 `10.10.0.0/16`，本地 `10.20.0.0/24`，错开 |
| 最实用 | 本地流水线构建镜像 → 推 Harbor → 阿里云 ECS 拉取运行（不需要打通网络，Harbor 能被公网访问即可） |
| 进阶 | Terraform 建 VPN 网关 + IPsec；或 remote state 数据源互相引用 |

联动三步：Harbor 公网可达 + 启用 HTTPS → ECS 配 Harbor 凭据 `docker login` → Jenkins 加「部署到云」stage。

## vSphere 参考版本（`vmware-vsphere-reference/`）

展示官方 `hashicorp/vsphere` provider 的写法（datacenter/cluster/datastore 定位、
`clone` + `customize` 定制主机名与固定 IP）。它需要 vCenter 才能跑，已登记豁免，
不参与运行路径，也不被当作在用模块要求配 backend。

保留它的意义：有官方 provider 时资源能被真正声明和追踪（完整 state、可 import、直接拿 IP）
—— 这正是 Workstation 场景缺失的能力。以后用上 vCenter，本地那套 SSH 脚本大部分可以退役。

---

# 第二部分 · 操作逻辑

## 启动路径：按什么顺序跑

| 步骤 | 入口 | 改动什么 | 费用 | 前置 |
|---|---|---|---|---|
| 0 · 静态自检 | `tools/check_boundaries.py`、`ansible/tools/` | 无 | 无 | python3 |
| 1 · 工具链与 lock | `aliyun-ecs/` 的 `terraform init` | 下载 provider | 无 | Terraform、Git Bash |
| 2 · 云端创建→销毁 | 阶段一 | 建 VPC/交换机/安全组/ECS | 按量付费 | AccessKey |
| 3 · 本地只读盘点 | 阶段二第 1 档 | 无 | 无 | 两台节点 SSH 可达 |
| 4 · 核对→接管 | 阶段二第 2/3 档 | 第 3 档覆写 compose 文件 | 无 | 同上 |
| 5 · 应用交付链路 | 阶段三 | Harbor 项目 + webhook、Jenkins 任务壳 | 无 | Harbor 密码、Jenkins Token |
| 6 · GitLab 入口 | 阶段四 | GitLab 项目 + webhook | 无 | GitLab Token |
| 7 · 批量管理 | 阶段五 | 云机器可变；本地默认只读 | 无 | Ansible 控制端（Linux/WSL） |

**最小闭环是 0 → 2 → 3**，覆盖两个主要结论：云资源能整组创建/删除；已存在虚机不会被 Terraform 接管。
第 4 步之后每步都会真的改动环境。

### 第 0 步 · 静态自检

```bash
python3 tools/check_boundaries.py             # 九条边界 + 衔接登记
python3 tools/check_boundaries.py --selftest  # 69 个用例

cd ansible
python3 tools/check_guard.py             # 静态护栏
python3 tools/check_guard.py --selftest  # 19 个用例
python3 tools/selftest.py                # 178 项断言（模板渲染 + 保护公式真值表）
```

CI 里跑的是严格档（`--require-remote-state --require-ci`）；本地首跑用默认档，不被 CI 门槛挡住。

### 第 1 步 · 工具链与 lock 文件

```bash
which bash          # 必须是 Git 安装目录下的 bin/bash.exe；System32 下的是 WSL 的
terraform version   # >= 1.5.0
```

国内网络 `terraform init` 拉 provider 会超时，仓库根目录备好了镜像配置：

```bash
export TF_CLI_CONFIG_FILE="$(pwd)/terraform.rc"
```

`.terraform.lock.hcl` 是提交进仓库的。若它只记了单平台哈希，别的平台 `init` 会报
`doesn't match any of the checksums previously recorded`，补全即可：

```bash
cd aliyun-ecs
terraform providers lock -platform=windows_amd64 -platform=linux_amd64
```

### 第 2 步 · 云端（唯一花钱的一段）

见阶段一。验收三条：`terraform output` 打出 IP；访问 IP 能看到欢迎页（Ansible 跑过之后换成容器栈页面）；
`apply` 之后 `terraform plan` 无变化。收尾 `terraform destroy` 后**再看一眼 plan** 应为空。

#### 回收时要顺带清掉的三样东西

| 遗留物 | 不清的后果 | 怎么清 |
|---|---|---|
| `ansible/inventory/hosts.cloud.yml` | 清单指向已释放 IP，playbook 卡连接超时 | 删文件，下次 apply 后重新生成 |
| `~/.ssh/known_hosts` 里该 IP 的记录 | IP 被回收复用，ssh 报 `REMOTE HOST IDENTIFICATION HAS CHANGED`，第一幕就 UNREACHABLE（踩坑 45） | **已自动化**：`gen_cloud_inventory.py` 生成清单时同步删掉这批 IP 的旧键；手工清用 `ssh-keygen -R <IP>` |
| `aliyun-ecs/tf-outputs.json` | 过期载荷，`--from-output-json` 读到旧 IP | 删文件 |

`accept-new` 在这里拒绝是正确行为（同一个 IP 背后换了台机器），修法不是关校验。

### 第 3、4 步 · 本地部分

见阶段二。第 1 档只盘点，判据是四份报告打出来且端口/目录/挂载与 tfvars 期望一致。
第 3 档（接管）会覆写 compose 文件，被覆掉的挂载里有 `docker.sock` / `k3s` / `kubectl` / `.kube` 四项，
少任何一项的症状是「Jenkins 界面正常，但所有部署任务失败」。

### 第 5、6 步 · 应用交付与流水线入口

见阶段三/四，只改 API 层。三个「配错了不报错、只是没反应」的点：webhook 地址写主节点 IP（不能 localhost）、
端口必须是 8081、GitLab 要放行私有网段回调。

### 第 7 步 · Ansible

见阶段五。控制端必须 Linux/macOS/WSL；仓库在 `/mnt/c` 下时必须拷到原生文件系统并补 `chmod`。
判据：`verify.yml` 跑完所有被管机器 `changed=0`；`site.yml` 默认跑法里 E 幕被门闩跳过。

## 阶段一 · 阿里云部分

### 创建了什么

| 资源 | 说明 |
|---|---|
| VPC | 默认 `10.10.0.0/16` |
| vSwitch | 默认 `10.10.1.0/24`，可用区自动选择 |
| 安全组 | 开放 22/80/443、VPC 内 ICMP。容器端口不在里面 —— 只绑回环，走 80 反代 |
| ECS | 默认 1 台（可调 10 台），经济型规格，按量付费 |
| 密钥对 | 传了公钥才创建 |

实例通过 `user_data` 自动装 nginx 并写一行欢迎页，开机直接访问公网 IP 可验证。

### 一个入口：80 反代到容器栈

| | 系统 nginx | 容器里的 nginx |
|---|---|---|
| 装它的 | `user_data`（开机一次） | `roles/app_stack`（compose） |
| 听哪里 | `0.0.0.0:80` | `127.0.0.1:8080`，只绑回环 |
| 作用 | 公网唯一入口；上游没起来时给待部署页 | 真正的应用页面 |

理由：80 不能空着（`apply` 刚结束、Ansible 还没跑时，需要不依赖 SSH/Docker 的"机器活着"的证据）；
容器不直接对外（安全组里没有这个端口 —— 不需要被外面访问的端口就不该出现在安全组里）；
反代配置归 Ansible（写进 `user_data` 会被 `ignore_changes` 挡住，对存量机器不生效）。

落地在 `roles/app_stack`：渲染 `/etc/nginx/nginx.conf`（`nginx -t` 不过不落盘）→ reload →
本机 `curl 127.0.0.1` 断言 200。渲染后不再 `include` 发行版的 `sites-enabled`，避免两个 `default_server` 抢 80。

### 镜像就位：先拿物料，再谈编排

| 步 | 做什么 | 失败时 |
|---|---|---|
| 1 | `docker image inspect` 看本机有没有 | 进下一步 |
| 2a | 给了 `wf_stack_image_tar` → 上传 → `docker load` | 记下，不中断 |
| 2b | 没给 → `timeout <N> docker pull`（直连失败后换已声明镜像站前缀拉，再 tag 回原名） | 记 stderr |
| 3 | `up -d`，以「镜像已就位」为硬门槛 | — |

「镜像没拿到」不等于「80 一定 5xx」—— 旧容器还在跑。所以自检接受 `200/502/503/504` 再按内容分判：
200 = 旧容器在服务（不拦）；5xx + 待部署页 = 上游真死（通过）；5xx + nginx 默认错误页 = `error_page` 没生效（失败）。
只允许 5xx 的写法会让 uri 模块在第一种情况下自己报错，盖掉真正原因（踩坑 49）。

`registry-mirrors` 配了仍超时的经典原因：`daemon.json` 只在启动时读一次。
所以 `docker_engine` 写完 `flush_handlers` 重启，再断言 `docker info` 里真的出现这条地址。

### 发布与回滚：版本落在产物上

| 路径 | 是什么 |
|---|---|
| `releases/<版本>/index.html` | 本次发布的不可变产物 |
| `public/index.html` | 线上被 nginx 读的那份（从产物拷贝） |
| `current` | 当前生效版本名 |
| `history` | 发布过的版本，`lineinfile` 追加 |

**回滚不是再跑一遍发布**：那会用当前模板重新渲染，得到"旧版本号 + 新内容"。
回滚只做三件事：从 history 挑版本 → 拷产物回 `public/` → 从 80 读回版本标记。
`rollback.yml` 里没有任何 template 模块（自测钉死）。
版本名 CI 传 `-e wf_release=$CI_COMMIT_SHORT_SHA`；自检读页面里的 `<meta name="wf-release">`。

**容器挂的是目录不是单文件**：单文件 bind mount 把 inode 钉在容器里，换内容容器读不到且不报错；
目录挂载换文件立刻生效，不用重建容器（踩坑 51）。

### 操作步骤

```bash
cd aliyun-ecs
export ALICLOUD_ACCESS_KEY="..."      # PowerShell: $env:ALICLOUD_ACCESS_KEY="..."
export ALICLOUD_SECRET_KEY="..."
cp terraform.tfvars.example terraform.tfvars   # 改 region / ssh_public_key / ssh_allowed_cidr
terraform init && terraform validate && terraform plan && terraform apply
terraform output
terraform destroy    # 用完必做
```

### 几个值得留意的写法

- **`for_each` 而非 `count`**：count 删中间一台会导致后面全部重建。
- **镜像不写死 ID**：`image_id` 留空时按 `image_os`（默认 `ubuntu_24_04`）用 `data "alicloud_images"` 查最新。
  不选 ALinux3 是因为系统 Python 3.6，ansible-core 2.17+ 管不了它（踩坑 43）。
- **`ignore_changes = [user_data]`**：改启动脚本不重建机器；`image_id` 不忽略 —— 换镜像是换操作系统。
- **`PostPaid` + `deletion_protection = false`**：学习场景用完清干净。
- `user_data` 按包管理器（apt/dnf/yum）分支，不按发行版名判断。
- 变量 `validation` 限制实例数上限。

### 安全提醒

- `ssh_allowed_cidr` 默认 `0.0.0.0/0` 只适合练手，真实使用收敛成 `"你的公网IP/32"`。
- AccessKey 用 RAM 子账号，别用主账号密钥。
- `terraform.tfstate` 有明文信息，已在 `.gitignore`；多人协作必须换远程后端。

## 阶段二 · 本地部分：三档递进

### 先弄清一个点：Jenkins 是 docker-compose 部署的

磁盘上那份 `/data/jenkins-data/docker-compose.yaml` 就是它的声明式配置。
Terraform 无条件覆写它，最要紧的这些挂载会被抹掉：

```yaml
- /data/jenkins-data:/var/jenkins_home        # 数据目录
- /var/run/docker.sock:/var/run/docker.sock   # 在宿主机上构建镜像
- /usr/bin/docker:/usr/bin/docker
- /usr/local/bin/k3s:/host-bin/k3s:ro
- /usr/local/bin/kubectl:/host-bin/kubectl:ro
- /etc/rancher/k3s:/etc/rancher/k3s:ro
- /root/.kube:/root/.kube:ro
```

被覆盖后的事故现象很有迷惑性：容器正常、Jenkins 界面正常，但每个部署任务报 `kubectl: command not found`。
盘点/核对脚本会逐个确认其中 5 个容器内路径（`/var/run/docker.sock`、`/host-bin/k3s`、
`/host-bin/kubectl`、`/etc/rancher/k3s`、`/root/.kube`）。

| 档 | 配置 | 行为 |
|---|---|---|
| 1 | `manage_containers = false` | 只盘点（默认） |
| 2 | `manage_containers = true`，`jenkins_manage_compose = false` | 只核对，一行不改（推荐长期状态） |
| 3 | 两者都 `true` | 接管：备份后覆写 compose 并重启栈 |

### 第一步：只读盘点

```bash
cd vmware-workstation
cp terraform.tfvars.example terraform.tfvars   # 改 IP / 用户名 / 私钥路径
terraform init && terraform validate && terraform apply   # manage_containers 默认 false
```

`apply`（实为纯读取）打印四份盘点：节点基础信息；容器与 compose 栈（最关键，
含 Jenkins 真实端口/挂载/重启策略与那 5 个挂载检查）；K3s 集群状态；GitLab 服务健康。

### 第二步：核对（仍不改任何东西）

```hcl
manage_containers      = true
jenkins_manage_compose = false
jenkins_http_port      = 8081                 # 宿主端口，容器内 8080
jenkins_home_dir       = "/data/jenkins-data"
jenkins_compose_dir    = "/data/jenkins-data"
```

`scripts/jenkins-compose.py` 三层检查：运行中容器 vs 期望（镜像/端口/挂载/是否 compose 创建）；
compose 文件 vs 期望（**语义比对**：两边都过 `docker compose config` 再比，注释缩进不算漂移）；
核对模式只报告。配置没变时第二次 apply 不重跑，强制重看用 `terraform apply -replace=null_resource.jenkins_stack`。

### 第三步：接管（确认后再开）

`jenkins_manage_compose = true`：发现语义差异 → 备份 → 覆写 → `docker compose up -d`。
不用 `down -v`，不碰数据目录。收敛状态靠声明式比对，绝不靠删除。

幂等性来自「先比对、后动手」：没有差异就 exit 0，反复 apply 不动正在跑的服务。
注意 Jenkins 数据是 bind mount 到宿主机目录，没有「容器重建不丢数据」的保护，
`/data/jenkins-data` 要纳入备份（顺带把 compose 文件也备了）。

### 关于 Harbor：不是「暂未接管」，是「不能接管」

官方默认配置约 9 个容器（nginx/portal/core/registry/registryctl/db/redis/jobservice/log），
`manage_harbor` 恒为 false：官方没有单容器发行版；栈搞坏的代价是整条流水线的制品全没；
日常变更（项目、用户、保留策略）都在 API 层，而 API 层本配置是管的（`manage_cicd`）。
容器与配置归 `docker compose` + `harbor.yml`，项目与 webhook 策略归 Terraform。

## 阶段三 · CI/CD 流水线

链路落在主节点：

```
[从节点] GitLab ── Push Hook ──> Jenkins 构建 ── 推镜像 ──> Harbor
   ▲                              [主节点]                  │ PUSH_ARTIFACT
   │                                                        v
   │                                                  Harbor webhook
   └── git push ────────────────────────────────> Jenkins 部署任务
                                                            │
                                                            v
                                                      K3s 拉取新镜像
```

### 三个真实风险

**风险一：`PULL_ARTIFACT` 会造成部署死循环（最严重）。** 部署任务本身要拉镜像，
webhook 若监听拉取事件就形成 部署→拉镜像→触发→再部署 的无限循环。
`webhook_event_types` 有 validation：包含 `PULL_ARTIFACT` 直接报错；`cicd.tf` 另有 precondition；
运行时脚本会检测并纠正已有配置。只订阅 `PUSH_ARTIFACT`。

**风险二：推多个 tag 触发多次部署。** 同时推 `latest` 和 `v1.2.3` 会发两次 `PUSH_ARTIFACT`。
只推一个语义化版本 tag，或给部署任务加 `disableConcurrentBuilds()`。

**风险三：webhook 地址不能写 `localhost`。** Harbor 在容器里，`localhost` 是它自己。
必须写主节点 IP + **8081**（Jenkins 宿主端口，不是容器内 8080）：

```
✅ http://192.168.193.147:8081/generic-webhook-trigger/invoke?token=xxx
❌ http://localhost:8081/...        ← localhost 指向 Harbor 自己
❌ http://192.168.193.147:8080/...  ← 端口错
```

相反，Terraform **调 API 时**用 `localhost`（脚本在主节点上执行）。两个场景方向相反，容易搞混。
端口一致性有自动守卫：`apply` 输出的安全检查会提取 webhook 地址里的端口与 `jenkins_http_port` 比对。
「地址对但端口错」是最隐蔽的失效：回调打到没人监听的端口，Harbor/GitLab 都不当致命错误，
现象是「代码推了、镜像推了、流水线毫无反应」。

### 用法

```bash
cd vmware-workstation
terraform apply        # 默认 manage_cicd = false，先看现状
```

```hcl
harbor_admin_password  = "..."
jenkins_api_token      = "..."   # API Token，不是登录密码
webhook_endpoint_url   = "http://192.168.193.147:8081/generic-webhook-trigger/invoke?token=xxx"
webhook_event_types    = ["PUSH_ARTIFACT"]
manage_cicd            = true
```

```bash
# 密码建议走环境变量
export TF_VAR_harbor_admin_password="..."
export TF_VAR_jenkins_api_token="..."
terraform apply
```

接管后 Terraform 保证：Harbor 项目存在；webhook 只监听 `PUSH_ARTIFACT`；回调地址与认证头符合期望；
按名字幂等（已存在且一致则不动）。

**Jenkins 任务默认不接管**（`manage_jenkins_job = false`）：任务的流水线逻辑应写在应用仓库的
`Jenkinsfile` 里（Pipeline-as-Code），Terraform 只适合保证「任务存在且指向正确的仓库和分支」这个壳。

**destroy 默认不删 webhook**（`cicd_cleanup_on_destroy = false`）：webhook 正被流水线用着，
destroy 时删掉会直接打断业务。要删显式设 `true` 或去界面手工删。

## 阶段四 · GitLab 流水线入口

GitLab 是 Omnibus 实体服务，Terraform 不碰它本身：

| 层 | 谁管 |
|---|---|
| 服务启停、进程健康 | `systemctl` + `gitlab-ctl status/restart` |
| `/etc/gitlab/gitlab.rb` | 手工/Ansible + `gitlab-ctl reconfigure`（用 null_resource 硬改是反模式：没有 read，漂移看不见） |
| 项目 + webhook（API 层） | Terraform（`gitlab.tf`） |

Terraform 管两件事：GitLab 项目（存在即不动）和 Push webhook。服务健康由只读盘点覆盖
（自动识别安装方式，打印 `gitlab-ctl status`、版本、数据目录占用、备份目录现状）。

### GitLab 默认拒绝回调私有网段

GitLab 10.6 起为防 SSRF 默认禁止 webhook 请求 `127.0.0.1`、`10/8`、`172.16/12`、`192.168/16` 等 ——
你的整条链路都在 `192.168.x.x`。不放行的表现：`git push` 成功、Jenkins 毫无反应、
界面上也没有明显报错（只记在 webhook 详情页 Recent events），第一反应通常是去查 Jenkins，方向就偏了。

`gitlab-project.py` 做三层：打开全局开关 `allow_local_requests_from_web_hooks_and_services`；
把 Jenkins 的 `host:port` 加进出站白名单（双保险）；**重新读取设置做验证** —— 这个设置不生效，入口就是死的。

### 开关与 Token

```hcl
manage_gitlab_cicd = true
gitlab_api_token   = "..."    # api scope + 管理员权限（放行内网是实例级设置）
gitlab_webhook_endpoint_url = "http://192.168.193.147:8081/generic-webhook-trigger/invoke?token=..."
```

推荐第一步就开它 —— 入口的问题最隐蔽，先确认「push 能触发 Jenkins」。

Token 获取：界面 Access Tokens 勾 api；或从节点上：

```bash
sudo gitlab-rails runner "u=User.find_by_username('root'); \
  t=u.personal_access_tokens.new(scopes: ['api'], name: 'terraform-managed', \
  expires_at: 365.days.from_now); t.save!; puts t.token"
```

token 有字符集校验（`[A-Za-z0-9_-]`，20~64 位）：脚本要把它注入 Ruby 代码，限制字符集避开 sed 转义陷阱。

### 验证入口真的通了

`gitlab_webhook_test_on_apply = true` 会向 Jenkins 实发一次 push 事件并返回真实响应码 ——
代价是**真的触发一次完整流水线**，默认 false，验证完关掉。不想触发就看脚本输出的最近投递记录（只读）。

### 其他

- **external_url 对齐**：`gitlab_external_url` 必须与 `gitlab.rb` 里的 `external_url` 形式一致
  （含端口/协议），不一致时 API 调用会莫名失败。盘点输出会打印实际值。
- **内存**：GitLab 官方建议 4GB 起，从节点还跑着 K3s agent，是全链路最紧张的一台。
  降内存改 `gitlab.rb`（puma worker、sidekiq 并发、关 prometheus）后 `reconfigure`。
- **备份**：实体服务没有「重建不丢数据」，定期 `sudo gitlab-backup create`，确认 `/var/opt/gitlab/backups` 有文件。
- **排查速查**：push 后 Jenkins 没反应 → webhook Recent events；投递失败/被拒 → 放行设置；
  API 莫名失败 → external_url 不一致；服务无响应 → `gitlab-ctl status` / `tail`；502/反复重启 → 内存不够。

### CI 执行器：装在 147，用 shell executor

147 上已有 terraform、ansible-core、docker、云机器 SSH key，且与 GitLab(139) 同网段。

| 选择 | 没选的原因 |
|---|---|
| **shell executor（选它）** | 直接用宿主机工具链；代价是隔离性差，只适合单机自用 |
| docker executor | 每个 job 先配环境（provider 缓存、密钥都不在镜像里），与「用 CI 收敛变更」相反 |
| GitHub Actions | 147 到 github.com 不通 |

```bash
sudo GITLAB_URL=http://192.168.193.139:8000 \
     RUNNER_TOKEN=glrt-xxxxxxxx \
     ./tools/install_runner.sh    # 幂等，重复执行只做 verify
```

`concurrent = 1` 是正确性考虑：ansible 侧没有锁，两个 job 同时改同一台机器，谁最后写谁赢。

**PATH 要探测后写进 config.toml**：shell executor 不继承登录 shell 的 PATH，147 上 ansible 在
`~/.venvs/ansible/bin` 不在标准 PATH，漏了的症状是第一行 `command not found`。
脚本用 `shutil.which` 探测真实位置写回去；`.gitlab-ci.yml` 的 `before_script` 另有工具链自检。

**`tags` 必须写**（顶层 `default` 兜底）：手工登记的 runner 大概率 `--run-untagged=false`，
没 tag 的 job 不会失败也不会超时，只是灰圈一直转、状态 stuck（踩坑 54 相关，B7 第 7 条子规则）。

CI 变量：`ALICLOUD_ACCESS_KEY`、`ALICLOUD_SECRET_KEY`（Masked）、`WF_SSH_KEY_FILE`（File）、`WF_SSH_USER`。

## 阶段五 · Ansible：批量管理云服务器与本地服务器

| | 负责 | 不负责 |
|---|---|---|
| `ansible/` | 机器基线（包、目录、时区、内核参数、limits）；云机器 Docker 与容器栈；全机器只读体检 | 云资源的创建销毁；Jenkins/Harbor/GitLab 服务对象；本地机器文件（默认） |

同一份声明式配置不能让两个工具同时声明。

### 云机器随便改，本地机器默认一个字节都不改

| 组 | 来源 | 性质 |
|---|---|---|
| `cloud` | `tools/gen_cloud_inventory.py` 生成（本地手工跑自己调 `terraform output`；流水线走 `--from-output-json` 读 artifact） | 随时重建，允许任意变更 |
| `local` | `inventory/hosts.yml` 手写 | 手工维护、跑着别人在用的服务，默认完全只读 |

三层防护，失效模式各不相同：

1. **结构性隔离**：会改东西的 role 只出现在 `hosts: cloud` 的幕里 —— 本地机器压根没轮到它。
2. **运行前断言**：`guard` 是所有幕的第一个 role，变更型幕里硬断言，防的是第一层被绕过
   （本地机器挪进 cloud 组、复制粘贴幕忘改 hosts、`hosts: cloud` 改成 `all`）。
3. **逐任务许可**：「新增」不带条件（补缺安全），「覆盖已有文件/设置」一律 `when: wf_can_write | bool`。

设计选择「断言 + 报错」而不是静默跳过：静默跳过的结果是"看起来成功但什么都没做"。

### 两个解锁开关

```bash
ansible-playbook playbooks/verify.yml                                              # ① 默认只读
ansible-playbook playbooks/site.yml -e local_mutation_allowed=true --check --diff  # ② 干跑
ansible-playbook playbooks/site.yml -e local_mutation_allowed=true                 # ③ 真改（只补缺）
ansible-playbook playbooks/site.yml -e local_mutation_allowed=true \
                   -e local_overwrite_allowed=true                                 # ④ 连覆盖也放开（先备份）
```

「能覆盖」必须蕴含「能补缺」：`wf_can_write` 同时要求两个开关。如果只认覆盖开关，
会出现「没解锁变更却拿到覆盖权限」的反直觉状态 —— 保护公式自己就该是单调的，
不依赖调用方记得先加门闩。selftest 用 16 种开关组合穷举钉住这条不变量。

### 五幕，风险由低到高

| 幕 | 目标 | 意图 | 做什么 |
|---|---|---|---|
| A | `all` | read | 只读体检，报告写在控制端 `reports/` |
| B | `local` | read | 本地基线检测（只看不改） |
| C | `cloud` | mutate | 云机器基线 + Docker 引擎 |
| D | `cloud` | mutate | 容器栈（只绑回环）+ 80 反代 |
| E | `local` | mutate | 本地变更 —— 默认整幕不执行，`pre_tasks` 第一个任务就是 `meta: end_play` |

E 幕照常声明 `intent: mutate`（guard 一定会检查它），用显式前置判断决定跑不跑 ——
保护逻辑必须一眼能看懂。`verify.yml` 里所有采集任务 `changed_when: false`，
所以「跑完 changed 全 0」是干净的验收信号。

### 路径禁区：按路径段，不按字符串前缀

`group_vars/local.yml` 列了绝对不能碰的路径（`/data/jenkins-data`、`/var/opt/gitlab`、
`/etc/gitlab`、`/etc/rancher/k3s`、`/var/lib/rancher/k3s`、`/root/.kube`）。
`baseline` 动手前断言写入目标不落在禁区下。判定用 `wf_under_any`：
先 `posixpath.normpath` 归一化，再按段比较 —— `/data/jenkins-data-OLD` 是字符串前缀但不在禁区之下，
`-OLD` / `.bak` 这类命名到处都是，用 `startswith` 会误伤。

### 云机器的清单从哪来

```bash
cd ansible
python3 tools/gen_cloud_inventory.py                   # 读 ../aliyun-ecs 的 terraform output
python3 tools/gen_cloud_inventory.py --allow-private   # 无公网 IP 时也生成（默认跳过）
```

生成的 `hosts.cloud.yml` 是产物不入库。生成器对每个值用 `json.dumps` 渲染：
JSON 是 YAML 1.2 的子集，中文/引号/冒号都不需要额外转义。
同时会清掉这批 IP 在 known_hosts 里的旧键（见第 2 步回收清单）。

### 怎么验证

离线（不连机器）：

```bash
cd ansible
python3 tools/check_guard.py            # 8 类不变量
python3 tools/check_guard.py --selftest # 19 个用例
python3 tools/selftest.py               # 178 项断言
```

`check_guard.py` 检查：清单声明保护且 cloud/local 不重叠；每幕声明 `intent`；`guard` 是第一个 role；
变更型幕的 hosts 不可能包含本地机器；解锁型幕必须有 `meta: end_play` 门闩且不引用云专用 role；
本地可达的 role 里写任务必须带 `wf_can_write`；禁区内不得出现 baseline 写入目标；
`group_vars` 必须在 Ansible 真正会加载的位置（踩坑 24）。也跟进 `import_role` 的 `tasks_from`（踩坑 53）。

真机：

```bash
python3 -m venv ~/.venvs/ansible && ~/.venvs/ansible/bin/pip install -r ansible/requirements.txt
~/.venvs/ansible/bin/ansible-playbook playbooks/verify.yml        # changed 必须全 0
~/.venvs/ansible/bin/ansible-playbook playbooks/site.yml          # E 幕被门闩跳过
~/.venvs/ansible/bin/ansible-playbook playbooks/site.yml -e local_mutation_allowed=true --check --diff
~/.venvs/ansible/bin/ansible-playbook playbooks/site.yml \
  -e '{"wf_docker_registry_mirrors": ["https://<可用的加速器>"]}'
```

版本用 `requirements.txt` 的约束，别直接 `pip install ansible`（ALinux3 的 Python 3.6，见踩坑 43）。

## 环境要求

- Terraform >= 1.5.0；阿里云账号（AccessKey 有 VPC/ECS/安全组权限）
- 本地两台已装 K3s 的虚机，SSH 可达；主节点有 Docker Compose v2 + python3 + Jenkins/Harbor 栈；
  从节点有 python3 + GitLab（Omnibus）+ K3s agent
- Windows 本机需要 **Git Bash**（`local-exec` 调 `bash`）；本机不需要 Python
- Ansible >= 2.14，控制端必须 Linux/macOS/WSL —— Windows 原生 Python 没有 `os.fork`，
  这是平台限制，调配置解决不了。被管机器只需要 `python3`（3.8+）

**WSL 的坑：`/mnt/c` 是世界可写目录**，Ansible 拒绝加载里面的 `ansible.cfg`（只给 WARNING，
配置静默失效）。两种解法：

```bash
# 解法一（推荐）：拷到 WSL 原生 ext4，拷完必须补 chmod ——
# rsync -a 保留权限位，源头的 777 会跟过去，少了这句报错一模一样（踩坑 40）
rsync -a /mnt/c/path/to/terraform-demo/ ~/terraform-demo/
chmod -R go-w ~/terraform-demo

# 解法二：显式指定
export ANSIBLE_CONFIG=/mnt/c/path/to/terraform-demo/ansible/ansible.cfg
```

验收只看一行：`ansible --version | grep "config file"`，`None` 就是被拒载了。

**Windows 上必须用 Git Bash**：`vmware-workstation/` 的 provisioner 都是 `local-exec` + bash，
PATH 里的 `bash` 若指向 WSL 的 `bash.exe`（System32/WindowsApps），会在 Windows/Linux 路径间来回转换。
判断：`which bash` 应是 `<Git安装目录>\bin\bash.exe`。
远端脚本经 `ssh 'python3 -u -'` 从 stdin 执行（`-u` 不能省：不缓冲输出，否则看起来像卡住）；
脚本只用标准库，远端不需要 pip。

**国内网络**：`terraform.rc` 让 `aliyun/alicloud` 走阿里云镜像，其余走官方源：

```powershell
Copy-Item terraform.rc.example terraform.rc
$env:TF_CLI_CONFIG_FILE = (Resolve-Path .\terraform.rc).Path
```

## 验证状态

### Terraform 侧

| 目录 | fmt | validate |
|---|---|---|
| `aliyun-ecs/` | 通过 | 通过（阿里云镜像下载 provider） |
| `vmware-workstation/` | 通过 | 通过（5 个 Python 脚本 + compose 模板过 `py_compile`/渲染检查） |
| `vmware-vsphere-reference/` | 通过 | 未跑（需 vCenter，仅语法参考） |

行为验证（不只是语法）：只读模式 plan 为 `7 to add, 0 to change, 0 to destroy`；
三道安全防护（空 token、localhost webhook、缺协议）都在 plan 阶段拦下；
端口一致性护栏正反两个方向都验过；核对脚本三分支用 mock docker 验证；4 个 API 脚本的纯函数逻辑
mock HTTP 验证；渲染产物剥掉 `<<EOT` 后能被系统 Python 直接执行，`expected_compose` 与手写文件逐字节一致。

### Ansible 侧

**离线**：`check_guard.py --selftest` 19 个用例（含 import_role 旁路、`hosts: all` 经典错误、
无法静态解析的 hosts 保守处理）；`selftest.py` 178 项断言 —— 保护公式真值表是从
`roles/guard/tasks/main.yml` **读出公式本身**求值的（不是重抄副本），穷举 16 种开关组合；
反向验证（改坏 hosts、删门闩、公式改松）都被抓到。

**真机**（WSL2 Ubuntu + ansible-core 2.21，本机同时扮演 cloud/local 两组，共 15 轮）：

| 验证项 | 结果 |
|---|---|
| 只读验收 `verify.yml` | changed=0；baseline 检出差异但拒绝动手 |
| 默认路径 | A/B 幕只读；E 幕门闩生效零任务 |
| 解锁干跑 / 真跑 | 只有补缺动手；时区/sysctl/limits/防火墙纹丝不动 |
| 幂等 | 重跑 changed=0 |
| 云侧真跑 | Docker + daemon.json（含 mirrors）+ 容器 healthy + 80 反代 200 |
| 云侧幂等 | 第二次 changed=0（第一轮抓到首页嵌时间的真 bug，踩坑 28） |
| 双组冲突 | guard 当场 fatal |
| 静态工具 Linux 复跑 | 与 Windows 侧逐项一致 |
| 只补缺的决定性验证 | 注入缺失目录 → changed=1，覆盖类 md5 前后一致 |
| 真机拉镜像走加速器 | daemon.json 落盘 → 重启 → `docker info` 复核 → 容器 healthy |
| 真机两条失败分支 | 容器停 → 502+待部署页通过后统一 fail；旧容器在 → 200 判「旧容器在服务」 |

**明确没有验证的**：真实的两台 192.168.1.x 存量机器；`docker compose` 版本差异（只验过 2.40.3）；
发布/回滚链路只离线验了形状与表达式真值，没在真机跑过「发 v1 → 发 v2 → 回滚 v1」完整过程；
「离线镜像包」出路只验了任务形状；`docker info` 的 Registry Mirrors 超 3 条时会少看几行（`grep -A3`）。

### 边界检查器（`tools/check_boundaries.py`）

69 个自证用例全过（42 个应命中 + 23 个应放行 + 破坏性场景）。放行类里最关键的：
注释里的 `ansible-playbook` / `kubectl`、`echo` 提示文本里的 `apt-get install`、
`user_data` 里的 `dnf install` —— **误报率决定这类工具能不能活下去**。

对真实仓库的破坏性验证（每次只改一处，跑完还原并逐字节核对）：

| 场景 | 结果 |
|---|---|
| 拿掉 `when: manual` / 去掉分支限定 / 删回滚 job / 去掉 `--limit` / 带上解锁开关 / TF+Ansible 同 job / 加 deploy.sh / 去掉 tags 兜底 | 各自命中 B7，改一处只响一处 |
| 已声明文件里插 `terraform apply` | B2 报（不是 B8）—— 规则管辖不互相顶替 |
| 未登记的文件读 terraform output | B8 报漏登记，指向行号 |
| 声明 `mode: write` / `to: ansible` / 缺 why | 各自报 |
| 声明 `vsphere_virtual_machine` / playbook 里 `vmrun` / tools 里 subprocess vmrun | 各自报 B9 |
| 从豁免清单删掉 `vmware-vsphere-reference` | B9 报 8 个问题 —— 豁免真的在起作用 |
| `archive-vm-clone/` 改名脱离豁免 | 立刻报 29 个问题 —— 判据是清单不是目录名 |

校验器自身缺陷：第八轮自证抓出 4 个（自我误报、argv 写法漏检、上下文过滤一刀切、提示文本未放行，踩坑 30）；
第九轮在设计阶段砍掉一个永远不会响的检查（成环检测，踩坑 34）。

**明确没有验证的**：边界检查是静态的，不证明运行时没抢对象；
`.gitlab-ci.yml` 在真实 GitLab 上的行为（runner tag、CI 变量、artifact）后来已在 147 真跑验证（见踩坑 54-57 轮次）。

离线验证不依赖真实机器的原因：每个 Python 脚本都支持 `WF_CONFIG_JSON` 环境变量直接喂配置，
「远端没有 docker」「compose 文件不存在」这些分支都能用 mock 跑出来 —— shell 版本做不到这一点。

## 踩坑清单

57 条在 **[PITFALLS.md](PITFALLS.md)**，按轮次分组、带索引表。
正文里「见踩坑 27」的编号就是那份清单的编号。

| 轮次 | 主题 | 条目 |
|---|---|---|
| 一 | 阿里云部分 | 1–3 |
| 二 | 本地容器部分 | 4–5 |
| 三 | CI/CD 部分 | 6–7 |
| 四 | GitLab 部分 | 8–10 |
| 五 | 拿到真实 compose 文件之后 | 11–13 |
| 六 | shell 改写成 Python | 14–16 |
| 七 | 加入 Ansible 批量管理层 | 17–28 |
| 八 | 守住分层边界 | 29–31 |
| 九 | CI 唯一触发器 + 跨层衔接声明化 | 32–36 |
| 十 | 宿主机边界 + 豁免登记 | 37–41 |
| 十一 | 真机联调 | 42–45 |
| 十二 | 入口收敛：80 反代 | 46 |
| 十三 | 镜像就位 | 47 |
| 十四 | 真机验失败路径 | 48–49 |
| 十五 | 发布与回滚 | 50–51 |
| 十六 | 流水线驱动发布/回滚 | 52–53 |
| 十七 | 147 真跑 bootstrap | 54–55 |
| 十八 | 桶名被占 | 56 |
| 十九 | 镜像仓库：可达 ≠ 拉得到 | 57 |

★ 标记的是「不报错但后果严重」和「看似在保护其实没保护」的那几类，建议优先读。

## 成本与风险提示

**云端**：ECS 按量付费但实例存在就计费（含公网带宽），用完 `terraform destroy`；建议独立测试账号。

**本地**：默认只读（三个 manage 开关全 false），首次 apply 不改任何东西。

- Jenkins 是 bind mount 不是命名卷，`/data/jenkins-data` 要自己备份（关键的是 `jobs/`、
  `credentials.xml`、`config.xml`、`users/`，几百 MB 级别）。
- `jenkins_manage_compose = true` 会覆盖 compose 文件（先备份）；`jenkins_stack_down_on_destroy = false`
  时 destroy 不停 Jenkins，任何情况下不会 `down -v`。
- Jenkins 容器 `privileged: true` + root + `docker.sock` ≈ 宿主机 root 权限，不要暴露公网。
- 内存限制 2G 偏紧，频繁莫名重启就调 4G 或设 JVM 堆上限。
- 8081 是宿主端口（容器内 8080），所有外部访问都用 8081；写 8080 的后果是回调静默失败。
- GitLab：定期 `gitlab-backup create`；默认拒绝内网 webhook（`Outbound requests` 放行）；
  `gitlab_webhook_test_on_apply` 会真触发一次流水线，验证完关掉。
- Harbor：`manage_harbor` 恒为 false（无法接管）；webhook 只订阅 `PUSH_ARTIFACT`，
  订阅 `PULL_ARTIFACT` 会死循环。
- 渲染后的脚本含 Harbor 密码 / Jenkins Token / GitLab Token，local-exec 失败日志可能带出这些值，
  别贴到公开场合。`terraform.rc` 与 `*.tfvars` 已被 `.gitignore` 排除。
