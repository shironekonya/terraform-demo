# Terraform Demo：阿里云 ECS + 本地 K3s 环境

一份用于**学习**的 Terraform 示例。公有云部分从零创建资源；本地部分因为**环境和集群已经存在**，走的是完全不同的思路——不创建虚机，而是**盘点现状 + 以代码声明容器化服务**。最后再加一层 Ansible，解决「同一件事要在 N 台机器上做」的问题——其中大部分机器是不能碰的存量环境。

```
terraform-demo/
├── aliyun-ecs/                  # ① 阿里云：VPC / 交换机 / 安全组 / ECS
│   ├── versions.tf              #    provider 与版本约束
│   ├── variables.tf             #    输入变量（含校验规则）
│   ├── network.tf               #    网络层：VPC、vSwitch、安全组规则
│   ├── ecs.tf                   #    计算层：ECS 实例 + 密钥对
│   ├── outputs.tf               #    输出：IP、实例 ID、SSH 命令
│   ├── backend.tf.example       #    ★ 远程 state + TableStore 锁（默认不生效，见「分层边界」）
│   └── terraform.tfvars.example
│
├── vmware-workstation/          # ② 本地：管理已存在机器上的容器栈与流水线
│   ├── versions.tf              #    只用 null provider
│   ├── variables.tf             #    节点连接信息 + Jenkins compose 配置 + CI/CD 配置 + GitLab 配置
│   ├── inventory.tf             #    ★ 只读盘点：主/从节点、容器与 compose 栈、K3s、GitLab 服务状态
│   ├── containers.tf            #    ★ 主节点：Jenkins compose 栈的核对/接管 + Harbor 边界说明
│   ├── cicd.tf                  #    ★ 主节点：声明 Harbor 项目/webhook、Jenkins 任务（幂等）
│   ├── gitlab.tf                #    ★ 从节点：声明 GitLab 项目、Push webhook（幂等）
│   ├── templates/
│   │   └── jenkins-compose.yaml.tpl   # Jenkins compose 模板（接管模式下按它渲染）
│   ├── scripts/
│   │   ├── inventory.py         #    只读盘点脚本（按 section 分派：base/containers/k3s/gitlab）
│   │   ├── jenkins-compose.py   #    Jenkins compose 栈的核对/接管脚本（verify / reconcile 双模式）
│   │   ├── harbor-webhook.py    #    Harbor 项目与 webhook 同步脚本
│   │   ├── jenkins-job.py       #    Jenkins 任务同步脚本
│   │   └── gitlab-project.py    #    GitLab 项目与 Push webhook 同步脚本
│   ├── outputs.tf               #    拓扑、管理边界、流水线安全检查（含端口一致性核对）
│   ├── backend.tf.example       #    ★ 远程 state + TableStore 锁（默认不生效）
│   ├── terraform.tfvars.example
│   └── archive-vm-clone/        #    停用的旧方案（vmrun 克隆宿主机虚机；★ 已登记豁免）
│
├── vmware-vsphere-reference/    # ③ 参考：vSphere 官方 provider 写法（★ 已登记豁免）
│   └── main.tf
│
├── ansible/                     # ④ 批量管理：把①的云机器和②的存量机器统一管起来
│   ├── ansible.cfg              #    控制端必须是 Linux / macOS / WSL（Ansible 需要 os.fork）
│   ├── examples/
│   │   └── hosts.cloud.yml.example  # 云服务器清单长什么样（★ 不能放进 inventory/，见踩坑 27）
│   ├── inventory/
│   │   ├── hosts.yml            #    手写：本地服务器（受保护，默认完全只读）
│   │   ├── hosts.cloud.yml      #    产物：由 tools/ 生成，不入库
│   │   └── group_vars/          #    ★ 必须在清单旁（playbook 旁也可以），别处会被静默忽略
│   │       ├── all.yml          #    通用：报告目录、时区、体检项
│   │       ├── cloud.yml        #    云服务器：root 直登、不受保护
│   │       └── local.yml        #    ★ 本地服务器：受保护声明 + 禁区路径
│   ├── roles/
│   │   ├── guard/               #    ★ 保护机制本体：判定 + 断言 + 运行前打印状态
│   │   │   ├── tasks/main.yml
│   │   │   └── filter_plugins/wf_guard.py   # 路径段判定（不是字符串前缀）
│   │   ├── audit/               #    只读体检 → 报告写在控制端
│   │   │   ├── tasks/main.yml
│   │   │   └── templates/report.txt.j2
│   │   ├── baseline/            #    机器基线；本地机器只跑「检测」，不跑「应用」
│   │   ├── docker_engine/       #    仅云服务器：装 docker + 写 daemon.json
│   │   └── app_stack/           #    仅云服务器：拉起容器栈
│   ├── playbooks/
│   │   ├── site.yml             #    ★ 唯一入口：五幕，风险由低到高
│   │   └── verify.yml           #    纯只读入口（等价于 site.yml 的前两幕）
│   └── tools/
│       ├── gen_cloud_inventory.py   # 从 terraform output 生成云清单
│       ├── check_guard.py           # ★ 静态护栏校验：证明「变更任务碰不到本地机器」
│       └── selftest.py              # 离线自测：模板渲染 + 保护公式真值表
│
├── tools/                       # ⑤ 跨模块：分层边界的静态校验
│   └── check_boundaries.py      #    ★ 9 条边界（谁管什么），越界就报出来
│
├── .gitlab-ci.yml               # ★ 变更的唯一触发器（唯一能改动环境的路径）
└── boundaries.yml               # ★ 声明清单：跨层衔接（默认零条）+ 检查豁免（登记在册）
```

前三个目录**各自独立管理 state**，互不依赖，可以分别 `apply` / `destroy`。
（**注意**：`state` 默认在本地，多人协作前必须换成远程后端 —— 见 [分层边界](#分层边界谁管什么不越界)。）

`ansible/` 是另一回事：它是一层**没有 state 的编排层**，复用 ① 输出的云机器 IP、以及 ② 一直在管的那批存量机器。它**刻意不与 Terraform 争抢同一个对象** —— 详见 [阶段五](#阶段五--ansible批量管理云服务器与本地服务器)。

`tools/` 不属于任何一层：它是「边界的裁判」，检查上面四层有没有互相越界。

`.gitlab-ci.yml` 和 `boundaries.yml` 也不是某一层的东西，它们是**边界本身的载体**：
前者规定「只有流水线能动手」，后者既登记「层与层之间那几次数据传递是哪几次、为什么」，
也登记「哪些路径不参与运行、所以不参与检查」。详见 [分层边界](#分层边界谁管什么不越界)。

---

## 目录

**先读这三节（理解边界，后面才看得懂）**

- [本地环境拓扑（已存在，不由 Terraform 创建）](#本地环境拓扑已存在不由-terraform-创建)
- [核心概念：Terraform 管不了已存在的虚机](#核心概念terraform-管不了已存在的虚机)
- [分层边界：谁管什么，不越界](#分层边界谁管什么不越界) — ★ 九条边界 + CI 唯一触发器 + 跨层衔接声明 + 豁免登记

**本地部分（阶段二）**

- [阶段二 · 本地部分：三档递进](#阶段二--本地部分三档递进) — 只读盘点 → 核对 compose → 选择性接管
- [幂等性与数据安全](#幂等性与数据安全)
- [关于 Harbor：不是「暂未接管」，是「不能接管」](#关于-harbor不是暂未接管是不能接管)

**流水线（有两条，改的东西完全不同 —— 别搞混）**

- [阶段三 · CI/CD 流水线](#阶段三--cicd-流水线) — **应用交付**：Harbor webhook → Jenkins 部署
- [阶段四 · GitLab 流水线入口](#阶段四--gitlab-流水线入口) — 代码提交触发构建
- [CI/CD 是唯一触发器](#cicd-是唯一触发器把变更收敛成一条路径) — **环境变更**：`.gitlab-ci.yml`，跑的是 Terraform 和 Ansible 自己

**云端与参考**

- [阶段一 · 阿里云部分](#阶段一--阿里云部分)
- [vSphere 参考版本（`vmware-vsphere-reference/`）](#vsphere-参考版本vmware-vsphere-reference) — 对照材料，已登记豁免、不参与运行
- [两个环境怎么配合使用](#两个环境怎么配合使用)

**批量管理（阶段五）**

- [阶段五 · Ansible：批量管理云服务器与本地服务器](#阶段五--ansible批量管理云服务器与本地服务器) — 云机器随便改，本地机器默认一个字节都不改

**工程细节（踩坑记录在这里）**

- [本地部分的代码设计考虑](#本地部分的代码设计考虑)
- [环境要求](#环境要求)
- [验证状态](#验证状态)
- [成本与风险提示](#成本与风险提示)

---

## 本地环境拓扑（已存在，不由 Terraform 创建）

```
┌─────────────────────────────────────────────────┐
│  K3s 主节点（server）  192.168.1.101              │
│  • K3s 控制平面                                  │
│  • Jenkins  ← docker-compose 栈（宿主 8081）      │
│  • Harbor   ← docker-compose 栈（默认配置 ~9 容器，宿主 80/443）│
└─────────────────────────────────────────────────┘
                    │
┌─────────────────────────────────────────────────┐
│  K3s 从节点（agent）   192.168.1.102              │
│  • K3s 工作负载                                  │
│  • GitLab   ← Omnibus 实体服务（系统包安装，非容器）│
└─────────────────────────────────────────────────┘
```

**关键点**：主节点上的 Jenkins 与 Harbor 都是 **docker-compose 部署的容器栈**，独立于 K3s 之外；从节点上的 GitLab 是**直接装在系统里的实体服务**（Omnibus 包）。三个服务都不属于 K8s 工作负载。

这个差异直接决定了 Terraform 的边界：

| 服务 | 部署方式 | Terraform 管到哪一层 |
|---|---|---|
| Jenkins | docker-compose（单服务） | **只核对 compose 栈**；默认不覆写 compose 文件（`containers.tf`） |
| Harbor | docker-compose（官方默认配置，~9 容器） | **容器层完全不碰**；只接管其上的项目与 webhook 策略（`cicd.tf`） |
| GitLab | Omnibus 实体服务 | **服务本身完全不碰**；只接管 API 层：项目 + webhook（`gitlab.tf`） |

一句话概括这个原则：**凡是「已经有一份自己的声明式配置」的东西（compose 文件、gitlab.rb），Terraform 都不去当第二个源头。**

**注意两台机器角色不同**，这决定了两件事：

| | 主节点 | 从节点 |
|---|---|---|
| 流水线中的位置 | 构建与制品侧 | **流水线入口** |
| 跑着什么 | Jenkins、Harbor（compose 栈） | GitLab（实体服务） |
| Terraform 声明文件的脚本在哪执行 | `ssh 主节点` | `ssh 从节点` |
| 回调对方时地址写什么 | Harbor 回调 Jenkins 用**主节点 IP + 8081** | GitLab 回调 Jenkins 用**主节点 IP + 8081** |


---

## 核心概念：Terraform 管不了已存在的虚机

这是本项目最重要的一节，也是最容易产生错误预期的地方。

**问题**：你的两台虚机已经存在，能不能用 Terraform「接管」进 state？

**答案：在 VMware Workstation 场景下不能。** 原因很具体：

1. VMware Workstation **没有官方 Terraform provider**（HashiCorp 只维护 `vsphere`，面向 vCenter/ESXi）
2. 用 `null_resource` 也**无法 import** —— import 的前提是存在对应的 resource 类型且有 `read` 实现。`null_resource` 只是「这段脚本跑过没有」的标记，它不持有虚机对象，所以**没有任何东西能被接管进 state**

**所以正确的分层是：**

| 层次 | 归谁管 | 原因 |
|---|---|---|
| 虚拟机本身 | VMware Workstation（手工 / vmrun） | 无 provider，无法 import |
| 操作系统配置 | 配置管理工具（Ansible 等）更合适 | 不是 Terraform 的强项 |
| **容器进程** | **docker compose** | compose 文件本身就是声明式配置，Terraform 不去当第二个源头 |
| **容器之上的配置**（Harbor 项目/webhook、Jenkins 任务、GitLab 项目/webhook） | **Terraform（本目录）** | 走 HTTP API，与安装方式无关，最适合声明式管理 |

这个边界认知比代码本身更有价值：**Terraform 只能管它「创造得出来」、
或有 provider 支持 read、或只有它一个声明式源头的东西。**

---

## 分层边界：谁管什么，不越界

上一节讲的是「Terraform 管不了什么」。这一节讲**剩下四个工具之间怎么分工**，
以及**这些边界怎么才能不只是文档里的一句话**。

### 九条边界

| # | 边界 | 一句话理由 | 校验器规则 |
|---|---|---|---|
| B1 | **Terraform 管「资源是否存在、拓扑是否正确」** | 它擅长的是「这个东西该不该存在、长什么样」，不擅长「机器里现在是什么状态」 | 由 B4（state）+ 人工审计覆盖 |
| B2 | **Ansible 管「机器 / 服务内部状态是否正确」** | 幂等 + 可重复执行，天生适合「把这台机器调到目标状态」 | B6 |
| B3 | **K8s 工作负载用 K8s 工具，不进 Terraform** | 发布节奏（什么时候发这一版）和基础设施节奏（有没有这个集群）是两件事 | B3 |
| B4 | **不拿 Ansible 当 Terraform 的触发器** | 否则「谁对状态负责」说不清：apply 报成功、后面半截失败，state 里记的却是成功 | B1 |
| B5 | **state 放远程后端 + 开状态锁** | 本地 state + 无锁 = 并发 apply 互相覆盖，产生永远 destroy 不掉的幽灵资源 | B4 |
| B6 | **Python / Shell 只做胶水，不替代状态管理** | 生成 tfvars、解析 output、调 API、做健康检查都合适；碰 state 文件不行 | B2 / B5 / B6 |
| B7 | **CI/CD 是变更的唯一触发器** | 否则「以谁的本地环境为准」永远没有答案 —— 每个人手跑的那次都算数 | B7 |
| B8 | **跨层衔接必须显式声明，且只能只读单向** | 「绝对禁止」和「随便来」都不对；正确形态是默认零条 + 每条都写出来给人 review | B8 |
| B9 | **宿主机虚拟化平台不进仓库的运行路径** | demo 的组件全在虚机**内部**跑；把宿主机的虚拟化平台写进 IaC，会让控制端被钉死在一台机器上 —— 不可复现、上不了 CI | B9 |

> **最后那一列是对照，不是重复。** 这张表说的是**职责**（谁管什么），校验器说的是**检查项**
> （怎么发现越界）。两者不是一套编号：一个职责可能要两条检查才拦得住（B6 拆成了 B2/B5/B6），
> 一条检查也可能覆盖两个职责。看校验器输出时，请对着最后一列看。

**为什么是这九条**：它们全都不是「规范洁癖」，而是**失败一次就很难查**的那类问题 ——
两个工具都声称自己管某个对象时，现场状态没有权威来源；state 被覆盖时，
资源真实存在但 Terraform 不知道，出错信息指向的永远是别的地方；
而「谁都能随手改一下环境」的时候，前六条的失效过程根本没人看得见。

### 这个仓库现在守得怎么样

边界写进文档很容易达成共识，**难的是在代码里不被慢慢侵蚀**。所以每条边界都配了
一个可执行的静态检查（`tools/check_boundaries.py`），纯标准库、不连机器、不装依赖：

```bash
python3 tools/check_boundaries.py                      # 9 条边界逐条报状态
python3 tools/check_boundaries.py --selftest           # 证明这个检查器真的会响（65 个用例）
python3 tools/check_boundaries.py --require-remote-state   # CI 用：没有远程后端直接失败
python3 tools/check_boundaries.py --require-ci             # CI 用：没有流水线定义直接失败
```

它检查的东西（每条都对应一种真实的越界方式）：

| 规则 | 拦什么 | 怎么判定 |
|---|---|---|
| B1 | `*.tf` 或胶水脚本里调 `ansible-playbook` | 只看会被执行的命令，注释与提示文本不算 |
| B2 | `ansible/` 里跑 `terraform apply / destroy / import / state rm …` | 只允许只读的 `output / show / plan / validate / fmt / version` |
| B3 | `resource "kubernetes_deployment"` / `helm_release` / `kubectl_manifest` | 用闭集匹配，不搞「`kubernetes_` 开头就算」 |
| B4 | `backend "local"`、oss 后端缺 `tablestore_table`、backend 里硬编码凭证 | 没有 backend 块给提示（`--require-remote-state` 时升级为失败） |
| B5 | 脚本里 `open('terraform.tfstate')`、provisioner 里碰 state 文件 | — |
| B6 | provisioner / 胶水脚本里 `apt-get install`、`systemctl restart` | `user_data` 豁免（那是实例启动配置，只在创建时跑一次） |
| B7 | 没有流水线定义；TF 与 Ansible 被塞进同一个 job；变更动作没有人工放行；`site.yml` 不带 `--limit`；碰 local 的 job 没有人工放行；解锁开关被悄悄带上；有一键绕过流水线的入口脚本 | 对流水线做**文本级**结构检查（不是 GitLab CI 语义解析器），约定 `ansible-playbook` 的调用写在一行里 |
| B8 | 代码里有跨层调用但没声明；声明写成 `mode: write`；声明的 `to` 不是 terraform；没写 `why`；声明了但代码里找不到（腐烂）；声明文件用了子集之外的写法 | 严格的最小 YAML 子集解析，遇到不认识的写法**报错而不是猜** |
| B9 | `resource "vsphere_virtual_machine"`、`provider "vsphere"`、`source = "hashicorp/vsphere"`；`vmrun` / `vmware-cmd` / `vmware-vdiskmanager` 调用；`.vmx` / `.vmdk` 路径；豁免清单里没写 `why`、或登记了不存在的路径 | 命令类只在**执行上下文**里找（`error_message = "找不到 vmrun.exe"` 是提示文本，不算）；豁免判据是**清单**，不是目录名 |

**判定原则比规则本身更重要：只看会被执行的命令。** 这个仓库里有好几处
「提示用户去装 python3」的 `echo`，里面就含 `apt-get install` ——
如果连提示文本都算越界，这个检查器第一天就会被无视，然后被删掉。

当前状态：**B1 / B2 / B3 / B5 / B6 / B7 / B8 / B9 通过；B4 有 2 条提示**（两个在用模块还没有配远程后端，
见下一节）。这一点是诚实的：`backend.tf.example` 已经写好放在各模块里，但**没有替你启用** ——
因为启用它需要先有 OSS 桶和 OTS 锁表，凭空打开只会让 `terraform init` 直接失败。

### CI/CD 是唯一触发器：把变更收敛成一条路径

B1~B6 说的都是「谁不该碰谁」，但没回答**那到底谁能动手**。答案是流水线 ——
否则「以谁的本地环境为准」这个问题永远没有答案：每个人手跑的那次都算数。

`.gitlab-ci.yml` 是这台仓库里唯一能改动环境的路径：

```
validate ──→ plan ──→ apply(人工放行) ──→ configure ──→ verify
   │           │                           │
   │           └─ 产出 artifact ───────────┘
   │              tf-outputs.json
   └─ 边界校验 + 语法校验（不需要凭证，也不碰机器）
```

六个 stage、八个 job，其中三条设计约束是刻意的：

> **别和 [阶段三](#阶段三--cicd-流水线) / [阶段四](#阶段四--gitlab-流水线入口) 搞混。**
> 那两阶段管的是**应用交付**链路（Harbor webhook → Jenkins 部署），
> Terraform 在里面只负责声明「Harbor 项目 / webhook / Jenkins 任务」这些**配置对象**。
>
> 这里的 `.gitlab-ci.yml` 是另一回事：它跑的是**基础设施自身的变更**
> （`terraform plan/apply` 与 `ansible-playbook`）。
> 两条流水线都跟 GitLab 有关，但一条改的是**应用**，一条改的是**环境** ——
> 而且它们**不能互相调用**，否则就又回到「谁对状态负责」说不清的局面。
> 这也是为什么 B7 会检查「TF 与 Ansible 不在同一个 job 里」，
> 而不是只检查「有没有流水线」。

**① Terraform 与 Ansible 是两个独立 job，谁都不调用谁。**
数据靠 artifact 传递：`plan` / `apply` job 把 `terraform output -json` 存成
`.ci-artifacts/tf-outputs.json`，`configure` job 用
`gen_cloud_inventory.py --from-output-json` 消费它。这样 Ansible 那个 job 里
**没有 state、没有云凭证** —— 越界不是「约好了不做」，而是**做不到**。

这也顺手解决了一个老问题：以前那份清单是 Ansible 自己去调 `terraform output` 生成的，
等于要求 Ansible 那一侧也拿到整套 Terraform 权限。改成 artifact 之后，
CI 里的跨层调用数降到了 **0**。

**② 变更类动作必须人工放行。** `validate` / `plan` 和只读体检自动跑；
`apply`、任何碰本地机器的 job、`teardown` 都必须有人点那一下（`when: manual`）。
`apply` 用的还是 `plan` 阶段产出的那份 `tfplan`，不是重新算一遍 ——
否则人工点的那一下，批准的不是你看见的那份计划。

**③ Ansible 的调用一律显式 `--limit`。** 不加限定的 `site.yml` 会同时覆盖
`cloud` 和 `local` 两组。本地服务器虽然被 `roles/guard` 拦着，但流水线里不该依赖
「拦得住」—— 该直接不碰。碰 `local` 的 job 还要再叠一道 `when: manual`，
和 Ansible 那侧的 `local_mutation_allowed` 形成**两道门**。

> **这套约束是可执行的**：上面每一条都由 B7 盯着。故意把 `apply` 的 `when: manual`
> 拿掉、或者给自动跑的 job 悄悄带上解锁开关，B7 会立刻报出来（验证记录见
> [验证状态](#验证状态)）。

### 跨层衔接：默认零条，每条都要写出来

B1/B2 把跨层调用一律禁掉。但现实里总有一两次数据要从一层传到另一层 ——
最典型的就是「云机器的 IP 只有 Terraform 知道，Ansible 必须拿到它才能连上去」。

「绝对禁止」和「随便来」都不对。正确形态是 **默认零条 + 每条都写成显式声明**，
放在仓库根的 `boundaries.yml` 里：

```yaml
handoffs:
  - from: ansible                      # 谁发起
    to: terraform                      # 调谁（只能填 terraform，见下）
    mode: read                         # 只能填 read
    where: ansible/tools/gen_cloud_inventory.py
    why: "云服务器的 IP 只有 Terraform 知道……"   # 必须写：写不出理由说明它本就不该存在
```

**为什么 `to` 被钉死成 terraform。** 能被声明的衔接只有一种形状：某一层**只读地**读
Terraform 的输出。反方向（调用 Ansible）是不能声明的 —— 那是 B1 的地盘，而 B1 要守的
性质（`apply` 的结果 = 这次变更的结果）不是「这次特殊」就能破例的。
**没有声明文件时，等价于「一条都不允许」**，这是默认值。

顺带一个好处：方向钉死之后，「单向」这条性质就是**结构性**的 —— 环根本构造不出来，
所以这里**没有**成环检测。这是刻意的：**一个永远不会响的检查比没有检查更糟，
它会让人以为查过了。**

**这个声明文件的解析器是刻意写窄的。** 它只认那一小撮 YAML 子集
（顶层键 0 缩进、列表项 2、字段 4），遇到子集之外的写法一律**报错并给出行号**，
绝不猜。理由是：猜错的后果是「声明被静默忽略」——**那比没有声明更危险，因为人会以为它生效了**。
如果你确实要写更复杂的 YAML，请同时去改 `tools/check_boundaries.py` 里的 `read_sections()`。

> **在流水线里不需要任何声明。** CI 里 Ansible 那一侧连 `terraform` 命令都不执行，
> 跨层调用数是 0。这份清单管的是「人在自己机器上手工跑」的那条路径 ——
> 两条路径都要有兜底，但**只有一条需要例外**。

### 豁免登记：不参与运行的东西也得登记在册

检查器只认**运行路径**上的代码。但仓库里总有些东西不参与运行：停用的旧方案、
语法参考、教学对照。它们不必遵守运行时的边界，因为根本不会被跑起来。

问题在于「谁不参与运行」如果靠**目录名**去猜，两个方向都会坏：

- 把目录改个名，豁免就**静默失效** —— 检查器开始对着一堆存档代码报红，而那些红全是噪音
- 反过来更糟：真在跑的代码塞进一个叫 `archive-` 的目录，就**永远查不到了**

所以豁免必须登记在册，和跨层衔接放在同一个文件里：

```yaml
exemptions:
  - path: vmware-workstation/archive-vm-clone   # 不参与运行的路径
    why: "停用的旧方案……"                       # 为什么它不参与运行
  - path: vmware-vsphere-reference
    why: "语法参考……"
```

两条纪律：

1. **豁免的语义是「不参与运行」，不是「网开一面」。** 它对**所有**检查生效 ——
   而不是给每条规则单独配一个跳过开关。留了开关，迟早有某条检查忘了打开它，
   于是最该被豁免的那批代码反而被查得最狠。
2. **豁免清单自身也被检查。** 缺 `why`、登记了不存在的路径（豁免腐烂）、
   或者用了子集之外的写法，B9 都会报出来。没有这一层，`exemptions` 就只是一份
   谁都能往里塞、塞完不用负责的名单 —— 那和隐式豁免没有区别，
   只是把「猜目录名」换成了「猜谁的提交」。

当前登记的只有两条，都是「不在运行路径上」的：

| 路径 | 为什么不参与运行 |
|---|---|
| `vmware-workstation/archive-vm-clone/` | 停用的旧方案：用 `vmrun` 从模板克隆宿主机虚机。与 B9 直接相悖，已停用，仅留作教学对照 |
| `vmware-vsphere-reference/` | 语法参考：需要 vCenter 才能 `init`，本项目不用它。因此也不作为「在用模块」检查（也就不需要 backend） |

> 判据是**路径段**而不是字符串前缀（`foo/bar-OLD` 不是 `foo/bar` 的子路径）——
> 这个坑在 Ansible 的禁区判定里踩过一次，见踩坑 22。

### state 放哪：本地 → 远程（含加锁）

`state` 默认在本地（`.gitignore` 已经排除了它，不会误提交）。**单人练手够用，多人协作必须换掉。**

本地 state 的两个后果，都不是理论问题：

1. **并发 apply 互相覆盖** —— A 和 B 同时 apply，各自读到旧 state，后写的那份把先写的覆盖掉。
   结果是资源真实存在于云上，但 state 里没有它 —— **永远 destroy 不掉的幽灵资源**，
   而且持续计费。Terraform 的锁就是为这个存在的，本地 state 没有锁。
2. **state 里是明文凭证** —— `user_data`、密钥、密码都会写进去，散落在每个人的电脑上。

启用步骤（模板已备好，两个模块各一份）：

```bash
# 1) 先建两样东西（Terraform 不会替你建）
#    · 一个专用 OSS Bucket
#    · 一个 OTS(TableStore) 实例 + 一张锁表
#      ★ 锁表必须有一列叫 LockID、类型 String —— 漏了会报锁失败

# 2) 启用后端
cd aliyun-ecs
cp backend.tf.example backend.tf      # 填掉里面的 <占位符>
terraform init -migrate-state         # 把现有本地 state 搬上去

# 3) 验证真的搬对了
terraform plan                        # ★ 必须是 "No changes"，
                                      #   否则说明 state 没搬对，别继续 apply
```

凭证走环境变量（`ALICLOUD_ACCESS_KEY` / `ALICLOUD_SECRET_KEY`），**不要写进 `backend.tf`** ——
写进去会被存进 `.terraform/` 和 plan 文件里。CI 里建议用 RAM 角色换临时凭证。

> 本地那套（VMware）用的是**同一个 OSS 桶 + 不同 prefix**。理由：state 放哪和资源在哪是两件独立的事，
> 本地虚机同样需要防并发覆盖。如果不许依赖公有云，等价的替换方案是 `pg` / `consul` /
> 内网 MinIO（`s3` + `use_lockfile`），接口一样，只换那一段配置。

### 为什么 K8s 工作负载不写进 Terraform

Terraform 能管 K8s（有 provider），这个项目**故意不这么做**：

| 用谁管 | 适合的节奏 | 例子 |
|---|---|---|
| Terraform | 基础设施的生命周期 | VPC、ECS、集群本身、安全组 |
| K8s YAML + git | 应用编排，和代码一起 review | Deployment、Service、ConfigMap |
| Helm | 带参数的发布 | 同一套 chart 发多个环境 |
| ArgoCD / Flux | 多环境持续交付，自动纠偏 | 「线上和 git 不一致」自动拉回 |

关键在**节奏不同**：`terraform apply` 的语义是「把基础设施对齐到声明」，
一次可能动 20 个资源；应用发布的语义是「把这一版发上去」，需要灰度、回滚、看指标。
把后者塞进前者，就会出现「改一个镜像 tag 导致 plan 里有 3 个资源要重建」这种局面。

这个仓库里 `kubectl` 只出现在**只读盘点**里（`kubectl get` 看 K3s 状态），
这是「盘点现状」，不是「声明式管理」，不越界。

---

## 阶段二 · 本地部分：三档递进

### 第 0 步：先弄清一个容易搞错的点

**Jenkins 是 docker-compose 部署的。** 也就是说磁盘上那份
`/data/jenkins-data/docker-compose.yaml` 就是它的声明式配置 ——
它已经把「容器该长什么样」描述清楚了。

由此引出一个关键判断：**不要让两个声明式工具管同一个对象。**

如果 Terraform 无条件按自己的模板覆写那份 compose 文件，你手工加的东西会被抹掉，
其中最要紧的是这些特意挂进去的宿主资源：

```yaml
- /data/jenkins-data:/var/jenkins_home            # 数据目录（bind mount）
- /var/run/docker.sock:/var/run/docker.sock       # 在宿主机上构建镜像
- /usr/bin/docker:/usr/bin/docker                 # 容器内直接用宿主 docker CLI
- /usr/local/bin/k3s:/host-bin/k3s:ro             # 直接操作宿主 K3s
- /usr/local/bin/kubectl:/host-bin/kubectl:ro     # 部署到 K3s
- /etc/rancher/k3s:/etc/rancher/k3s:ro            # 读 K3s 配置
- /root/.kube:/root/.kube:ro                      # 读 kubeconfig
```

这些是一个普通 Jenkins 的 `docker run` 命令里**不会出现**的东西。
一旦被覆盖掉，事故现象很有迷惑性：容器正常起来、Jenkins 界面正常、任务能触发，
但每个部署任务都失败，报 `kubectl: command not found`。

> 盘点与核对脚本会**逐个确认其中 5 个容器内路径**是否存在：
> `/var/run/docker.sock`、`/host-bin/k3s`、`/host-bin/kubectl`、`/etc/rancher/k3s`、`/root/.kube`。
> 少任何一个都会在报告里单独列出来。这比笼统说一句「挂载正常」有用得多——
> 漏挂 `.kube` 时 Jenkins 界面毫无异常，只有部署任务会挂。

所以本配置的能力是**逐级放开**的：

| 档 | 配置 | 行为 |
|---|---|---|
| 1 | `manage_containers = false` | 只盘点，完全不碰 compose（**默认**） |
| 2 | `manage_containers = true`<br>`jenkins_manage_compose = false` | **只核对**：逐项比对并报告差异，一行都不改 |
| 3 | `manage_containers = true`<br>`jenkins_manage_compose = true` | **接管**：按模板覆写 compose 文件并重启栈（覆写前自动备份） |

默认停在第 1 档；**第 2 档是推荐的长期状态** —— 它把「现实与期望的差异」
摆在你面前，却不会动任何东西。

### 第一步：只读盘点（务必先跑这个）

```bash
cd vmware-workstation
cp terraform.tfvars.example terraform.tfvars
# 修改 k3s_server / k3s_agent 的 IP、用户名、私钥路径

terraform init
terraform validate
terraform apply      # manage_containers 默认为 false，不会改动任何东西
```

`apply`（实为纯读取）会打印四份盘点结果：

1. **节点基础信息** — 主机名、系统、内核、CPU、内存、磁盘、Docker/K3s 版本
2. **容器与 compose 栈** ← 最关键。列出所有容器，以及 Jenkins 的**真实端口、挂载、
   重启策略、compose 工程名、compose 文件所在路径**，并逐个检查那 5 个宿主资源挂载
   是否齐全；同时打印 Harbor 各容器状态，以及宿主机上的所有 compose 工程
3. **K3s 集群状态** — 节点列表（确认一主一从）、命名空间、Pod
4. **从节点 GitLab 服务健康** — `gitlab-ctl status`、版本、数据目录占用、备份目录现状

> 这一步的价值：把"机器上现在到底有什么"变成 Terraform 输出，后续做任何变更时心里有底。

### 第二步：让 Terraform 核对 Jenkins compose 栈（仍不改动任何东西）

```hcl
# terraform.tfvars
manage_containers      = true
jenkins_manage_compose = false   # 关键：只核对，不覆写

# 期望值取自你实际的 compose 文件，重点是端口
jenkins_http_port   = 8081                  # 宿主端口（容器内是 8080）
jenkins_home_dir    = "/data/jenkins-data"
jenkins_compose_dir = "/data/jenkins-data"
```

```bash
terraform apply
```

`scripts/jenkins-compose.py` 会做三层检查：

```
A. 运行中的容器 vs 期望          —— 抓「容器被手工改过」
   · 镜像 / 重启策略 / Web 端口 / Agent 端口 / 数据目录挂载
   · 是否由 compose 创建（没有 compose 标签 = 它是被 docker run 起的）
   · 宿主资源挂载是否齐全（逐个检查 5 个容器内路径）
B. 磁盘上的 compose 文件 vs 期望 —— 抓「文件不是期望的那份」
C. 处置                          —— 核对模式只报告，一行都不改
```

**B 用的是语义比对，不是文本 diff。** 做法是把两个文件分别用
`docker compose config` 渲染成规范化配置再比。若直接 diff 文本，注释、缩进、
字段顺序的差异都会被算成「有漂移」，于是每次 apply 都提示要覆写文件，很吵。
语义比对只报真实配置变化，你手工写的注释不会被无故判为漂移。

> **一个使用细节**：`null_resource` 的 provisioner 只在 `triggers` 变化时才重跑，
> 所以配置没变时第二次 `apply` 不会重新打印报告。想强制重看一次：
>
> ```bash
> terraform apply -replace=null_resource.jenkins_stack
> ```

### 第三步：确认无误后，再决定是否让 Terraform 接管

只有当你决定「compose 文件以后由 Terraform 生成」时才开这一档：

```hcl
jenkins_manage_compose = true
```

行为：发现语义差异 → 备份原文件（`.bak.<时间戳>`）→ 写入新文件 → `docker compose up -d`。

**不会用 `down -v`，也不会碰数据目录。** 把容器收敛到期望状态本来就是
`docker compose up -d` 的原生能力，不需要也不应该靠删除来实现。

如果你更愿意继续手工维护那份 compose 文件，保持 `false` 就好 ——
核对模式本身已经能把差异摆在你面前，那才是它主要的价值。

### 幂等性与数据安全

这套代码对「状态」和「数据」的原则很明确：**收敛状态靠声明式比对，绝不靠删除。**

```
核对模式           -> 连文件都不写
接管模式           -> 只覆写 compose 文件，且覆写前先备份
                     （备份失败就中止，不会出现「原文件没了、新文件也没写成」）
terraform destroy  -> 默认什么都不做（jenkins_stack_down_on_destroy = false）
                      即使设为 true，也只是 docker compose stop，容器保留
```

幂等性来自「先比对、后动手」：每次执行都先算清差异，没有差异就 exit 0。
所以**反复 `apply` 不会动你正在跑的服务**。Terraform 这侧的 `triggers` 放的是
「配置指纹」（镜像、端口、目录、脚本哈希、模板哈希），配置没变就不触发重跑。

⚠️ **数据安全上有一点要清楚**：Jenkins 的数据是 **bind mount 到宿主机目录**的，
不存在「`docker rm` 不碰命名卷」那层保护。目录被删或被改，配置、插件、
构建历史就一起没了。**建议把 `/data/jenkins-data` 纳入备份** ——
顺带一提，你的 compose 文件也在这个目录里，所以备份它会连带把 compose 文件一起备份。

### 关于 Harbor：不是「暂未接管」，是「不能接管」

你的 Harbor 是**官方默认配置部署** —— `harbor.yml` 未改动、用 `install.sh`
生成的 compose 栈，约 9 个容器：

```
nginx / harbor-portal / harbor-core / registry / registryctl /
harbor-db / redis / harbor-jobservice / harbor-log
```

所以 `manage_harbor` **恒为 false**。这不是保守，是技术上不成立：

1. **官方没有单容器发行版。** 用 `docker run` 复刻这个栈，等于手写一遍 Harbor
   的容器编排逻辑（还要处理 db/redis 的启动顺序依赖），失败概率很高。
2. **代价不对称。** Harbor 里是你所有已推送的镜像，以及标签、扫描结果、复制策略。
   栈被搞坏不是「停一会儿」，而是整条流水线的制品全没了 ——
   包括正在被 K3s 拉取的那些镜像。
3. **没有收益。** Harbor 的日常变更（项目、用户、保留策略）都在 API 层，
   而 API 层本配置是管的（见 `manage_cicd`）。

**它的容器与配置继续归 `docker compose` + `harbor.yml` 管；项目与 webhook 策略归 Terraform 管。**

---

## 阶段三 · CI/CD 流水线

你把这条链路打通了，它落在**主节点**上：

```
  [从节点] GitLab ──── Push Hook ────> Jenkins 构建 ──── 推送镜像 ────> Harbor
     ▲                                  [主节点]                        │
     │                                                                  │ PUSH_ARTIFACT
     │                                                                  v
     │                                                            Harbor webhook
     │                                                                  │
     │                                                                  │ HTTP POST
     │                                                                  v
     └── git push ──────────────────────────────────────────> Jenkins 部署任务
                                                                        │
                                                                        v
                                                                   K3s 拉取新镜像
```

前两阶段管的是「进程在不在跑」，这一阶段管的是「**流水线怎么连起来**」。
GitLab 那一跳（链路入口）在后面的 ④ 单独讲。

### ⚠ 先说三个真实风险，都是这套架构的典型坑

#### 风险一：`PULL_ARTIFACT` 会造成部署死循环（最严重）

Harbor 的 webhook 支持这些事件类型：

| 事件 | 含义 | 是否该监听 |
|---|---|---|
| `PUSH_ARTIFACT` | 推送镜像 | ✅ 通常只要这个 |
| `PULL_ARTIFACT` | **拉取**镜像 | ❌ **危险** |
| `DELETE_ARTIFACT` | 删除镜像 | ❌ 不建议 |
| `SCANNING_COMPLETED` / `_FAILED` / `_STOPPED` | 扫描相关 | ❌ 不建议 |
| `QUOTA_EXCEED` / `QUOTA_WARNING` | 配额 | ❌ 不建议 |
| `REPLICATION` | 复制状态变化 | ❌ 不建议 |
| `TAG_RETENTION` | 标签清理 | ❌ 不建议 |

**为什么 `PULL_ARTIFACT` 是致命的**：你的部署任务本身就要拉取镜像。如果 webhook 监听了拉取事件，就会形成：

```
部署 -> 拉镜像 -> 触发 PULL_ARTIFACT -> webhook 回调 -> 再部署 -> 再拉镜像 -> ...
```

**无限循环**，Jenkins 和 K3s 会被一起拖死。

所以 `webhook_event_types` 设了一条 `validation`：**只要包含 `PULL_ARTIFACT` 就直接报错**，并且 `cicd.tf` 里还有一道 `precondition`，外加运行时脚本会检测并纠正已有配置。三重防护，因为这个坑代价太大。

#### 风险二：推多个 tag 会触发多次部署

如果构建时同时推了 `latest` 和 `v1.2.3`，Harbor 会发**两次** `PUSH_ARTIFACT`，部署任务就执行两次。

建议：推送时只推一个语义化版本 tag，或让部署任务加并发锁（Jenkins 的 `disableConcurrentBuilds()`）。

#### 风险三：webhook 地址不能写 `localhost`

**Harbor 运行在容器里**，从 Harbor 容器内部访问 `localhost` 是它自己，根本到不了 Jenkins。

所以 `webhook_endpoint_url` **必须写主节点的 IP**：

```
✅ http://192.168.1.101:8081/generic-webhook-trigger/invoke?token=xxx
❌ http://localhost:8081/generic-webhook-trigger/invoke?token=xxx    ← localhost 指向 Harbor 自己
❌ http://192.168.1.101:8080/generic-webhook-trigger/invoke?token=xxx ← 端口错，Jenkins 在 8081
```

相对地，Terraform **调 API 时**用的是 `localhost`（脚本在主节点上执行，端口映射到宿主机）。两个场景方向相反，很容易搞混 —— 这也是变量注释里反复强调的原因。

★ **端口这一项还有一道自动守卫**：`terraform apply` 输出的安全检查段落会提取 webhook
地址里的端口，与 `jenkins_http_port` 比对，不一致就直接标红（`✗ 危险`）。

为什么要专门做这个检查：「地址写对了但端口写错」是这套架构里最隐蔽的失效方式 ——
回调打到宿主机上没人监听的端口，连接被拒，而 Harbor / GitLab 都不把它当致命错误，
只在各自的投递记录里留一笔。现象就是「代码推上去了、镜像也推上去了，但流水线毫无反应」，
而且第一反应通常是去查 Jenkins 任务，方向就偏了。

### 用法

```bash
cd vmware-workstation

# 1. 默认 manage_cicd = false，先跑一次看现状
terraform apply     # 输出 cicd_pipeline 会打印链路构成 + 三项安全检查

# 2. 在 terraform.tfvars 补齐
```

```hcl
harbor_admin_password  = "你的 Harbor 管理员密码"
jenkins_api_token      = "在 Jenkins 用户配置里生成的 API Token"   # 不是登录密码
webhook_endpoint_url   = "http://192.168.1.101:8081/generic-webhook-trigger/invoke?token=xxx"
#                        ★ 端口是 8081（Jenkins 宿主端口），不是 8080

harbor_project         = "apps"
harbor_webhook_name    = "jenkins-deploy-trigger"
webhook_event_types    = ["PUSH_ARTIFACT"]   # 保持只有一个

manage_cicd = true
```

```bash
terraform apply
```

`admin` 密码建议用 `terraform.tfvars` 管理，或更好——用环境变量：

```bash
export TF_VAR_harbor_admin_password="..."
export TF_VAR_jenkins_api_token="..."
```

### 接管之后 Terraform 保证什么

- Harbor 项目存在
- webhook **只**监听 `PUSH_ARTIFACT`
- webhook 回调地址与认证头符合期望
- 用名字做幂等识别：已存在且一致则什么都不做

### 关于 Jenkins 任务：默认不接管

`manage_jenkins_job` 默认 `false`，这是有意的。

**Jenkins 任务的流水线逻辑应该写在应用仓库的 `Jenkinsfile` 里**（Pipeline-as-Code），这样演进走 Git 提交、可 review、可回滚。Terraform 只适合保证「任务存在且指向正确的仓库和分支」这个壳。

如果你已经手工建好任务，保持 `false` 完全没问题。

### destroy 默认不删 webhook

`cicd_cleanup_on_destroy` 默认 `false`。因为你的 webhook 是手工配好的、流水线正靠它跑着——Terraform 一 `destroy` 就删掉会**直接打断业务**。

需要删除时显式设为 `true`，或去 Harbor 界面手工操作。

---

## 阶段四 · GitLab 流水线入口

这一阶段对应 `gitlab.tf`，管的是**跑在从节点上**的 GitLab——也就是整条流水线的**起点**。

### 先说清楚：GitLab 是实体服务，Terraform 不碰它本身

你的 GitLab 是 **Omnibus 包直接装在系统里的**（`apt`/`yum` 安装），不是 Docker 容器。这一点决定了 Terraform 的管理边界：

| 层 | 谁管 | 为什么 |
|---|---|---|
| 服务启停、进程健康 | `systemctl` + `sudo gitlab-ctl status/restart` | 系统服务的职责，Terraform 插手只会添乱 |
| GitLab 配置（`/etc/gitlab/gitlab.rb`） | 手工/Ansible，改完 `gitlab-ctl reconfigure` | 用 `null_resource` 硬改 gitlab.rb 是反模式——没有 provider 的 read，配置漂移了 Terraform 根本看不见 |
| **项目 + webhook（API 层）** | **Terraform（`gitlab.tf`）** | 纯 REST API 调用，与安装方式无关，最适合声明化 |

Terraform 只管一件半事：

| 目标 | 脚本 | 说明 |
|---|---|---|
| GitLab 项目 | `scripts/gitlab-project.py` | 仓库存在即不动，不存在才创建 |
| Push webhook | 同上 | 配好「push 之后去哪通知 Jenkins」 |

服务本身的健康由**只读盘点**覆盖（`inventory.tf` 的从节点盘点会自动识别安装方式，对实体服务打印 `gitlab-ctl status`、版本、external_url、数据目录占用、备份目录现状）。

### ★★ 接入 GitLab 最容易踩的坑：它默认拒绝回调私有网段

这是本项目里最隐蔽的一个失效模式，值得单独说清楚。

GitLab 从 10.6 起，为了防止 SSRF，**默认禁止 webhook 请求以下地址**：

```
127.0.0.1、::1、0.0.0.0
10.0.0.0/8、172.16.0.0/12、192.168.0.0/16   ← 你的实验环境全在这里面
IPv6 site-local
```

你的整条链路都在 `192.168.x.x`，也就是说：**不打开放行，GitLab 发出的 webhook 会被直接拦截**。而它的表现非常坑人——

```
git push 成功
  -> Jenkins 没有任何反应
  -> GitLab 界面上也没有明显报错（回调失败只记在 webhook 详情页的 Recent events 里）
  -> 你的第一反应通常是「Jenkins 任务配错了」或「webhook 地址写错了」
```

所以 `gitlab-project.py` 里做了三层：

1. 打开全局开关 `allow_local_requests_from_web_hooks_and_services`
2. 把 Jenkins 的 `host:port` 加进出站放行白名单（双保险，防某个版本开关改名）
3. **重新读取设置做验证**——而不是设完就假定成功

第 3 点是有意的：这个设置如果不生效，整个流水线入口就是死的，值得多花一次 API 请求去确认。

### 只有一个开关

```hcl
manage_gitlab_cicd = false   # 管项目和 Push webhook（API 层）
```

服务本身不在 Terraform 管理范围内（见上），所以不再需要单独的「容器层」开关。

**推荐第一步就开它**：

```bash
# terraform.tfvars
manage_gitlab_cicd = true
gitlab_api_token   = "..."                    # 需要 api scope + 管理员权限
gitlab_webhook_endpoint_url = "http://192.168.1.101:8081/generic-webhook-trigger/invoke?token=..."
```

为什么先做这一步：入口的问题最隐蔽。先把「push 能触发 Jenkins」这件事确认下来，排查范围最小。

### API Token 怎么来

需要 `api` scope，且**需要管理员权限**——因为「本地网络放行」是实例级设置，普通用户改不了。

两种获取方式：

```
方式一（界面）：GitLab -> 用户设置 -> Access Tokens -> 勾选 api -> 生成

方式二（命令行，在从节点上执行，会直接打印出 token）：
  sudo gitlab-rails runner \
    "u=User.find_by_username('root'); \
     t=u.personal_access_tokens.new(scopes: ['api'], name: 'terraform-managed', \
     expires_at: 365.days.from_now); t.save!; puts t.token"
```

> 注意 token 有字符集校验（`[A-Za-z0-9_-]`，20~64 位）。这不是为难你——脚本内要把它注入一段 Ruby 代码，限制字符集可以彻底避开 `sed` 的转义陷阱，而 GitLab 自己生成的 token 天然满足。

### 怎么验证入口真的通了

只看配置对不对是不够的。`gitlab_webhook_test_on_apply` 会让脚本向 Jenkins **实打实发一次 push 事件**，并返回 Jenkins 的真实响应码——这是唯一能证明链路通的方法。

```hcl
gitlab_webhook_test_on_apply = true   # 验证时临时打开
```

**代价要说清楚**：测试发出的是真实 push 事件，会**真的触发一次完整流水线**（构建 → 推镜像 → 部署）。所以默认 `false`，验证完记得改回去。

不想触发构建的话，也可以看脚本输出的「最近 webhook 投递记录」，那里会列出最近几次投递的响应码——只读，没有副作用。

### external_url 对齐

`gitlab_external_url` 只是 Terraform 调 API 用的基址，**它必须与 `/etc/gitlab/gitlab.rb` 里的 `external_url` 写成一样的形式**（含端口/协议）。不一致时 GitLab 可能返回重定向，API 调用会莫名失败。盘点输出会打印 gitlab.rb 里实际的 external_url，照着填即可。

### GitLab 与 K3s 同机共存

从节点上同时跑着 K3s agent 和 GitLab：K3s agent 用 **containerd**，GitLab 实体服务自带 Omnibus 运行时（bundled nginx/puma/postgres），互不冲突。

需要留意的是**内存**：GitLab 官方建议 4GB 起步，而 K3s agent 上的业务 Pod 也在吃内存。盘点输出会打印从节点的内存与 GitLab 数据目录占用——这台从节点是全链路里资源最紧张的一台。

想给 GitLab 降内存的话，改 `/etc/gitlab/gitlab.rb`（`puma['worker_processes']`、`sidekiq['concurrency']`、关 prometheus_monitoring）然后 `gitlab-ctl reconfigure`——这属于实体服务的配置管理，不归 Terraform。

### 数据安全（和容器部署完全不同）

```
容器部署：容器重建 -> 数据不丢（bind mount 在宿主机）
实体服务：没有「重建」概念，磁盘坏 = 数据没
```

所以备份比容器部署**更紧迫**：请定期跑 `sudo gitlab-backup create`，并确认 `/var/opt/gitlab/backups` 里有备份文件。盘点输出会打印备份目录现状——如果显示「备份目录不存在」，说明你的仓库还没有第二份拷贝，值得尽快处理。

### 排查速查表

| 现象 | 先查哪里 |
|---|---|
| push 后 Jenkins 完全没反应 | GitLab 项目 -> Settings -> Webhooks -> Recent events（看有没有投递、响应码多少） |
| 投递记录显示失败 / 被拒绝 | 回到本文「默认拒绝回调私有网段」一节，确认放行设置 |
| API 调用莫名失败 / 重定向 | `gitlab_external_url` 与 gitlab.rb 里的 `external_url` 不一致 |
| GitLab 整个服务无响应 | `sudo gitlab-ctl status` 看哪个组件挂了，`sudo gitlab-ctl tail` 看日志 |
| 页面 502 / 容器反复重启 | 内存不够（官方建议 4GB 起） |

---

## 阶段五 · Ansible：批量管理云服务器与本地服务器

前面四个阶段是「一台机器一件事」—— Terraform 管云资源、管若干个存量服务对象。
`ansible/` 解决的是另一个问题：**同一件事要在 N 台机器上做**（装运行时、对齐基线、把服务拉起来），
而且这 N 台机器里**大部分是不能碰的存量环境**。

所以这一层的第一设计目标不是"功能多"，而是**"不该动的东西一定动不到"**。

### 它负责什么，不负责什么

| | 负责 | 不负责 |
|---|---|---|
| `ansible/` | 机器基线（包、目录、时区、内核参数、limits）；云机器上的 Docker 与容器栈 | 不碰任何云资源（VPC / 安全组 / ECS 的创建销毁仍是 Terraform） |
| | 全机器只读体检，输出「现状报告」 | 不碰 Jenkins / Harbor / GitLab 的**服务对象**（项目、任务、webhook 仍是 Terraform 的 `cicd.tf` / `gitlab.tf`） |
| | | 不碰本地机器上的任何文件（默认；见下） |

两条边界都是同一条原则：**同一份声明式配置，不能让两个工具同时声明。**
（这条原则在前几轮已经踩过一次，见 [验证状态](#验证状态) 第 13 条：旧的 `docker run` 方案和 compose 抢同一个容器。）

### 云机器随便改，本地机器默认一个字节都不改

`inventory/` 里只有两个组，性质完全相反：

| 组 | 来源 | 性质 |
|---|---|---|
| `cloud` | 由 `tools/gen_cloud_inventory.py` **生成**（IP 每次 `apply` 都可能变，所以不手写）。取数有两种方式：本地手工跑时它自己调 `terraform output`；流水线里走 `--from-output-json` 读 artifact，**全程不碰 terraform** | 随时可以重建，允许任意变更 |
| `local` | `inventory/hosts.yml` **手写** | 手工维护、攒了很久、上面跑着别人在用的服务 —— **默认完全只读** |

「本地不覆盖」不是靠"记得别写错 playbook"来实现的，而是三层叠加：

**第一层 · 结构性隔离。** 会改东西的 role（`baseline` 的 apply 部分、`docker_engine`、`app_stack`）
只出现在 `hosts: cloud` 的幕里。本地机器根本不在那些幕的 host 列表里，**不是"跳过"，是压根没轮到它**。

**第二层 · 运行前断言。** `guard` role 是所有幕的第一个 role，它在任何任务之前算一次保护状态，
并对"变更型幕"做硬断言：

```yaml
- name: "guard · 断言：受保护的机器不允许执行变更型任务"
  ansible.builtin.assert:
    that:
      - (wf_intent | string) != 'mutate'
  when: wf_protected | bool
```

这一层防的是**第一层被绕过**：有人把本地机器挪进 `cloud` 组、有人复制粘贴幕时忘了改 `hosts`、
有人把 `hosts: cloud` 改成了 `all` —— 都会在这里被拦下，而不是等到机器已经被改了才发现。

**第三层 · 逐任务许可。** 通过前两层之后，role 内部还把"新增"和"覆盖"分成两类：
不覆盖已有对象的任务不带条件（补缺本身是安全的）；会覆盖已有文件/设置的任务一律带 `when: wf_can_write | bool`。

> 这套设计有意选择**"断言 + 报错"而不是"静默跳过"**。
> 静默跳过的结果是"看起来跑成功了，但什么都没做"，而这里会明确告诉你：
> 你在往本地服务器写东西，以及要显式解锁该怎么做。

### 两个解锁开关

默认什么都不用传，就是最安全的状态。想动本地机器，必须**显式**解锁，而且是两级：

```bash
# ① 默认：只读体检。对本地服务器一个字节都不改。
ansible-playbook playbooks/verify.yml

# ② 看变更会做什么（干跑，不落地）
ansible-playbook playbooks/site.yml -e local_mutation_allowed=true --check --diff

# ③ 真的改（只补缺：装缺失的包、建缺失的目录；已存在的文件一律不碰）
ansible-playbook playbooks/site.yml -e local_mutation_allowed=true

# ④ 连"覆盖已有设置"也放开 —— 不建议，除非你备份过
ansible-playbook playbooks/site.yml -e local_mutation_allowed=true -e local_overwrite_allowed=true
```

两个开关的关系是有意的：**「能覆盖」必须蕴含「能补缺」**，而不是各管各的。
`wf_can_write` 的公式里同时要求两个开关，理由写在 `roles/guard/tasks/main.yml` 的注释里 ——
如果只认 `local_overwrite_allowed`，就会出现「没解锁变更、却已经拿到覆盖权限」这种反直觉状态。
真实路径上它会被幕的 `meta: end_play` 挡住，但那是**靠 playbook 的写法**保证的，不是靠保护公式本身。
保护公式自己就该是单调的，不该依赖调用方记得先加门闩。

`tools/selftest.py` 用 16 种开关组合的穷举把这条不变量固定下来了。

### 五幕，风险由低到高

`playbooks/site.yml` 是唯一入口：

| 幕 | 目标 | 意图 | 做什么 |
|---|---|---|---|
| A | `all` | `read` | 只读体检，报告写到**控制端** `reports/` |
| B | `local` | `read` | 本地机器的基线**检测**（差什么看得见，但什么都不改） |
| C | `cloud` | `mutate` | 云机器基线 + Docker 引擎 |
| D | `cloud` | `mutate` | 云机器：拉起容器栈 |
| E | `local` | `mutate` | 本地机器变更 —— **默认整幕不执行**，`pre_tasks` 里第一个任务就是 `meta: end_play` |

幕 E 的写法值得解释一句。它照常声明 `intent: mutate`（于是 `guard` 一定会检查它），
但用一个显式的前置判断决定"这一幕到底要不要跑"。没有改成"条件成立时才把 intent 设成 mutate"，
因为那会让 `guard` 的判断条件变得又绕又隐蔽 —— **保护的逻辑必须一眼能看懂**。
两层各管一件事，读起来反而清楚。

判断"该不该跑"时，`changed=0` 是一个很干净的验收信号：`verify.yml` 里所有采集任务都带
`changed_when: false`，报告文件也写在控制端而不计入被管机器，所以"跑完它，被管机器的 changed 必须全是 0"。

### 路径禁区：为什么用「路径段」而不是「字符串前缀」

`group_vars/local.yml` 里列了本地机器上**绝对不能碰**的路径：

```yaml
wf_local_forbidden_paths:
  - /data/jenkins-data
  - /var/opt/gitlab
  - /etc/gitlab
  - /etc/rancher/k3s
  - /var/lib/rancher/k3s
  - /root/.kube
```

`baseline` 在**动手之前**会断言自己的写入目标不落在这些路径下（这是第三道防线，纯保险）：

```yaml
- name: "baseline · 断言：写入目标不得落在本地禁区路径下"
  ansible.builtin.assert:
    that:
      - wf_forbidden_hits | length == 0
```

判定用的是 `roles/guard/filter_plugins/wf_guard.py` 里的 `wf_under_any`。
它**按路径段（segment）比较，不是按字符串前缀**：

```python
# /data/jenkins-data-OLD 不在 /data/jenkins-data 之下 ——
# 但它是字符串前缀。用 startswith 会误伤这条正常路径。
```

这个区别看着小，但 `-OLD`、`-backup`、`.bak` 这种命名在实际环境里到处都是，
用前缀匹配 = 一条恰好以禁区路径开头的正常路径会被当成禁区，或者反过来放过真正的禁区子路径。
`tools/selftest.py` 里专门有一条用例锁住这个行为。

### 云机器的清单从哪来

不手写。云机器 IP 每次 `terraform apply` / `destroy` 都会变，手写必然过期，
而过期的清单意味着"对着一个已经不存在的 IP 跑 playbook"：

```bash
cd ansible
python3 tools/gen_cloud_inventory.py                   # 读 ../aliyun-ecs 的 terraform output
python3 tools/gen_cloud_inventory.py --allow-private   # 没有公网 IP 时也生成（默认跳过并说明原因）
```

生成的 `inventory/hosts.cloud.yml` 已在 `.gitignore` 里 —— 它是**产物**，不是源码。

> 生成器对每个值都用 `json.dumps` 渲染。这不是风格偏好：**JSON 是 YAML 1.2 的子集**，
> 所以 `json.dumps` 的结果一定是一个合法 YAML 标量，中文、引号、冒号都不需要额外转义。
> 手写 YAML 字符串时最容易出的就是"这个值里有冒号/引号"的转义 bug。

### 怎么验证

`ansible/` 有两层验证。**第一层不连任何机器**：

```bash
cd ansible
python3 tools/check_guard.py            # 静态护栏：证明「没有任何路径能让变更任务碰到本地服务器」
python3 tools/check_guard.py --selftest # 证明上面那个校验器真的会响
python3 tools/selftest.py               # 离线自测：模板渲染 + 保护公式真值表
```

`check_guard.py` 直接解析 inventory / group_vars / playbooks / roles，检查 8 类不变量：

1. 清单声明了保护、且 cloud 与 local 不重叠
2. 每个幕都声明了 `intent`
3. `guard` 必须是 `roles` 的第一个
4. 变更型幕的 `hosts` 不可能包含本地机器
5. 解锁型幕必须用 `hosts: local`/`all`，必须有 `meta: end_play` 门闩，且不得引用云专用的 role
6. 本地可达的 role 里，写任务必须带 `wf_can_write`；云专用的 role 必须自带"我不是本地机器"的断言
7. 禁区内不得出现 `baseline` 的写入目标
8. **`group_vars` 必须在 Ansible 真正会去加载的位置**（清单旁或 playbook 旁）——
   放错了整层保护变量都会被静默忽略，这是真实踩过的坑，见踩坑 24

它的 `--selftest` 用 13 个用例自证："该拦的拦下了，该放行的放行了"。
包括 `hosts: all` 这种经典写法错误、`web:!db` 这种**无法静态解析的 hosts**（解析不了时保守处理并告警），
以及 P8 的三个用例（放对位置 → 放行；放根上 → 拦下；两处都放一份 → 拦下多余的那份）。

**第二层是真机验证**。真正 `ansible-playbook` 跑一遍的步骤：

```bash
# 0) 控制端准备（Linux / macOS / WSL 原生文件系统）
python3 -m venv ~/.venvs/ansible && ~/.venvs/ansible/bin/pip install ansible

# 1) 只读验收：跑完它，任何被管机器的 changed 都必须是 0
~/.venvs/ansible/bin/ansible-playbook playbooks/verify.yml

# 2) 默认路径：五幕里 E 幕应被门闩跳过
~/.venvs/ansible/bin/ansible-playbook playbooks/site.yml

# 3) 干跑解锁路径：--check --diff 看它会补什么、跳过什么
~/.venvs/ansible/bin/ansible-playbook playbooks/site.yml -e local_mutation_allowed=true --check --diff

# 4) 云侧（先确认 daemon.json 需要的镜像加速器可达）
~/.venvs/ansible/bin/ansible-playbook playbooks/site.yml \
  -e '{"wf_docker_registry_mirrors": ["https://<可用的加速器>"]}'
```

国内网络下 Docker Hub 经常不可达，`nginx:alpine` 拉不下来。`wf_docker_registry_mirrors`
就是为这个准备的 —— 传进去之后它会写进 `daemon.json` 的 `registry-mirrors`，daemon 直接走加速器。

---

## 阶段一 · 阿里云部分

### 创建了什么

| 资源 | 说明 |
|---|---|
| VPC | 网段默认 `10.10.0.0/16` |
| vSwitch | 默认 `10.10.1.0/24`，可用区自动选择 |
| 安全组 | 开放 22 / 80 / 443，VPC 内 ICMP |
| ECS | 默认 1 台（可调到 10 台），经济型规格，**按量付费** |
| 密钥对 | 传了公钥才创建 |

实例创建时会通过 `user_data` 自动装 nginx 并写入一行欢迎页，开机后直接访问公网 IP 就能看到结果，方便验证。

### 操作步骤

```bash
cd aliyun-ecs

# 1. 配置凭证（推荐用环境变量，不要写进代码）
#    Linux / macOS
export ALICLOUD_ACCESS_KEY="你的AccessKeyId"
export ALICLOUD_SECRET_KEY="你的AccessKeySecret"
#    Windows PowerShell
#   $env:ALICLOUD_ACCESS_KEY="..."
#   $env:ALICLOUD_SECRET_KEY="..."

# 2. 准备变量文件
cp terraform.tfvars.example terraform.tfvars
#    按需修改 region、ssh_public_key、ssh_allowed_cidr 等

# 3. 初始化 → 预演 → 创建
terraform init
terraform validate
terraform plan
terraform apply

# 4. 查看结果
terraform output

# 5. 用完一定要销毁，避免持续计费
terraform destroy
```

### 几个值得留意的写法

- **`for_each` 而非 `count`** 管理实例。用 `count` 时，删掉中间一台会导致后面所有实例重建；`for_each` 只动目标那一台。
- **`user_data_replace_on_change = false`**，避免改个描述就触发实例重启。
- **`instance_charge_type = "PostPaid"`**（按量付费）+ `deletion_protection = false`，学习场景用完一把清干净。
- **`ignore_changes = [image_id]`**，镜像 ID 变更不直接重建，先让人看到 plan 再决定。
- 变量的 `validation` 块限制了实例数量上限，防止手滑开出几十台。

### 安全提醒

- `ssh_allowed_cidr` 默认 `0.0.0.0/0` **只适合练手**。真实使用请收敛成 `"你的公网IP/32"`。
- AccessKey 建议用 **RAM 子账号**，只给 ECS/VPC 相关权限，别用主账号密钥。
- `terraform.tfstate` 里有明文信息和凭证，已在 `.gitignore` 排除。
  **但「不提交」只解决了一半问题** —— 多人协作时本地 state 会互相覆盖，
  必须换成远程后端 + 状态锁。模板已经备好（各模块的 `backend.tf.example`），
  操作步骤见 [分层边界](#分层边界谁管什么不越界)。

---

## 本地部分的代码设计考虑

- **盘点与变更分离**：`inventory.tf` 全是只读操作，`containers.tf` 才涉及变更，由 `manage_containers` 开关控制。先看清再动手，这是对待存量环境的基本纪律。默认 `manage_containers = false`，第一次 `apply` 只盘点、不碰任何东西。
- **不"创建容器"，只"核对编排栈"**：这是本目录最重要的一次设计修正。Jenkins 是 docker-compose 管理的，而 **compose 文件本身就是一份声明式配置**。如果 Terraform 再去 `docker run` 一个同名容器，就是两个声明式工具抢同一个对象——除了端口冲突（8081/50000）和丢失那 6 个宿主资源挂载，不会有别的结果。所以 `containers.tf` 里没有 `docker run`，只有一个 `null_resource.jenkins_stack` 做三件事：核对运行态、核对 compose 文件、按档位决定动不动手。
- **触发器放"配置指纹"而非随机值**：`triggers` 里放 `config_hash = sha1(整份脚本配置)`、`script_hash = filesha1(脚本)`，以及端口/镜像等关键字段。配置没变就不触发脚本重跑；如果放时间戳或随机值，每次 `apply` 都会重建资源。
- **所有 SSH 参数必须进 triggers**：destroy 阶段的 provisioner **只能引用 `self.*`**，不能引用 `var.*` 或别的资源（连 `interpreter` 里放一个变量路径都会报 `Invalid reference from destroy provisioner`，实测过）。所以 `ssh_key`、`ssh_user`、`ssh_port`、`down_on_destroy` 都预先放进了 triggers —— 这是 Terraform 的硬性规则，踩过一次就会记住。
- **脚本走 stdin 传输**：把 JSON 配置 base64 编码后注入脚本，再通过 `ssh 'python3 -u -'` 送入远端，不在远端落任何中间文件。
- **绝不写用户的 compose 文件，更不删**：脚本只读 `/data/jenkins-data/docker-compose.yaml`。真要改写（`reconcile` 档），先备份成带时间戳的 `.bak`，再覆盖。`destroy` 最多 `compose stop`，且必须 `jenkins_stack_down_on_destroy = true` 才执行；永远不会 `down -v`。
- **绝不碰 `/data/jenkins-data` 这个目录本身**：它是 bind mount 的宿主机目录，里面是你的 Jenkins 全部数据。脚本对它**只读**，不创建、不清理、不递归删除。

### 脚本为什么是 Python 而不是 Shell

最初这五个脚本是 shell 写的，改写的原因是**可读性**。几处具体问题：

| shell 里的写法 | 问题 | Python 里的做法 |
|---|---|---|
| `tr '}' '\n' \| grep -F '"name":"x"' \| sed -n 's/.*"id":\([0-9]*\).*/\1/p'` | 手工解析 JSON，隐含「字段顺序固定、无嵌套、无转义」的假设 | `json.loads()` + `next(p for p in policies if p["name"] == name)` |
| `docker inspect --format "{{range \$p,\$b := ...}}"` | Go 模板一行流，引号和 `$` 层层转义 | `{{json .NetworkSettings.Ports}}` + `json.loads()` |
| `free -h \| awk "/^Mem:/{print \$2}"` 嵌在 HCL heredoc 里 | 要写成 `\\$2`，还要把 shell 的 `${x}` 双写成 `$${x}` | 取值与排版分开，`sh("free -h")` 之后用 Python 处理 |
| `[ "$A" = "x" ] && ok \|\| { warn; N=$((N+1)); }` | 计数、分支、`set -e` 交互容易出错 | `if/else` + 一个 `Audit` 类收问题 |

另外两个直接收益：

- **唯一的占位符**。旧版每个字段一个 `replace()`（渲染 compose 10 个、渲染脚本 7 个），还得小心占位符之间不构成前缀关系，否则短的会先把长的替换掉一半。现在每个脚本只有一个 `__WF_CONFIG_B64__`。
- **可以离线调试**。每个脚本都支持用环境变量直接喂配置，不需要碰真机器：

  ```bash
  WF_CONFIG_JSON='{"container":"jenkins","image":"jenkins/jenkins:2.555.3-lts",
    "http_port":8081,"agent_port":50000,"inner_http_port":8080,
    "inner_agent_port":50000,"home_dir":"/data/jenkins-data",
    "compose_dir":"/data/jenkins-data","restart_policy":"always",
    "mode":"verify","expected_compose":"","host_mounts":[]}' \
    python3 scripts/jenkins-compose.py
  ```

### 配置注入：为什么用 base64 包一层

`containers.tf` / `cicd.tf` / `gitlab.tf` 里都是这个模式：

```hcl
local.xxx_config = { ...整份配置... }

xxx_script = replace(
  file("${path.module}/scripts/xxx.py"),
  "__WF_CONFIG_B64__",
  base64encode(jsonencode(local.xxx_config)),
)
```

包 base64 的理由是**字符集**：base64 只产生 `[A-Za-z0-9+/=]`，不含引号、反斜杠、换行、`$`。所以：
- 不需要任何转义，密码/Token 里有什么字符都不影响；
- 不可能意外截断 Python 字符串字面量，也不可能被 HCL 当成模板插值。

代价是渲染后的脚本里那段配置人眼读不了 —— 需要排查时用上面的 `WF_CONFIG_JSON` 方式跑。

### compose 模板用 `templatefile()`，不用链式 `replace()`

`templates/jenkins-compose.yaml.tpl` 里直接写 `${image}`、`${http_port}`：

```hcl
expected_compose = templatefile("${path.module}/templates/jenkins-compose.yaml.tpl", {
  image = var.jenkins_image
  ...
})
```

比 `replace(replace(replace(...)))` 可读，而且**变量名写错会在 `plan` 阶段直接报错** —— `replace()` 写错只会静默不替换，留下一串 `__IMAGE__` 在渲染结果里。

> 一个坑：模板的**注释里**也不能随便写 `${...}`，`templatefile` 一样会去解析它。要写字面量得写成 `$${...}`。本项目的模板头部就踩过这个。

---

## vSphere 参考版本（`vmware-vsphere-reference/`）

> 阅读顺序上，前三节按「阶段一 阿里云 → 阶段二 本地盘点 → 阶段三 CI/CD → 阶段四 GitLab」
> 展开；本节属于**参考资料**，不占阶段序号。目录树里的 `①②③` 是**目录编号**，与阶段序号无关。

`vmware-vsphere-reference/main.tf` 展示了用官方 `hashicorp/vsphere` provider 的正确写法，包括 datacenter / cluster / datastore / network 数据源定位，`clone` + `customize` 定制主机名和固定 IP，以及 `guest_id`、`thin_provisioned` 等从模板继承的细节。

**它在本机跑不起来**（需要 vCenter 或开启 API 的 ESXi），而且**不在运行路径上** ——
它登记在 `boundaries.yml` 的豁免清单里，所以 B9 不会对它报错，
它也不会被当作「在用模块」要求配 state 后端（它就是一份对照材料，不该有 state）。

保留它是为了让你看清两件事：

1. 有官方 provider 时，资源能被**真正声明和追踪**——`vsphere_virtual_machine` 有完整 state，能 import 已存在虚机，能直接拿到 IP。这正是 Workstation 场景缺失的能力。
2. 这也解释了为什么本地部分要绕道 remote-exec：**不是不想用好方法，而是没有 provider 可用。**

> 如果以后你能用上 vCenter 或 ESXi，`vmware-workstation/` 里那些 SSH 脚本大部分可以退役，改成 provider 原生资源。这是值得期待的演进方向。

---

## 两个环境怎么配合使用

目前两边独立。后续想打通时，方向是：

| 打通方式 | 做法 |
|---|---|
| 网络层 | Workstation 用**桥接模式**，虚机就和宿主机同网段，能直接访问公网 ECS |
| 安全 | 阿里云安全组**只放行宿主机公网 IP**，不要开 `0.0.0.0/0` |
| 代码层 | 两边网段错开（阿里云 `10.10.0.0/16`，本地 `10.20.0.0/24`），避免将来做专线/VPN 时冲突 |
| 实用联动 | **本地流水线构建镜像 → 推 Harbor → 阿里云 ECS 拉取运行**。这条链路不需要打通网络，只要 Harbor 能被公网访问即可 |
| 进阶 | 阿里云侧用 Terraform 建 VPN 网关 + IPsec 连接，对接本地；或用 `remote state` 数据源让两边互相引用输出值 |

本地流水线（阶段三）已经跑通，下一步若想让两边联动，最直接的路径是：

1. 让 Harbor 能被公网访问（或走已有 VPN），并**启用 HTTPS**——镜像仓库走明文 HTTP 有凭据泄露风险
2. 阿里云 ECS 上配好 Harbor 的登录凭据，`docker login` 后拉取镜像
3. 把 ECS 上的部署也纳入流水线：本地 Jenkins 增加一个「部署到云」的 stage

这样整条链路就从「本地 CI/CD」升级成「本地构建 → 云端部署」，是这套环境最有价值的演进方向。

---

## 环境要求

- **Terraform** >= 1.5.0 —— [下载地址](https://developer.hashicorp.com/terraform/downloads)
- **阿里云账号**，且 AccessKey 有 VPC / ECS / 安全组权限
- **本地环境**：两台已装好 K3s 的虚机，且都能从本机 SSH 连通
  - 主节点：Docker + **Docker Compose（compose v2 插件）** + **python3** + Jenkins（compose 栈）+ Harbor（compose 栈）
  - 从节点：**python3** + GitLab（Omnibus 实体服务）+ K3s agent
- **Windows 用户**：本机需要 **Git Bash**（Terraform 的 `local-exec` 会调用 `bash`）。
  PowerShell 里直接跑会因为找不到 `bash` 而失败 —— 见下面「Windows 上必须用 Git Bash」。
- **Ansible** >= 2.14（仅 `ansible/` 需要）—— 而且**控制端必须是 Linux / macOS / WSL**，见下。

### Ansible 控制端：Windows 上跑不了（这是平台限制，不是配置问题）

`ansible/` 这一层的控制端**不能**是 Windows 原生 Python。原因是硬的：

```bash
python -c "import os; print(hasattr(os, 'fork'))"
# Windows 原生 Python → False
```

Ansible 用 `os.fork` 拉起 worker 进程，Windows 没有这个函数。这和"配置对不对"无关，
调 `ansible.cfg` 也解决不了。三种可行做法：

| 做法 | 说明 |
|---|---|
| WSL2（推荐） | `wsl --install` 后在 WSL 里 `pip install ansible`。★ 仓库最好拷到 WSL 原生文件系统（如 `~/`）再跑，原因见下 |
| Linux / macOS 机器 | 直接作为控制端；注意它对两台本地机器要有 SSH 直连 |
| 容器 | `docker run -it --rm -v "$PWD:/work" -w /work alpine/ansible ansible-playbook ...` |

> 被管的机器（云 + 本地）**不需要装 Ansible**，只需要 `python3`（Ansible 模块在远端要靠它跑）。
> 这一点和 Terraform 那部分的要求是一样的。

**WSL2 的一个坑：`/mnt/c` 是"世界可写目录"。** Windows 的 drvfs 挂载默认把所有文件呈现成 `777`，
而 Ansible 出于安全考虑**拒绝加载世界可写目录里的 `ansible.cfg`**（只给一行 WARNING，配置静默失效）。
`chmod o-w` 在 drvfs 上改不动。两种解法：

```bash
# 解法一（推荐，也是"放到 Linux 服务器上"的正确形态）：
#   把仓库拷到 WSL 原生 ext4 里跑（再补一句 chmod），ansible.cfg 才会被加载
#
#   ★ 拷完必须补一句 chmod —— 别省。
#     `rsync -a` 保留权限位，而源头的权限位就是 drvfs 给的 777，
#     所以刚拷过去的一整棵树仍然是 777，ext4 上照样报一模一样的 WARNING。
#     「换成 ext4 就好了」只做对了一半，真正让它生效的是那次 chmod。
rsync -a /mnt/c/path/to/terraform-demo/ ~/terraform-demo/
chmod -R go-w ~/terraform-demo          # ← 不能省，见踩坑 40

# 解法二：显式指定配置文件路径，绕过目录级判定
export ANSIBLE_CONFIG=/mnt/c/path/to/terraform-demo/ansible/ansible.cfg
```

审这一步有没有漏，只看一行就够：

```bash
ansible --version | grep "config file"
# 对了：config file = /home/<你>/terraform-demo/ansible/ansible.cfg
# 漏了：config file = None        ← 被拒载了，inventory / roles_path 全部静默失效
```

**本仓库的 `ansible/` 已经在一台真实 Linux 机器上完整跑过**（Ubuntu 26.04 / ansible-core 2.21.4，
用 WSL 主机同时扮演 cloud 组和 local 组的受管机），覆盖了只读验收、本地保护默认路径、
两次解锁门闩、云侧 Docker 安装与容器栈拉起、幂等性重跑、双组冲突拦截 —— 详见
[验证状态](#验证状态)。**边界检查器（含 B9）也已经在同一台 Linux 上单独复跑并做过破坏性验证**，
所以「检查器只在 Windows 上跑过」这个空白已经补上了。

仍未验证的只剩：真实的两台 192.168.1.x 存量机器、阿里云真机、以及 `docker compose` 版本差异。

> **两台远端机器都要有 `python3`**（3.8+ 即可）。核对脚本已从 shell 改成 Python，
> 通过 `ssh 'python3 -u -'` 从 stdin 执行，所以远端只需要解释器本身：
>
> ```bash
> # Ubuntu / Debian
> sudo apt-get install -y python3
> # RHEL / CentOS / Rocky
> sudo yum install -y python3
> ```
>
> 脚本**只用标准库**（`json` / `subprocess` / `urllib` / `xml.etree` / `difflib` / `hashlib`），
> 不需要 `pip install` 任何东西，也不需要虚拟环境。Terraform 会在执行前用
> `command -v python3` 预检，缺了会给出上面这条安装提示，而不是抛一堆 Python traceback。
>
> 本机（Windows）**不需要** 装 Python —— 本机仍然只用 Git Bash 跑那几段短的 bash 编排逻辑。

> 核对脚本优先用 `docker compose`（**v2 插件形式**，带空格），找不到时回退到 `docker-compose`
> （**v1 独立二进制**，带连字符）；两者都没有会直接报错中止。
> 建议用 v2 —— 脚本的语义比对依赖 `compose config` 的规范化输出，
> v1 的渲染结果与 v2 有细微差别，可能产生假差异。

### SSH 连通性要求

本地部分完全依赖 SSH，**两台机器都要确认**这些前置条件：

```bash
# 1. 能免密登录（Terraform 无法处理交互式密码输入）
ssh -i ~/.ssh/id_ed25519 ubuntu@192.168.1.101     # 主节点
ssh -i ~/.ssh/id_ed25519 ubuntu@192.168.1.102     # 从节点（GitLab 在这台）

# 2. 主节点：目标用户能执行 docker，无需 sudo
ssh ubuntu@192.168.1.101 'docker ps'
# 若报 permission denied，把用户加入 docker 组：
#   sudo usermod -aG docker $USER && newgrp docker

# 3. 从节点：能读到 GitLab 服务状态（盘点要用）
ssh ubuntu@192.168.1.102 'sudo gitlab-ctl status'

# 4. 从节点上 GitLab 的 API 要能被本机访问（用于项目与 webhook 管理）
curl -s http://192.168.1.102/api/v4/version

# 5. 两台都要有 python3（核对脚本是 Python 写的，跑在远端）
ssh ubuntu@192.168.1.101 'python3 -V'
ssh ubuntu@192.168.1.102 'python3 -V'
```

代码里用了 `BatchMode=yes`（禁止交互），所以免密登录是硬要求。如果卡在密码提示，说明密钥没配好。

> ⚠️ 两台机器的 SSH 私钥如果不同，注意在 `terraform.tfvars` 里分别填 `k3s_server.ssh_key` 和 `k3s_agent.ssh_key`。

### Windows 上必须用 Git Bash

`vmware-workstation/` 里所有 provisioner 都是 `local-exec` + `interpreter = ["bash", "-c"]`，
在**本机**执行一段 bash：这段 bash 负责拼 `ssh` 命令、把脚本正文从 stdin 送进去
（`ssh ... 'python3 -u -'`，核对脚本在远端跑，解释器是远端的 `python3`）。
所以本机 PATH 里必须有一个可用的 `bash` —— 注意**本机并不需要 Python**。

> `-u` 不能省。它让 Python 的输出不缓冲，否则远端会攒够一整块才吐出来，
> 看起来像"卡住不动"，实际是在跑。脚本内部也做了 `sys.stdout.reconfigure(...)`，
> 防止远端 locale 是 `POSIX`/`C` 时中文输出直接报 `UnicodeEncodeError` 中断盘点。

Windows 上有两个坑：

1. **PowerShell / CMD 里直接跑会失败** —— 报 `exec: "bash": executable file not found`，
   因为默认 PATH 里没有 `bash`。请用 **Git Bash** 打开终端再跑 `terraform`。
2. **PATH 里的 `bash` 可能指向 WSL 的 `bash.exe`** —— 那会在 Windows 路径（`C:\...`）
   和 Linux 路径（`/mnt/c/...`）之间来回转换，`ssh` 也会被换成 WSL 里的版本，
   症状是密钥路径找不到、或 `terraform.rc` 路径失效。

   判断方法：`which bash`，看结果是不是 `<Git安装目录>\bin\bash.exe`。
   如果看到 `WindowsApps\bash.exe` 或 `System32\bash.exe`，那是 WSL，需要调整 PATH
   让 Git Bash 的 `bin` 目录排在前面。

> 这条不是"可选优化"。不满足它，`apply` 会在第一个 `local-exec` 步骤直接失败，
> 而且报错信息指向 `exec` 层，不容易联想到是 shell 的问题。

### 国内网络：provider 下载超时怎么办

直连 `registry.terraform.io` 会去 GitHub Releases 拉 provider，国内经常超时，报错类似：

```
Error: Failed to install provider
could not query provider registry for registry.terraform.io/aliyun/alicloud
```

本项目提供了一份镜像配置 `terraform.rc.example`。启用方式：

```powershell
# Windows PowerShell
Copy-Item terraform.rc.example terraform.rc
$env:TF_CLI_CONFIG_FILE = (Resolve-Path .\terraform.rc).Path
```

```bash
# Linux / macOS
cp terraform.rc.example terraform.rc
export TF_CLI_CONFIG_FILE="$(pwd)/terraform.rc"
```

它让 `aliyun/alicloud` 走阿里云镜像，其他 provider 仍走官方源，不影响安全性。

> 本地 `vmware-workstation/` 只用 `null` provider，体积小，一般无需镜像。

---

## 验证状态

本项目已用 Terraform 1.9.8 实际跑过校验，不是"看起来能跑"：

| 目录 | `fmt` | `validate` | 说明 |
|---|---|---|---|
| `aliyun-ecs/` | 通过 | **通过** | 已用阿里云镜像成功下载 provider 并通过校验 |
| `vmware-workstation/` | 通过 | **通过** | null provider 初始化成功；5 个 Python 脚本 + 1 个 compose 模板全部通过 `py_compile` / `fmt` / 渲染检查 |
| `vmware-vsphere-reference/` | 通过 | 未跑 | 需要 vCenter 才能 init，仅作语法参考 |
| `ansible/` | 不适用 | 不适用 | 不是 Terraform 目录。改用 `check_guard.py` + `selftest.py` 校验，见下 |
| `tools/` | 不适用 | 不适用 | 不是 Terraform 目录。改用 `check_boundaries.py` 校验，见下 |

另外做了两项**行为验证**，不只是语法检查：

| 验证项 | 做法 | 结果 |
|---|---|---|
| 只读模式不误伤 | `terraform plan`（全部开关为 false） | `7 to add, 0 to change, 0 to destroy`，只有盘点与提示类资源 |
| 三道安全防护 | 用 `-var` 故意传错配置 | 全部在 plan 阶段被拦下，附中文原因说明 |
| **端口一致性护栏** | `-var webhook_endpoint_url=…:8080/…`（故意写错端口） | ✓ 报「Harbor webhook 端口是 8080，而 Jenkins 实际在 8081 —— 回调会被直接拒绝」 |
| 同上，改成正确端口 | `-var webhook_endpoint_url=…:8081/…` | ✓ 报「一致（8081）」 |
| **核对脚本三分支行为** | 用 mock `docker` 模拟三种现场 | ✓ 「完全一致 / 端口漂移 / 非 compose 容器」三种分支都按设计输出（15/15 断言通过） |
| **四个 API 脚本的纯函数逻辑** | mock HTTP 响应，只测判定逻辑不碰真服务 | ✓ 4 种 Harbor base URL 归一化、3 种 webhook 漂移、6 种 Jenkins Job XML、7 种 GitLab 项目/白名单、3 种盘点分派与降级 —— 全部通过 |
| **渲染产物本身能跑** | 从 `terraform console` 取 `local.*_script`，剥掉 `<<EOT` 包装后直接用系统 Python 执行 | ✓ 配置解码正确；`expected_compose` 与手写 compose 文件**逐字节一致**（含注释） |

### `ansible/` 的验证状态

这一层的验证分两段：**静态 + 离线**（不需要任何机器），以及**真机验证**
（在一台 Ubuntu 26.04 / ansible-core 2.21.4 的 Linux 机器上完整跑过五幕，
用 WSL 主机同时扮演 `cloud` 组和 `local` 组的受管机）。

#### 第一段：静态 + 离线（103 项断言 + 13 个校验器自证用例，全部通过）

| 验证项 | 做法 | 结果 |
|---|---|---|
| **保护机制真的拦得住** | `tools/check_guard.py --selftest`：13 个用例，5 个"应拦下"、5 个"应放行"（含 P8 的 3 个位置用例）、3 个 role 写入门 | ✓ 该拦的拦下了，该放行的放行了 |
| ↑ **反向验证** | 故意把幕 C 的 `hosts: cloud` 改成 `hosts: all`；另一轮去掉幕 E 的解锁门闩 | ✓ 两次都被抓到，且给出了正确原因（"变更型 play 的 hosts 可能包含本地服务器"）；改回后重新确认通过 |
| **保护公式的真值表** | `tools/selftest.py` 从 `roles/guard/tasks/main.yml` **读出公式本身**求值（不是重抄一份） | ✓ 11 个场景 + **穷举全部 16 种开关组合**，断言"可覆盖 ⟹ 变更已解锁"始终成立 |
| ↑ **反向验证** | 把 `wf_can_write` 临时改回"只认覆盖开关"的松散写法 | ✓ 穷举立刻报错并列出反例（`在local=True 变更=False 覆盖=True`）；改回后重新确认通过 |
| **所有模板都能渲染成合法结构** | 体检报告 / `daemon.json` / compose / index.html / sysctl / limits | ✓ JSON 与 YAML 全部解析通过；含各档降级分支（无 docker、无 systemd、无 ss） |
| **报告的换行结构** | 逐行断言（磁盘/工具/端口/服务每项一行、分隔线独立成行） | ✓ 子串断言对"换行丢失"是盲的，必须按行验 |
| **playbook 里的 shell 串过得了 Jinja** | 抽出所有 `shell` / `command` / `script` body 逐个 `env.parse()`（13 项断言） | ✓ 包括反向验证（字面 `{{ }}` 必须报错）和假阳性守卫（单花括号、`{{ var }}` 不误报） |
| **路径过滤器（禁区判定）** | 9 个用例，含 `-OLD` 字符串前缀陷阱、结尾多余斜杠、非绝对路径 | ✓ 全部符合预期；并用真实配置复核 `baseline` 的 5 个写入目标都避开了禁区 |
| **云清单生成器** | `summary` 正常路径 / 退化为 `instance_public_ips` / 空输出 | ✓ 三种输入都产出合法 YAML，且**不会生成 `local` 组** |
| **22 个 YAML 文件全部可解析** | `selftest.py` 第 1 项 | ✓ |

#### 第二段：真机验证（9 轮运行，5 个验收性质全部成立）

控制端：WSL2 Ubuntu 26.04 + ansible-core 2.21.4（仓库放在原生 ext4 上，`ansible.cfg` 自动加载）。
受管机就是同一台机器，通过临时清单分别放进 `local` 组（`ansible_connection: local` + sudo）和 `cloud` 组。

| # | 验证项 | 命令 | 结果 |
|---|---|---|---|
| 1 | **只读验收** | `verify.yml` | ✓ `ok=29~30 changed=0 failed=0` —— "changed 必须为 0"成立；`baseline` 正确检出差异（缺 jq、缺 `/opt/wf` 两个目录）但拒绝动手 |
| 2 | **默认路径（不解锁）** | `site.yml` | ✓ A/B 幕只读执行；E 幕 `meta: end_play` 生效，一个任务都没进 |
| 3 | **解锁干跑** | `site.yml -e local_mutation_allowed=true --check --diff` | ✓ `changed` 全部是补缺（装 jq、建目录）；**时区/sysctl/limits/时间同步全部 skipping** —— 只开第一个开关确实不会覆盖任何已存在的东西 |
| 4 | **解锁真跑** | 同上（去掉 `--check`） | ✓ jq 装上（1.8.1）、两个目录建成、`/etc/localtime` 原封未动、防火墙零改动 |
| 5 | **幂等性（本地）** | 重跑 `verify.yml` | ✓ `changed=0`，补缺之后验收性质依然满足 |
| 6 | **云侧真跑** | 临时清单入 `cloud` 组 + 传 `wf_docker_registry_mirrors` | ✓ Docker（docker.io 29.1.3 + compose v2 2.40.3）装上、`daemon.json`（含 mirrors 分支）写入、**nginx 容器在 8080 端口跑起来**，`curl` 拿到渲染的首页 |
| 7 | **云侧幂等（修 bug 后）** | 连跑两次 `site.yml` | ✓ 第二次 `changed=0`，容器 healthy。第一次跑抓到了一个真 bug：首页嵌了渲染时间 → 每次都"变了" → 容器被无谓重启，见踩坑 28 |
| 8 | **双组冲突拦截** | 同一台机器同时放进 `cloud` 和 `local` | ✓ `guard` 的断言当场 fatal，报「一台机器不能两者都是」并指出该保留哪个组 |
| 9 | **静态工具在 Linux 上复跑** | `check_guard.py` / `--selftest` / `selftest.py` | ✓ 8 类不变量、13 个用例、103 项断言在真实 Linux + Python 3.14 环境下同样全部通过 |

**2026-09-30 复跑了一遍**（换了台 WSL 实例，确认上一轮结论不是那台机器的偶然）：

| # | 验证项 | 结果 |
|---|---|---|
| 10 | 同步后 `ansible.cfg` 是否被加载 | ✓ 先踩到了 `config file = None`，原因是 `rsync -a` 把 drvfs 的 777 一并搬了过来 —— 补 `chmod -R go-w` 后恢复，见踩坑 40 |
| 11 | 第 1/2/5 轮 | ✓ `verify.yml` `ok=29 changed=0`；`site.yml` 默认路径 E 幕门闩生效、零变更；补缺后重跑仍 `changed=0` |
| 12 | 「只补缺」决定性问题 | ✓ 用 `-e` 注入一个**缺失的目录**制造差异：`changed=1`（补缺动手了），同时时区 / sysctl / limits 的 md5 前后完全一致（覆盖类一个没动），跑完把探针目录删掉 |
| 13 | 云侧重构建 + `now()` 回归 | ✓ 删掉 role 管理的 `index.html` → 重建（`changed=2`，重建后 md5 与删之前一致）→ 再跑 `changed=0`：模板确实是「事实的函数」而不是「时间的函数」 |

**明确没有验证的部分**（写成"已验证"会是假的）：

- 真实的两台 192.168.1.x 存量机器（K3s / Jenkins / Harbor / GitLab 在它们身上）——
  它们不在这台 WSL 的网络可达范围内
- 阿里云真机（`gen_cloud_inventory.py` 生成的清单没喂过真实 `terraform output`）
- `docker compose` 版本差异导致的行为差别（真机上是 2.40.3，只验了这一个版本）

↑ 曾经还有一条「B9 与豁免清单只在 Windows 上跑过」，**这一条已经补上**：2026-09-30 在
同一台 Linux（Ubuntu 26.04 / Python 3.14.4）上把边界检查器完整复跑了一遍 ——
65 个自证用例、142 个文件的全量扫描、以及 8 个破坏性场景（含改名脱离豁免那条），
结果与 Windows 侧**逐项一致**（同名场景命中数 1 / 1 / 1 / 8 / 1 / 29 / 0+提示）。
运行前先核对过两侧文件指纹：`boundaries.yml`、`check_boundaries.py`、`README.md`、
`.gitlab-ci.yml`、`ansible.cfg` 五个文件的 sha1 完全相同，所以「在 Linux 上验过」这句话
说的是同一份代码，不是改过的副本。

### `tools/` 分层边界的验证状态

边界检查器 `check_boundaries.py` 的验证方式是「静态 + 自证 + 对真实仓库做破坏性验证」，
共 **65 个自证用例**，全部通过。它**只用标准库**
（`argparse` / `pathlib` / `re` / `shutil` / `tempfile`），不依赖 yaml，也不依赖 Ansible。

| 验证项 | 做法 | 结果 |
|---|---|---|
| **该有反应的都有反应** | `--selftest`：42 个「应该有反应」用例（命中越界，或按设计给出提示） | ✓ 每一条都被对应规则抓到 |
| **该放的放得开** | `--selftest`：23 个「应当放行」用例 | ✓ 见下 |
| ↑ 放行类里最关键的几个 | 注释里的 `ansible-playbook` / `kubectl` / `tfstate`；`echo` 提示文本里的 `apt-get install`；Python 文档字符串里的 `systemctl`；`user_data` 里的 `dnf install`；`kubernetes_namespace` / `kubernetes_nodes`；一份**规范的流水线** | ✓ 全部不误报 —— **误报率决定这类工具能不能活下去** |
| **反向验证（跨工具）** | `subprocess.run(['terraform','apply'])` vs `terraform output -json` | ✓ 前者命中 B2，后者放行 |
| **反向验证（B7 的六条子规则）** | 从一份规范流水线出发，**每次只改一处**：拿掉 `when: manual` / 去掉 `--limit` / `--limit` 换成 `local` / 给自动 job 带上解锁开关 / 把 TF 与 Ansible 塞进同一个 job / 加一个 `deploy.sh` | ✓ 每一条都各自命中，改一处只响一处 |
| **反向验证（B8 的六条子规则）** | 未登记 / `mode: write` / `to: ansible` / 缺 `why` / 引号未闭合 / 顶层多一个键 | ✓ 同上；且解析失败**只报一次**（不叠加一条假的「没写 why」） |
| **反向验证（B9 的三类痕迹 + 豁免机制）** | 三类痕迹各来一遍：`resource "vsphere_virtual_machine"` / `provider "vsphere"` 与 `source = "hashicorp/vsphere"` / `.vmx` 路径 / `vmrun` 调用（Terraform、Python、Ansible、shell 各一种）；放行类：`error_message = "找不到 vmrun.exe"`、`vmrun_path` 变量名、注释与 `.md` 文档。豁免机制正反各一遍：同样的违规代码，登记过豁免就放行、没登记就报 | ✓ 12 个命中/提示、7 个放行全部符合预期；豁免那组是**唯一变量**对照（同文件同内容，只在清单里加一行），所以响了就一定是豁免机制在起作用 |
| **B8 / B9 不重复报账** | 构造一个「顶层多了个键 + exemptions 段」的文件，只看 B9 | ✓ B9 报 0 —— 顶层结构错归属 B8。**同一条错误被两条规则各报一遍**是这次重构差点留下的坑：解析是全文一次做完的，不给错误标上归属就会出现「一个毛病、两条报告」 |
| **子模块不被误伤** | 构造 `module "child" { source = "./child" }` | ✓ 只提示父目录，不提示 `child` —— 给子模块建议「加 backend」是**有害**建议，Terraform 明确禁止 |
| **真实仓库** | 对全仓库跑（142 个文件） | ✓ B1/B2/B3/B5/B6/B7/B8/B9 通过；B4 报 2 条提示（两个**在用**模块尚未启用远程后端，与事实一致）；`vmware-vsphere-reference` 不再被当作在用模块 —— 它登记在豁免清单里 |
| **真实仓库上的破坏性验证** | 见下表 —— 证明它在这个仓库里真的会响，不只是合成样例里会响 | ✓ 13 个场景，每个都按预期命中，跑完自动还原并**逐字节核对**过 |
| **CI 严格模式** | `--require-remote-state` / `--require-ci` | ✓ 退出码 1，把「没有远程后端」「没有流水线定义」从提示升级为失败；同一状态下不加开关则退出码 0 + 提示 |
| **backend 模板真的能被解析** | 把 `backend.tf.example` 拷成 `backend.tf`，跑 `terraform fmt -check` 与 `terraform init` | ✓ 格式已是 `terraform fmt` 的不动点（用户拷完不会立刻格式失败）；`init` 正确解析出 bucket / region / prefix，构造出 `https://<bucket>.oss-cn-hangzhou.aliyuncs.com/?prefix=terraform-demo/aliyun-ecs/` —— 失败原因只是占位桶不存在，**不是语法错误** |
| **两个新 YAML 文件本身合法** | 用 **PyYAML** 独立校验（不经过我自己的校验器） | ✓ `.gitlab-ci.yml` 认出 6 个 stage、8 个 job；`boundaries.yml` 认出 1 条衔接声明 + 2 条豁免登记 |

对真实仓库做的破坏性验证（每次只改一处，跑完立刻还原）：

| 场景 | 改了什么 | 期望 | 实际 |
|---|---|---|---|
| S0 | 什么都没改 | B7 ✓ / B8 ✓ | ✓ 通过 |
| S1 | 拿掉 `apply` job 的 `when: manual` | B7 失败，原因指向「变更动作没有人工放行」 | ✓ 1 个问题 |
| S2 | 在**已声明**的文件里插一句 `terraform apply` | 该由 **B2** 拦（B8 只管登记，这个文件已登记） | ✓ B2 报 1 个问题，B8 通过 —— 两条规则的**分工**被验证到了 |
| S2b | 新加一个没登记的文件去读 `terraform output` | B8 失败（漏登记） | ✓ 1 个问题，且指向具体行号 |
| S3 | 把声明里的 `where` 改成不存在的路径 | 漏登记（失败）+ 声明腐烂（提示）**同时**出现 | ✓ 1 失败 + 1 提示，两条都指得准 |
| S4 | 把声明改成 `mode: write` | 声明被拒 → 该衔接等于没登记 | ✓ 2 个问题（声明本身 + 未登记），逻辑正确 |
| S5 | 流水线定义整个不在 | 严格模式失败、退出码 1；非严格模式只提示、退出码 0 | ✓ 两种模式都符合预期 |
| S6 | 在 `aliyun-ecs/` 里声明一个 `vsphere_virtual_machine` | B9 失败 | ✓ 1 个问题，指向新加的文件 |
| S7 | 在 Ansible playbook 里加一条 `command: vmrun -T ws list` | B9 失败 | ✓ 1 个问题 |
| S8 | 在 `tools/` 加一个 `subprocess.run(['vmrun', ...])` 的脚本 | B9 失败 | ✓ 1 个问题 |
| S9 | 把 `vmware-vsphere-reference` 从豁免清单里删掉 | B9 失败 —— 证明**豁免真的在起作用** | ✓ 8 个问题（`main.tf` 里的 provider、数据源、资源全被指了出来） |
| S10 | 删掉某条豁免的 `why` | B9 失败 —— 豁免必须写得出理由 | ✓ 1 个问题，指向 `boundaries.yml` 的行号 |
| S11 | 把 `archive-vm-clone/` 改名为 `old-vm-clone/`（脱离豁免前缀） | B9 失败 —— **改名后立刻不再豁免**，证明判据是清单而不是目录名 | ✓ 29 个问题 |
| S12 | 在豁免清单里登记一个不存在的路径 | B9 提示（豁免腐烂） | ✓ 1 条提示 |

> S11 是这一组里最有价值的一个：它把「显式豁免」和「按名字猜」的差别**变成了可观测的**。
> 旧实现是「名字以 archive 开头就跳过」，这个场景会**静默通过** ——
> 而静默通过意味着：只要把违规代码放进一个叫 `archive-` 的目录，就永远查不到了。

> S2 这个场景值得单独说一句：它验证的是**规则之间没有互相顶替**。
> 「在已声明的文件里写 `terraform apply`」不应该由 B8 报 ——
> B8 管的是「有没有登记」，B2 管的是「登记的行为是不是只读」。各管各的，
> 两条规则才不会在将来某次修改里悄悄变成一条。

**校验器自己的缺陷**：第八轮自证抓出 4 个（不是样例写错，是检查器本身的漏洞，详见踩坑 30）；
第九轮在设计阶段就砍掉了一个 —— 一个**永远不会响的检查**（成环检测），
理由见踩坑 34：它比没有检查更糟，因为它会让人以为查过了。

**明确没有验证的部分**：

- ~~检查器只在 Windows 的 Python 3.13 / 3.15 上跑过~~ —— **2026-09-30 已补上**。
  同一份代码（五个关键文件 sha1 两侧一致）在一台真实 Linux（Ubuntu 26.04 / Python 3.14.4）
  上复跑：65 个自证用例全过、142 个文件全量扫描九条边界全绿、上表 S6–S12 那 8 个破坏性场景
  再走一遍，命中数与 Windows 侧**逐项相同**（1 / 1 / 1 / 8 / 1 / 29 / 0+提示），
  每个场景还原后还做了逐字节核对
- 边界检查是**静态的**：它证明「代码里没有越界的写法」，不证明「运行时各工具真的没抢对象」
- `.gitlab-ci.yml` 只做到了「YAML 合法 + 通过 B7 的结构检查」，
  **没有在真的 GitLab 上跑过一次** —— runner 的 tag、CI 变量、artifact 传递
  这几处的实际行为都还没验证过
- `terraform init` 那个 artifact 路径（`aliyun-ecs/tfplan`）依赖 `needs: artifacts` 的下载行为，
  同样只做了静态检查

这几项的验证方式值得说明一下，因为它们**不依赖任何真实机器**。每个 Python 脚本都支持两种取配置的方式：

```python
# 1) 正常路径：读脚本里注入的 base64 占位符
# 2) 调试路径：WF_CONFIG_JSON 环境变量直接覆盖
load_config()  # 内部先看 WF_CONFIG_JSON，没有再解 base64
```

所以「远端没有 docker」「远端没有 python3」「compose 文件不存在」这些分支，
都能在本机用假数据和 `subprocess` mock 跑出来，不必真的去动一台机器。这是改写成 Python
之后才有的能力 —— shell 版本的唯一测试方式就是找一台机器上去跑。

具体是哪三道防护被测过：

| 故意传错的配置 | 期望行为 | 实际结果 |
|---|---|---|
| `manage_gitlab_cicd=true` 但 token 为空 | 拦住 | ✓ 报「必须设置 gitlab_api_token」 |
| webhook 地址写成 `http://localhost:8080/x` | 拦住 | ✓ 报「不能是 localhost」并解释两条原因 |
| webhook 地址缺协议前缀 | 拦住 | ✓ 报「必须以 http(s):// 开头」（白名单解析依赖它） |

这些和 `PULL_ARTIFACT` 那条是同一类思路：**把「配错了也不报错、事后极难排查」的隐患，提前变成 apply 阶段的硬错误。**

「端口一致性护栏」值得单独说一句。它不是在验证变量的格式，而是在验证**两个不同来源的端口是同一个**：`jenkins_http_port`（渲染 compose 用）和 `webhook_endpoint_url` 里那个端口（Harbor 回调我们时用的）。这两个值分别写在两处，一旦不一致，症状是「构建成功、推镜像成功、Harbor 也触发了，但 Jenkins 从没收到过回调」——查起来极其费劲。所以在 `outputs.tf` 里加了一段正则提取 + 比对，让它在 plan 阶段就炸。

更一般的教训是：**凡是「同一个事实在两个地方各写一遍」的配置，都该加一致性检查。** 8081 这个端口就同时出现在 compose 渲染、webhook 地址、访问信息输出三处。

校验过程中实际修掉的问题，都是容易踩的坑：

**第一轮（阿里云部分）**

1. **`filetimestamp()` 这个函数不存在** —— Terraform 没有"取文件修改时间"的函数。改用 `filesha1()` 取内容哈希，语义反而更准（内容真变了才重建）。
2. **`user_data_replace_on_change` 是 AWS 专有参数**，阿里云 provider 不认。改用 `lifecycle.ignore_changes`。
3. **输出引用敏感变量必须显式标注** `sensitive = true`，否则 `validate` 直接报错。

**第二轮（本地容器部分）**

4. **destroy provisioner 不能引用 `var.*`** —— Terraform 硬性规则：销毁阶段的 provisioner 只能引用 `self` / `count.index` / `each.key`。原本在清理脚本里写了 `${var.ssh_port}`，直接编译失败。解决办法是把 `ssh_port`、`ssh_key`、`ssh_user`、`down_on_destroy` 全部预先放进 `triggers`，销毁时从 `self.triggers.*` 读。

   这个限制比想象中更硬：**连 `interpreter` 参数里想放一个变量路径都不行。** 我试过 `interpreter = [var.bash_path, "-c"]`，同样报 `Invalid reference from destroy provisioner`。所以"用变量指定 bash 路径"这条路只解决了一半问题，干脆放弃。
5. **`set -e` 与 `[ ] && { }` 的交互** —— 已实测确认：值相等时 `[ ]` 返回非零，但因为是 `&&` 左侧，不会触发 `set -e` 退出，`NEED_RECREATE` 正确保持 `false`。幂等路径是通的。

**第三轮（CI/CD 部分）**

6. **同一个 destroy 陷阱又踩了一次** —— `cicd.tf` 的销毁脚本引用了 `self.triggers.cleanup` 和 `self.triggers.harbor_pass`，但这两个 key 忘了放进 `triggers`。已修正。

   这个坑值得单独记住：**写 destroy provisioner 时，先把用到的 `self.triggers.*` 全部列出来，再回头逐个确认它们在 triggers 里有定义。** 顺序反过来（先写脚本再想 triggers）必然漏。

7. **渲染占位符不能用 `sed`，要用 `replace()`** —— `sed` 的替换串需要转义 `\`、`&`、`|`。Harbor 密码和 Jenkins Token 里完全可能出现这些字符，一旦出现就会**渲染错**，而且错得很隐蔽：脚本照常执行，只是密码是错的，报错信息指向认证失败，很难联想到是渲染问题。

   Terraform 的 `replace()` 是纯字面量替换，对任意字符（包括引号、斜杠、换行）都安全。代价是写法啰嗦一点，但值得。

   > **后来这个方案又被替换掉了。** `replace()` 本身没错，但"每个字段一个占位符"很难维护：
   > 渲染 compose 要 10 个、渲染脚本要 7 个，还必须保证占位符之间不构成前缀关系，
   > 否则短的会先把长的替换掉一半。现在改成**整份配置 `jsonencode` 后再 `base64encode`**，
   > 每个脚本只剩一个 `__WF_CONFIG_B64__`（详见上文「配置注入：为什么用 base64 包一层」）。
   > 教训是：`replace()` 只解决了「字符安全」，没解决「占位符数量爆炸」。

**第四轮（GitLab 部分）**

8. **本地值（local）在整个模块内必须唯一** —— 我在 `containers.tf` 里新加了一个 `jenkins_script`，而 `cicd.tf` 里早有一个同名 local（那是 Jenkins「任务」的脚本）。`validate` 直接报 `Duplicate local value definition`。改名为 `jenkins_container_script`，后来整个文件重写后是 `jenkins_compose_script`。

   ⚠️ 顺带发现一个**更隐蔽的同名问题**：同一个 `triggers = {}` 对象里重复写同一个 key，Terraform 会报错，但如果你把 GitLab 的「容器 shell 端口」和「宿主机 SSH 端口」都命名成 `ssh_port`，那就真的会在覆盖后悄悄失效——**这次是 validate 帮我抓住了**。涉及两台机器时，命名字段一定要带上下文。

9. **HCL 里三元表达式换行必须加括号** —— 写 `x = cond` 换行再写 `? a : b` 会直接编译失败，报 `Invalid expression`。

   原因：HCL 遇到换行就认为一个完整表达式结束了，下一行开头的 `?` 成了非法 token。**必须写成 `x = (cond ? a : b)` 或把整个条件用括号包起来。** 这是很容易反复踩的一条，已在代码注释里标注。

10. **`set -u` 下的空数组展开** —— 用条件拼 `docker run` 参数时，`"${ARR[@]}"` 在旧版 bash 下遇到空数组会报 `unbound variable`。改用 `${ARR[@]+"${ARR[@]}"}` 这种写法，两种版本都安全。

**第五轮（拿到真实 compose 文件之后）**

这一轮的问题**全部是"代码没错、默认值错了"**——最危险的一类。语法检查、`-validate`、甚至 `plan` 都是绿的，只有真跑到机器上才会出事。

11. **`host_port_of` 的参数语义搞反了** —— 这个函数用 `docker inspect` 取容器的宿主机映射端口，它的参数必须是**容器内端口**（`8080`），我却传了**宿主机端口**（`8081`）。

    为什么之前一直没暴露：最初的默认值里宿主机和容器端口**都是 8080**，两者相等，传哪个都对。改成真实的 `"8081:8080"` 之后，函数拿 `8081` 去匹配容器端口 `8080`，永远匹配不到，返回空——于是脚本会一直认为"端口漂移了"，每次 apply 都触发重建。

    修正后调用是 `host_port_of 8080` 和 `host_port_of 50000`，并在函数上加了一行注释写明参数是容器端口。**教训：当两个值的语义容易混淆时，函数名或注释里必须点明单位/视角，否则默认值一旦变化就会静默出错。**

12. **HCL 模板插值和 shell 参数展开的字面冲突** —— 脚本里写 `${CFGFILE:-（标签为空）}` 和 `${CONFIG_HASH:0:12}`，`validate` 直接报 `Template interpolation doesn't expect a colon at this location`。

    因为这两个脚本内容最终是**嵌在 HCL 里**的（经 `file()` 读进来仍需经过模板舞台），Terraform 看到 `${` 就按自己的语法去解析，而 `:` 在 HCL 插值里非法。shell 想表达的"取默认值"和"取子串"，Terraform 完全不认。

    修法不是加转义（越转越乱），而是**换成不依赖 shell 参数展开的写法**：

    ```bash
    # 改前（HCL 会报错）
    CFGFILE="${CFGFILE:-（标签为空）}"
    echo "${CONFIG_HASH:0:12}"

    # 改后
    if [ -z "$CFGFILE" ]; then CFGFILE="（标签为空）"; fi
    printf '%.12s' "$CONFIG_HASH"
    ```

    **规律：写在 Terraform 文件里的 shell 片段，尽量只用 `$VAR` / `"$VAR"` 这种最简单的形式；凡是 `${...}` 带冒号的复杂展开（默认值、子串、大小写转换），一律换成等价的外部命令或 if 判断。** 同理，`${VAR}` 光秃秃的花括号形式也没必要，写 `$VAR` 即可。

13. **旧的 `docker run` 方案会和 compose "抢对象"** —— 这不是编译错误，是设计错误，但危害最大。原方案要 `docker run` 一个名为 `jenkins` 的容器；而机器上那个 `jenkins` 是 compose 管的、占着 `8081` 和 `50000`、并挂了 6 个宿主资源路径。

    真跑下去的结果有且只有两种：要么因端口冲突起不来，要么侥幸起来但**那些挂载全丢**（`docker.sock`、`k3s`、`kubectl`、`rancher/k3s`、`.kube`、`/usr/bin/docker`），后者更糟——Jenkins 里的 k8s 部署 stage 会在某个深夜突然失败。

    修正方式是把「创建容器」整体换成「核对编排栈」，并把旧脚本 `scripts/jenkins.sh` **删除**，换成 `scripts/jenkins-compose.sh`（后来改写成 `scripts/jenkins-compose.py`）。**同一份声明式配置（compose 文件）不应该被两个工具同时声明。**

**第六轮（shell 改写成 Python）**

这一轮不是"修 bug"，是主动重构。改写过程中又抓出 3 个真问题，其中 1 个是**一直藏在 shell 版本里的真实缺陷**。

14. **`harbor_url` 少了 `/api/v2.0`，而且连通性检查是假的** —— 这是**顺带抓出来的老 bug，不是重写引入的**。

    `harbor_url` 默认值是 `http://localhost`（没有路径段）。旧 shell 里拼地址是 `"${API}/systeminfo"`，于是请求打到了 **Harbor 的前端页面**而不是 API 上，返回 404。而那段连通性检查用的是 `curl` 的**退出码**、又没加 `-f`，HTTP 404 的退出码照样是 0 —— 所以「检查通过」了，脚本继续往下走，随后在别的地方以 401/404 的形式失败。

    Python 版加了显式归一化：

    ```python
    def normalize_base(url: str) -> str:
        base = url.rstrip("/")
        if not base.endswith("/api/v2.0"):
            base = f"{base}/api/v2.0"
        return base
    ```

    **教训：判断"这个 HTTP 接口能用吗"要看状态码，不能看进程退出码。** 而且一个值如果可以被写成两种形式（带不带 `/api/v2.0`），就得在入口处归一化一次，而不是在每个拼接点各猜一次。

15. **`templatefile()` 会解析注释里的 `${...}`** —— 给 compose 模板写的头部注释里正好出现了 `${name}`（想说明"这个文件叫什么"），`validate` 直接报：

    ```
    templates/jenkins-compose.yaml.tpl:11,8-11: Invalid expression;
    Expected the start of an expression, but found an invalid expression token.
    ```

    模板引擎不区分"注释"和"正文"，只看 `${`。**模板里任何字面量 `${...}`（包括注释、文档字符串、shell 的 `${VAR}`）都必须写成 `$${...}`。** 这条和第五轮第 12 条是同一类坑的两个面：那边是 shell 片段嵌进 HCL，这边是任意文本进模板 —— 触发条件都是"文本里出现了 `${`"。

16. **把已 `jsonencode` 的字符串当对象嵌套 → 会发出错误的请求体** —— 这条最隐蔽，**编译期完全静默**。

    `gitlab.tf` 里原本有 `gl_project_payload = jsonencode({...})`，它是个**字符串**。重构时我把配置收拢进一个对象，于是它成了对象里的一项：

    ```hcl
    # ✗ 错的：Python 端 json.loads 拿到的是字符串，不是对象
    gitlab_project_config = {
      project_payload = local.gl_project_payload   # 这是 jsonencode 过的字符串
    }
    ```

    `jsonencode(gitlab_project_config)` 对一个字符串值会**再编码一层**（变成 JSON 字符串里的 JSON），`fmt` 和 `validate` 都不会报。Python 端 `json.loads` 得到 `str`，然后 `json.dumps` 发出去，GitLab 收到的是一个被双引号包住的一整串文本 —— 报 400，且错误信息完全看不出是编码层级问题。

    修法是**让嵌套的值保持为 HCL 对象，只在需要哈希的地方单独 `jsonencode`**：

    ```hcl
    gitlab_project_config = {
      project_payload = { name = ..., visibility = ... }   # 纯对象
    }
    config_hash = sha1(jsonencode(local.gitlab_project_config))
    ```

    **教训：`jsonencode` 只应该在"最终出口"调用一次。** 中间层一旦混入"已经序列化过的字符串"，就会出现双重编码。凡是看到"某个值本身就是 `jsonencode` 的结果、又要被塞进另一个 `jsonencode`"，都要停下来检查一遍。

另外这轮还顺手把「本机能执行的脚本」和「必须塞进 HCL 的脚本」彻底分开了：4 个 `scripts/*.sh` 改写成 `.py`，`inventory.tf` 里那 4 段几百行的内联 shell 也抽成了 `scripts/inventory.py`。**`inventory.tf` 里现在只有 4 行 `environment = { INVENTORY_SCRIPT = local.inventory_scripts.xxx }`，实体代码全在 `.py` 里** —— 这才是"几百行 shell 可读性差"的正解：不是把 shell 翻译成另一种 shell，而是让它离开 `.tf` 文件。

**第七轮（加入 Ansible 批量管理层）**

这一轮的目标是「批量管云服务器和本地服务器，但本地服务器不覆盖」。
按老规矩，先列出的都是**容易踩的坑** —— 其中第 19、24、25、26、28 条是这一轮
（离线测试 + 真机验证）真的抓出来的问题。

17. **「不改本地机器」不能靠约定，要靠结构。** 最容易写出来的方案是"跑的时候记得加 `--limit`"
    或者"在这个任务上写个 `when`" —— 两者都是流程约定，都会忘。最后落成三层：
    **结构性隔离**（会改东西的 role 只出现在 `hosts: cloud` 的幕里）+
    **运行前断言**（`guard` 在变更型幕里硬断言，防的是第一层被绕过）+
    **逐任务许可**（"新增"不带条件、"覆盖"必须带 `wf_can_write`）。
    关键在于三层的失效模式不同：忘写 `when` 会被第一层挡住，改错 `hosts` 会被第二层挡住，
    而第一层根本不依赖任何人记得什么。

18. **断言失败要给出"下一步怎么做"，不能只说"不行"。** `guard` 的 `fail_msg` 里直接写了三条命令：
    先只读体检、再 `--check --diff` 干跑、最后才真改。
    如果只报一句 `assertion failed`，用的人下一步一定是去搜"怎么关掉这个断言" ——
    那保护就白做了。

19. **★ 更危险的能力必须蕴含较弱的能力 —— 这是本轮真抓出来的问题。** 原来 `wf_can_write` 的公式是
    「不在 local 组 **或** 开了覆盖开关」。写测试穷举 16 种组合时才发现：
    **只给 `local_overwrite_allowed=true`、不给 `local_mutation_allowed` 时，
    `wf_can_write` 已经是 `True` 了** —— 也就是"没解锁变更，却已经拿到了覆盖权限"。

    真实路径上这个状态不可达（幕 E 的 `meta: end_play` 会先拦住整幕），
    但那是**靠 playbook 的写法**保证的，不是靠保护公式本身。保护公式自己就该是单调的，
    不该依赖调用方记得先加门闩。修正后的公式要求两个开关**同时**成立：

    ```yaml
    wf_can_write: >-
      {{ (not (wf_in_local_group | bool))
         or ((local_mutation_allowed | default(false) | bool)
             and (local_overwrite_allowed | default(false) | bool)) }}
    ```

    **一般规律：权限系统里"更危险的那一项"要显式蕴含"较不危险的那一项"，
    而不是各写各的条件。** 各写各的必然会在某个组合下出现反直觉状态。

20. **测试要读被测文件本身，不要在测试里重抄一遍公式。** 保护公式如果被抄进测试里，
    改公式时测试照样是绿的 —— 它测的是副本。所以 `selftest.py` 的做法是
    **`yaml.safe_load` 打开 `roles/guard/tasks/main.yml`，取出那两行表达式求值**；
    再配一条"公式里只允许出现这 4 个已知开关"的断言，公式一改测试就会提醒你回来看。

    同理，权限公式的测试要用 **`StrictUndefined`** 而不是 Ansible 那种"可链式未定义"：
    变量名写错时，可链式未定义会**静默变成 False**，而 False 恰好意味着"不保护" ——
    这是最危险的失败方向。必须让它报错。

21. **校验器必须被证明真的会响。** 写了 `check_guard.py` 之后，故意把幕 C 从 `hosts: cloud`
    改成 `hosts: all`、又故意删掉幕 E 的解锁门闩，确认两次都被抓到、且给的原因是对的；
    改回来再确认通过。`selftest.py` 的真值表也一样：把公式临时改回松散版本，
    确认穷举会报错并列出反例。**没被故意破坏过的校验器，等于没校验器。**

22. **「路径在不在禁区之下」要按路径段判断，不能按字符串前缀。** `/data/jenkins-data-OLD`
    不在 `/data/jenkins-data` 之下，但它是字符串前缀。`-OLD` / `-backup` / `.bak` 这种命名
    在实际环境里到处都是，用 `startswith` 会把正常路径误判成禁区。
    所以 `wf_under_any` 先归一化（`posixpath.normpath`，因为这些是远端 Linux 路径，
    不能用 Windows 规则解析），再按段比较。

23. **控制端跑不起来就说跑不起来。** Ansible 依赖 `os.fork`，Windows 原生 Python 没这个函数
    （`hasattr(os, 'fork')` → `False`），WSL 也被安全策略挡住。这种情况下有两个选择：
    假装验证过，或者**如实标注"这部分未验证"并列清楚是哪几类**。
    选了后者 —— 因为"看起来验证过"比"明确未验证"危险得多。

24. **★ `group_vars` 放错位置 = 整层保护被静默忽略 —— 这是真机验证抓出的最危险的 bug。**
    `wf_audit_services is undefined` 这个运行时报错，`--syntax-check` 和全部离线测试都拦不住。
    受控实验定位到根因：**Ansible 只加载"清单旁"或"playbook 旁"的 `group_vars`，别处一律静默忽略。**
    我们原来放在 `ansible/group_vars/` —— 既不在 `inventory/` 旁边也不在 `playbooks/` 旁边，
    于是 `wf_local_forbidden_paths` 整个是空的、`wf_report_dir` 落到 `default` 值；
    而 `guard` 看起来还正常，因为它判断分组用的是魔法变量 `groups['local']`，不依赖 group_vars。
    **"保护变量加载失败，但保护检查照常全绿"——这是整套防护里最坏的失败模式。**
    修法：目录挪到 `inventory/group_vars/`，并给 `check_guard.py` 加了第 8 类不变量（P8）：
    按 Ansible 的真实查找规则解析出它实际会去加载的位置，验证 `wf_local_forbidden_paths`
    真的会被读到。**配置框架"找不到就当没看见"的静默行为，必须用一条显式检查顶回去。**

25. **Ansible 的 Jinja 环境开着 `trim_blocks`，块标签写 `{%-` 会吞掉前面内容行的换行。**
    体检报告渲染出来所有循环项挤成一行，排查发现是模板里 `{% for %}` / `{% endfor %}`
    前面都加了 `-`。Jinja 文档里 `trim_blocks` 是个普通选项，但 Ansible **默认就开着**且文档不显眼。
    实验对比三种写法后确认：块标签上的 `{%-` 不但没必要（行首的空白已被 trim_blocks 吃掉），
    还会**额外吞掉上一个渲染行结尾的换行符**。修法是把模板里全部 31 个块标签的前导 `-` 删掉，
    并给离线渲染环境加上 `trim_blocks=True`、给报告测试加**按行断言**（子串断言对换行丢失是盲的）。

26. **模板引擎不区分注释和正文 —— shell 注释里的字面 `{{ }}` 也会炸。**
    Ansible 会把整个 `shell:` 字符串先过一遍 Jinja2 再交给 bash。一个采集任务的注释里
    写了"输出形如 `{{ }}` 的提醒"（讽刺的是，那条注释本来就是为了警告双花括号的坑），
    真机运行时报 `Syntax error in template`。修法是注释里改说"双花括号"，并在
    `selftest.py` 里加了 `test_shell_bodies_compile()`：抽出全部 11 个 shell/command body
    逐个过 `env.parse()`，还配了反向验证（字面 `{{ }}` 必须报错）和假阳性守卫（`{{ var }}` 不能误报）。

27. **WSL 的 `/mnt/c` 是"世界可写目录"，`ansible.cfg` 会被拒绝加载。** drvfs 挂载把所有文件
    呈现成 777，而 Ansible 出于安全拒绝加载世界可写目录里的配置 —— 只给一行 WARNING，
    然后 inventory/roles_path/fact caching 全部静默回到默认值。`chmod o-w` 在 drvfs 上改不动。
    两个解法：`ANSIBLE_CONFIG=<绝对路径>` 显式指定；或者（更好）把仓库拷到 WSL 原生 ext4 上跑。
    ★ 注意「拷到 ext4」**必须配一次 `chmod -R go-w`**，否则 777 会跟着 `rsync -a` 一起过去，
    报错一模一样 —— 见踩坑 40。
    同类的还有：**`inventory/` 目录里放示例文件会被当成清单解析**（`hosts.cloud.yml.example`
    触发 `Exec format error` + `Invalid host pattern`），示例文件要放到 `examples/` 去吊着。

28. **★ 模板产物里不能嵌"当前时间" —— 这是云侧幂等性验证抓出来的真 bug。**
    `index.html.j2` 的页脚写了 `渲染于 {{ now(fmt=...) }}`。template 模块靠
    "渲染结果和线上一模一样"判断要不要动手，时间戳每次都变 → 每次运行都判成"文件变了"
    → 连锁触发 `compose up -d` → **每次跑 playbook 容器都被无谓重启一次**。
    `changed=2` 看起来无害，但"重跑应该什么都不动"这条性质被破坏了。
    修法是删掉时间戳（想看部署时间去 `docker inspect` 看 StartedAt），修完连跑两次 `changed=0`。
    **一般规律：任何参与 change detection 的产物，内容必须只是"事实的函数"，不能是"时间的函数"。**

**第八轮（守住分层边界）**

这一轮的目标是「边界不只写在文档里」（当时是六条，第九轮扩到了八条）。踩到的坑集中在两处：
**边界本身漏了一半**（第 29 条），以及**裁判自己会犯错**（第 30 条）。

29. **★ 边界写在文档里不等于守住了 —— 全项目的 state 都在本地。**
    前七轮把「本地服务器不覆盖」做到了三层防护 + 8 类不变量 + 103 项断言，
    但被问「state 放在哪」时，答案是「各人电脑上的 `terraform.tfstate`」。
    README 里只有一句「多人协作应改用 OSS 后端 + 加锁」—— **一句话 = 零落实**。

    这个漏项的危险在于它**不会报错**：单人开发、单人 CI，永远跑不出问题，
    直到某天两个人同时 apply。那时的症状是「资源在云上、state 里没有」，
    报错信息指向的是别的地方，而且**持续计费**。

    修法是把这条也做成可检查的：`backend.tf.example` 写好放到位，
    再让 B4 规则**每次跑都提醒**「这个模块还没有远程后端」，
    CI 里加 `--require-remote-state` 就升级为硬失败。
    **规律：一条边界如果在「默认配置」下就已经被违反，它必须每次都被提示出来，
    否则它会一直保持被违反的状态 —— 因为没人会主动去修一个不报错的问题。**

30. **★ 校验器自己会犯错：这一轮自证抓出了 4 个检查器本身的缺陷。**
    写完之后 29 个用例跑出 4 个失败，逐个看下来**没有一个是样例写错**，
    全是检查器的漏洞 —— 这正好说明「自证」这一步不能省：

    - **自我误报**：扫真实仓库时，检查器把自己 selftest 里那些
      *故意写出来的越界样例*当成了真违规，报了 5 个假问题。
      修法是扫描时排除自身文件。**任何带样例的检查器都有这个问题。**
    - **argv 写法的漏检（最值得记）**：`['systemctl', 'restart', 'docker']`
      匹配不上按空格分词的模式 `systemctl\s+restart`。
      而 argv 数组恰恰是**更规范**的写法（不经过 shell）——
      结果是「规范写法漏检、不规范写法反而查得出」。
      **这种漏洞比不检查更糟：它给人「已经检查过了」的错觉。**
      修法是匹配前先把 `'a', 'b'` 摊平成 `a b`。
    - **不能只扫「执行上下文」**：判断「有没有直接读 state 文件」时，
      我一开始只扫 `subprocess` 附近的行 —— 但典型越界是 `open('terraform.tfstate')`，
      它根本不经过 subprocess。**上下文过滤要按"这条规则可能长什么样"来定，不能一刀切。**
    - **提示文本必须放行**：`echo "安装：sudo apt-get install -y python3"` 是提示不是执行。
      不区分的话，第一次运行就会报出一堆假问题，然后这个检查器会被无视、删掉。
      判定原则写进了脚本头部：**只看会被执行的命令。**

31. **边界检查要用闭集，并且把「豁免」显式写出来。**
    最初想把 K8s 规则写成「`kubernetes_` 开头就算越界」，一行正则搞定 ——
    但 `kubernetes_namespace`、`kubernetes_nodes`、`kubernetes_service_account`
    都是**集群周边对象**，不是「应用发布」，一刀切会把正常用法也拦掉，
    然后用户的第一反应是把整个检查关掉。改成显式闭集之后，
    规则长了一点，但「拦什么」一眼可见。

    同理，`user_data` 必须**显式豁免**：它内容是 `dnf install nginx`，
    形式上完全符合「装包」的特征，但它属于**实例启动配置**（资源存在性的一部分），
    而且只在创建时执行一次 —— 和 Ansible 的「把机器调到目标状态」不是一回事。
    **豁免要写在代码里、写在规则表里，不要靠「大家心里都清楚」。**
    靠默契的豁免，下一个人重构时一定会踩到。

**第九轮（CI/CD 成为唯一触发器 + 跨层衔接声明化）**

这一轮要补的是「那到底谁能动手」（第 32 条），以及把「除非在明确可控的特殊场景下」
这句话变成能 review 的东西（第 33、34 条）。后三条是方法上的教训。

32. **★ 边界漏项的第二种形态：连「载体」都不存在。**
    第八轮补上了 state 后端，但还有一条同样**不报错**的漏项 ——
    整个仓库**零个 CI 配置**（没有 `.gitlab-ci.yml`、没有 `Jenkinsfile`、没有任何 workflow）。

    于是「CI/CD 是变更的唯一触发器」这条边界不是「守得松」，而是**根本没守**：
    基础设施变更 100% 靠人在自己机器上跑 `terraform apply` / `ansible-playbook`，
    「以谁的本地环境为准」这个问题永远没有答案。

    最讽刺的是：仓库**已经在管** GitLab + Jenkins + Harbor（`vmware-workstation/cicd.tf`），
    但那条流水线是**应用交付**链路（构建 → 推镜像 → webhook → 部署）。
    基础设施这条链路，连一个 job 都没有。

    **规律（第 29 条的推广）：一条边界如果连载体都不存在，第一步不是"加检查"，
    是先把载体建出来 —— 否则检查只会一直红着，然后被人忽略。**

33. **★ 在 CI 里让 Terraform 触发 Ansible，等于把刚拆开的耦合装回去。**
    很自然的一种写法是 `deploy` job 里先 `terraform apply`、再 `ansible-playbook` ——
    一步到位，还挺方便。但它把两个独立的状态权威塞进了同一个 job：
    apply 那半截成功、后半截失败时，流水线只告诉你「这个 job 是红的」，
    「资源到底建没建、机器到底配没配」又说不清了。

    正确形态是**两个 job + artifact**：Terraform 的 job 把 `terraform output -json`
    存成 artifact，Ansible 的 job 消费它。这样 Ansible 那一侧**没有 state、没有云凭证** ——
    越界不是「约好了不做」，而是**做不到**。
    顺带的好处：CI 里的跨层调用数从 1 降到了 **0**，连那条豁免声明都不需要了。

    **规律：判断两个东西该不该耦合，看「失败了以后状态归谁」比看「方便不方便」可靠。**

34. **★ 一个永远不会响的检查，比没有检查更糟。**
    写 B8 的时候顺手加了一个「声明里不许成环」的检测 —— 听起来很合理
    （互相触发正是要禁的事）。写完才发现它**不可达**：
    既然只允许声明 `X → terraform` 这一个方向，环根本构造不出来。
    这个检查从来不会响。

    留着它的代价不是那几行代码，而是**它会让人以为这个性质被查过了** ——
    真正需要防的事情反而没人管。删掉它，把「单向」改由方向限制**结构性**保证，
    并在注释和文档里写清楚「这里为什么**没有**成环检测」。

    **规律：一个检查要么能举出触发它的小例子，要么它就是在骗人。**

35. **★ 别把「校验器不报错」当成「已经验过了」—— 要拿真实仓库做破坏性验证。**
    46 个自证用例全绿，只说明校验器在**合成样例**上是对的。
    真正的门槛是：它在**这个仓库的这些文件**上会不会响。
    所以又做了一轮：每次只改一处真实文件（拿掉 `when: manual`、去掉 `--limit`、
    把 `mode` 改成 `write`、加一个没登记的文件……），跑完立刻还原。

    第一遍的结果**不可信** —— S1 之后我忘了还原 `.gitlab-ci.yml`，
    导致后面几个场景全带着前一个伤口，"B7 又报了"根本分不清是哪个原因造成的。
    重写成「每个场景独立还原」之后才拿到干净的证据。

    **规律：破坏性验证里，还原是测试的一部分。不还原的话，测的是累积状态，不是那一次改动。**

36. **★ 规则之间要划清管辖，否则两条规则会悄悄变成一条。**
    S2 那个场景（在**已声明**的文件里加一句 `terraform apply`）一开始我期望 B8 报，
    结果 B8 通过了、B2 报了。想了一下这是对的：
    B8 管「有没有登记」，B2 管「登记的行为是不是只读」—— 各管各的。

    如果为了「让 B8 也报一句」而把写入的判定抄进 B8，短期看起来更"全面"，
    实际是制造了两份会各自漂移的判定逻辑。
    同理，B1/B2/B8 三条规则共用**同一份**遍历生成器（`iter_ansible_calls` /
    `iter_terraform_calls`），就是为了防止某天改了过滤条件之后
    「B1 说它越界、B8 说没这回事」这种自相矛盾的输出。

    **规律：发现两条规则好像都在管同一件事时，先确认它们管的其实不是同一件事 ——
    多数情况下确实不是，划不清才是少数。**


**第十轮（宿主机边界 + 豁免登记）**

这一轮的起点是新加的一条边界：**demo 全部跑在虚机内部，宿主机上的虚拟化平台不动、也不声明为资源**。
对着仓库过了一遍，发现两处与它相悖的代码 —— 而检查器一条都管不到：

37. **★ 边界宣布了，不代表仓库里没有反例 —— 要找的是「违反它、却查不到的」那种。**
    `vmware-workstation/archive-vm-clone/`（用 `vmrun` 克隆宿主机虚机）和
    `vmware-vsphere-reference/main.tf`（`resource "vsphere_virtual_machine"`）
    都违反这条边界，而且都没有任何规则能发现它们。

    越是「当初就没打算让它跑起来」的代码，越容易绕过所有检查 ——
    因为它连「要不要被检查」这个问题都没被问过。
    所以新增 B9 时，顺手也把 `vmware-vsphere-reference` 从「在用模块」里摘了出来：
    它明明只是一份对照材料，却在被 B4 要求「配远程后端」，这种噪音会淹掉真信号。

38. **★ 隐式豁免是定时炸弹，两个方向都会炸。**
    原来的实现是「目录名以 `archive` 开头就跳过」。这个判据两边都不安全：
    - 把目录改个名，豁免**静默失效** —— 检查器开始对着一堆存档代码报红，而那些红全是噪音
    - 真在跑的代码塞进一个叫 `archive-` 的目录，就**永远查不到了**

    于是豁免改成登记在册（`boundaries.yml` 的 `exemptions`），并且**清单自身也被检查**：
    缺 `why` 报错、登记了不存在的路径提示「豁免腐烂」。

    要验证这一点，看代码是不够的，得在真实仓库上**改名** ——
    S11 把 `archive-vm-clone/` 改成 `old-vm-clone/`，检查器立刻报出 29 个问题。
    **改名前后行为不同，才证明判据是清单而不是目录名。**

    还有一个设计上的取舍：豁免对**所有**检查生效，没做成「每条规则一个跳过开关」。
    留开关的话，迟早有某条检查忘了打开它 —— 那时最该被豁免的代码反而被查得最狠。

39. **★ 破坏性脚本里不要用会在中途退出的下游命令。**
    这一轮的破坏性验证第一次跑挂了：`detail()` 里用了 `| head -6`，
    检查器输出超过 6 行时 head 提前退出 → 上游写 stdout 撞上 SIGPIPE 后**挂住** →
    `trap` 里的还原迟迟不执行 → 场景 S4 的破坏留在了工作区里（是我事后手工补回来的）。

    这个坑在第八轮的破坏性验证里已经踩过一次（当时是 `head` 打断了 S5）——
    同一个坑踩第二次，说明「记着点」不管用，得改结构：
    检查器输出先落盘（`> "$OUT" 2>&1`），后续的 `sed` 从**文件**读，
    这样任何下游命令提前退出都伤不到上游。

    **规律：破坏性脚本里每个下游命令都要问一句「它会读完所有输入吗」。**

40. **★ `rsync -a` 会把「世界可写」一起搬过去 —— 换到 ext4 不等于解决。**
    这一轮的复跑一开始就撞上了熟悉的 WARNING：

    ```
    [WARNING]: Ansible is being run in a world writable directory
    (~/wf-verify/terraform-demo/ansible), ignoring it as an ansible.cfg source.
      config file = None
    ```

    可这次仓库**已经在原生 ext4 上了**，为什么还报？因为文件是从 `/mnt/c` 用
    `rsync -a` 拷过来的，而 `-a` 隐含 `-p`（保留权限位）—— 源头的权限位就是 drvfs 给的
    `777`，于是 ext4 上刚落地的一整棵树**仍然是 777**。

    之前 README 里写的解法是「拷到 ext4 里就好了」—— 那只做对了一半。
    真正让它生效的是拷完那一次 `chmod -R go-w`；少了它，报错一模一样，而且
    检查方式只有 `ansible --version | grep "config file"` 这一行（它默认只给 WARNING，
    不报错，`inventory` / `roles_path` 全部静默失效）。

    **规律：凡是「换个位置就好了」的说法，都要问一句「搬过去的东西属性是谁给的」。**

41. **★ 已收敛的系统上跑「幂等」，证明不了变更路径是对的。**
    这一轮的复跑里，`site.yml -e local_mutation_allowed=true` 是 `changed=0` ——
    因为上一轮已经把 `jq` 和 `/opt/wf` 都补上了，**没有东西可补**。
    这个结果看着像"通过"，其实什么都没验到：它只证明了「无事可做时不乱做事」。

    要验证「只补缺、不覆盖」这条性质，必须先**制造差异**：用 `-e` 把
    `wf_base_dirs` 临时扩成一个不存在的路径，于是补缺类任务真的动手（`changed=1`），
    同时比对时区 / `sysctl` / `limits` 的 md5 前后是否一致（覆盖类必须纹丝不动）。
    跑完把探针目录删掉，端状态与跑之前完全相同。

    **规律：验收「幂等」和验收「变更」，需要两种不同的前置状态 —— 拿一种状态去验两件事，
    必然有一件是在自我安慰。**


---

## 成本与风险提示

### 云端（阿里云）

- 阿里云 ECS 默认**按量付费**，但**只要实例存在就会计费**（含公网带宽）。学习完请务必 `terraform destroy`。
- 阿里云那部分建议在**个人测试账号**里练习，不要在有生产资源的账号下 apply。

### 本地（Workstation / K3s）

- 本地部分**默认只读**（`manage_containers` / `manage_cicd` / `manage_gitlab_cicd` 全部为 `false`），第一次 `apply` 不会改动你机器上任何东西。开启管理前请先用盘点输出核对自己的实际配置。
- **Jenkins 是 bind mount，不是命名卷，备份要自己来。** `/data/jenkins-data` 是宿主机上的普通目录，`docker volume` 那套备份手段对它无效。请把 `/data/jenkins-data` 纳入常规备份（重要东西其实只有 `jobs/`、`credentials.xml`、`config.xml`、`users/`，几百 MB 级别），别指望 Docker 会替你留着。
- **不要把 `docker-compose.yaml` 直接指向 `/data/jenkins-data` 之外**——`jenkins_compose_dir` 默认就是 `/data/jenkins-data`，改成别处会让"数据和编排配置分离"这个有意设计失效。
- **`jenkins_manage_compose` 默认 `false` 是有意的**。它的 `true` 分支会**覆盖你的 compose 文件**（先备份成 `.bak` 再写）。只有当你确认"要让 Terraform 接管 compose 内容"时才打开。日常只想核对，保持 `false` 即可——`verify` 模式只报告、不写入。
- **`jenkins_stack_down_on_destroy` 默认 `false`**。这意味着 `terraform destroy` **不会停掉 Jenkins**——因为它是你现有的服务，不是 Terraform 建的。想让它停，显式设为 `true`。**任何情况下都不会执行 `down -v`**，数据目录不会被删。
- **特别注意 `privileged: true` + `user: root` + 挂载 `docker.sock`**：这个 Jenkins 容器实际上是宿主机的 root 等价权限。任何能在 Jenkins 里执行构建的人，都能拿到宿主机 root。学习环境可以接受，**但不要把它暴露到公网**，不要给不可信的人 Jenkins 账号。
- **内存限制 2G 偏紧**：`deploy.resources.limits.memory: 2G`。如果 Jenkins 里同时跑构建、又装了很多插件，JVM 可能被 OOM 杀掉，表现是容器莫名重启（`restart: always` 会立刻拉起来，所以你可能只觉得"卡了一下"）。频繁重启就往 4G 调，或设 `JAVA_TOOL_OPTIONS` 里的堆上限。
- **端口 8081 是个容易踩的坑**：容器内是 8080，映射到宿主机是 8081。所有"从外面访问 Jenkins"的地方都要用 **8081**——浏览器、Harbor 的 webhook 地址、GitLab 的 webhook 地址。写 8080 的后果不是报错，而是**回调静默失败**（Harbor 显示"已发送"，Jenkins 什么都没收到）。代码里已加了自动检查，但你自己手改 webhook 时仍要留意。

### GitLab（从节点）

- **GitLab 是 Omnibus 实体服务，Terraform 从不接管服务本身**——只管 API 层的项目与 webhook。服务启停/配置变更走 `gitlab-ctl`。
- **GitLab 没有容器的「重建不丢数据」优势**：数据全在 `/var/opt/gitlab`，请定期 `sudo gitlab-backup create`。
- **`gitlab_webhook_test_on_apply` 默认 `false`**，因为它发出的是真实 push 事件，会触发一次完整流水线（构建 → 推镜像 → 部署）。验证时临时打开，用完记得关掉。
- **默认配置的 GitLab 会拒绝向内网地址发 webhook**（`192.168.0.0/16` 等私有网段在默认的"允许出站"名单之外）。如果回调不触发，先查这里：`Admin → Settings → Network → Outbound requests`。这是 GitLab 的安全默认值，不是 bug。
- **`gitlab_cleanup_on_destroy` 与 `cicd_cleanup_on_destroy` 默认都是 `false`**：`destroy` 时保留 webhook，避免掐断正在跑的流水线。

### Harbor

- **Harbor 默认配置是约 9 个容器的 compose 栈，Terraform 不可能接管它。** 这不是"暂不接管"，是"无法接管"——`manage_harbor` 这个变量**在代码里被硬编码为永远为 `false`**，写 `true` 也不会生效，只会打印一段说明。详细原因见上文「关于 Harbor」一节。
- **`PULL_ARTIFACT` 类事件会造成无限循环**：如果 Harbor webhook 订阅了"拉取镜像"事件，而 Jenkins 部署时又去拉镜像，就会 拉取→触发→部署→拉取… 永远停不下来。只订阅 `PUSH_ARTIFACT`。

### 通用

- 渲染后的脚本会包含 Harbor 密码 / Jenkins Token / GitLab Token。若某个 `local-exec` 步骤执行失败，错误输出里**可能带出这些值**——注意别把失败日志直接贴到公开的地方。
- `terraform.rc` 与 `*.tfvars` 已被 `.gitignore` 排除（只提交 `.example`）。

