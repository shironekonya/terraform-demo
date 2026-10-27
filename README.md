# 个人云原生实战：Terraform + Ansible 全链路

本项目主要用于个人云原生实战。

**前提**：本地已有 CI/CD 流水线（Jenkins + Harbor + GitLab + k3s），现在要把「云资源 → 机器基线 → 应用部署」整条链路打通并纳管。

---

## 1. 主要思路

- **Terraform 纳管云资源**：VPC、交换机、安全组、ECS 全部声明式创建，按量付费、可整组删除。
  同时声明本地服务器上**以 HTTP API 为接口的资源**（Harbor 项目与 webhook、Jenkins 任务、GitLab 项目与 webhook）——
  这类资源与安装方式无关，适合声明式管理；虚机本身没有 provider，不进 Terraform。
- **Ansible 做批量变更**：云服务器基线（docker、镜像加速、目录与权限）与应用部署（compose 栈、系统 nginx 反代 80）都在这一层，
  全程幂等，变更后自带自检。
- **两者互不干涉**：Terraform 不调用 Ansible，Ansible 不调 `terraform output`，跨层数据只经 CI artifact 或声明文件传递。
  这条边界连同另外八条一起写进 [`boundaries.yml`](boundaries.yml)，由 `tools/check_boundaries.py` 静态检查。
- **CI 是变更的唯一入口，全自动蓝绿灰度**：`.gitlab-ci.yml` 把「谁、什么时候、以什么为准去改环境」
  收敛成一条路径 —— `git push` 即触发，全程无人工门：
  算槽位 → terraform 在**新槽位**（blue/green 两个 workspace 之一）整套建资源（旧机器不受影响）→
  ansible 初始化 → 请求新机器 nginx 做健康检查（200 且版本标记 = 本次 commit）→
  **失败自动销毁新机器回滚，成功自动回收旧机器**。Terraform 与 Ansible 在流水线里是两个**独立 job**，
  数据只经 artifact 传递。人工门撤掉后的安全网是 B7 的两条硬规则：
  变更 job 必须限定默认分支、流水线必须存在回滚 job；`teardown`（整组销毁）等不可逆动作仍保留人工门。

## 2. 整体逻辑

见 [`LOGIC.md`](LOGIC.md)：代码怎么分层、按什么顺序启动、每段为什么这么写，以及**验证状态**（含明确标注未验证的部分）。

## 3. 错误总结

见 [`PITFALLS.md`](PITFALLS.md)：57 条实践踩坑，按轮次分组、带索引，每条对应一种真实的失败模式。

---

## 接流水线（本地 GitLab）

Runner 装在**已经有工具链和 key 变量的那台**（本项目是 `192.168.193.147`：terraform + ansible + ECS 私钥 + 阿里云凭证），
用 shell executor——`.gitlab-ci.yml` 里的 job 靠 `tags: [wf-infra]` 找到它。

```bash
sudo GITLAB_URL=http://192.168.193.139:8000 \
     RUNNER_TOKEN=glrt-xxxxxxxxxxxx \
     ./tools/install_runner.sh
```

配套三件事（一次性 bootstrap，都在 147 上，`RUNNER_147.local.sh` 一个脚本做完）：

- **key 变量收敛**：shell executor 不继承登录 shell，机器上已有的 key 变量要收敛成
  `/root/.wf-aliyun.env`（权限 600），CI 的 `before_script` 会 source 它；
  也可以改用 GitLab CI Variables（两种并存时这份文件优先）。
- **terraform 入参收敛**：`terraform.tfvars`（SSH 公钥、放行网段）和 `terraform.rc`（provider 镜像）
  都被 gitignore，CI 每次都是全新 clone —— 收敛成 `/root/.wf-aliyun.tfvars` 与 `/root/.wf-terraform.rc`。
- **远程 state 的底层资源**：`state-bootstrap/` 模块创建 OSS 状态桶 `cloud-native-demo`
  （backend 不能用资源创建自己，鸡生蛋，所以自举），它在 `boundaries.yml` 登记豁免。
  **状态锁不配 OTS**：并发保护由 runner 的 `concurrent = 1` 保证 —— 加第二个 runner 前必须补锁
  （模板见 `backend.tf.example`）。

Runner 起来后 push 任意提交（包括空提交），流水线即触发：`validate` 两个 job 不需要云凭证，
应当先绿；灰度路径需要 key 变量、tfvars、后端资源三者就位。

**灰度的成本须知**：每次 push 会临时多建一台 ECS（按量付费），健康检查通过后旧机器即被回收 ——
灰度窗口内是双机计费，失败回滚时新机器也会被销毁，不会留闲置资源。

完整的取舍（为什么是 shell 而不是 docker executor、为什么 `concurrent = 1`、
为什么 runner 不上 GitLab 那台）写在 [`LOGIC.md`](LOGIC.md)。

## 复核

```bash
python3 tools/check_boundaries.py             # 九条边界逐条检查
python3 tools/check_boundaries.py --selftest  # 证明检查器确实会响：69 个用例
```

检查器只用 Python 标准库，不连云、不连机器、不装依赖，任何机器上都能复现同一结果。

Ansible 侧另有一套离线护栏与自证（167 项断言），跑法见 [`LOGIC.md`](LOGIC.md)。

## 注意

- 云端资源按量付费，实例存在期间持续计费，练习结束执行 `terraform destroy`；建议在独立测试账号内操作。
- 本地部分**默认只读**，首次 `apply` 不会改动任何内容；开启管理前先用盘点输出核对自己的实际配置。
- 不假设 Docker Hub 能连通：拉不到镜像时 playbook 停在「镜像就位」这一步并给出三条路（加速器 / 离线包 / 换镜像）。
- 这不是生产模板，也不是「一键部署」；完整的成本与风险清单见 [`LOGIC.md` 的「成本与风险提示」](LOGIC.md#成本与风险提示)。
