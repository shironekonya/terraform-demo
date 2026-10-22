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
- **CI 是变更的唯一入口**：`.gitlab-ci.yml` 把「谁、什么时候、以什么为准去改环境」收敛成一条路径
  —— `git push` → 流水线。七个阶段：`validate → plan → apply（人工放行）→ configure → verify → rollback → teardown`。
  Terraform 与 Ansible 在流水线里是两个**独立 job**，数据只经 artifact 传递；
  `apply` 执行的是 `plan` 产出的那份计划文件，不是重新算一遍 —— 流水线里被看过的那份，就是被执行的那份。
  **发布与回滚都在这里**：`configure:cloud` 把 `$CI_COMMIT_SHORT_SHA` 当版本名发布，
  `rollback:app` 是个 manual job，按 `releases/<版本>/` 里的产物退回上一版或指定版本。

## 2. 整体逻辑

见 [`LOGIC.md`](LOGIC.md)：代码怎么分层、按什么顺序启动、每段为什么这么写，以及**验证状态**（含明确标注未验证的部分）。

## 3. 错误总结

见 [`PITFALLS.md`](PITFALLS.md)：53 条实践踩坑，按轮次分组、带索引，每条对应一种真实的失败模式。

---

## 接流水线（本地 GitLab）

Runner 装在**已经有工具链的那台**（本项目是 `192.168.193.147`：terraform + ansible + ECS 私钥），
用 shell executor——`.gitlab-ci.yml` 里的 job 靠 `tags: [wf-infra]` 找到它。

```bash
sudo GITLAB_URL=http://192.168.193.139:8000 \
     RUNNER_TOKEN=glrt-xxxxxxxxxxxx \
     ./tools/install_runner.sh
```

然后三件必须在 GitLab 侧做的事：Runner 变绿（online，tag=`wf-infra`）、
配 CI/CD Variables（`ALICLOUD_ACCESS_KEY` / `ALICLOUD_SECRET_KEY` Masked、`WF_SSH_KEY_FILE` File 类型）、
该机器上的工作区保持干净（shell executor 直接在 `$CI_PROJECT_DIR` 里跑）。

完整的取舍（为什么是 shell 而不是 docker executor、为什么 `concurrent = 1`、
为什么 runner 不上 GitLab 那台）写在 [`LOGIC.md`](LOGIC.md)。

## 复核

```bash
python3 tools/check_boundaries.py             # 九条边界逐条检查
python3 tools/check_boundaries.py --selftest  # 证明检查器确实会响：67 个用例
```

检查器只用 Python 标准库，不连云、不连机器、不装依赖，任何机器上都能复现同一结果。

Ansible 侧另有一套离线护栏与自证（166 项断言），跑法见 [`LOGIC.md`](LOGIC.md)。

## 注意

- 云端资源按量付费，实例存在期间持续计费，练习结束执行 `terraform destroy`；建议在独立测试账号内操作。
- 本地部分**默认只读**，首次 `apply` 不会改动任何内容；开启管理前先用盘点输出核对自己的实际配置。
- 不假设 Docker Hub 能连通：拉不到镜像时 playbook 停在「镜像就位」这一步并给出三条路（加速器 / 离线包 / 换镜像）。
- 这不是生产模板，也不是「一键部署」；完整的成本与风险清单见 [`LOGIC.md` 的「成本与风险提示」](LOGIC.md#成本与风险提示)。
