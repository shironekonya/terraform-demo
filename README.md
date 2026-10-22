# 个人云原生实战：Terraform + Ansible 全链路

本项目主要用于个人云原生实战。

**前提**：本地已有 CI/CD 流水线（Jenkins + Harbor + GitLab），现在要把「云资源 → 机器基线 → 应用部署」整条链路打通并纳管。

---

## 1. 主要思路

- **Terraform 纳管云资源**：VPC、交换机、安全组、ECS 全部声明式创建，按量付费、可整组删除。
  同时声明本地服务器上**以 HTTP API 为接口的资源**（Harbor 项目与 webhook、Jenkins 任务、GitLab 项目与 webhook）——
  这类资源与安装方式无关，适合声明式管理；虚机本身没有 provider，不进 Terraform。
- **Ansible 做批量变更**：云服务器基线（docker、镜像加速、目录与权限）与应用部署（compose 栈、系统 nginx 反代 80）都在这一层，
  全程幂等，变更后自带自检。
- **两者互不干涉**：Terraform 不调用 Ansible，Ansible 不调 `terraform output`，跨层数据只经 CI artifact 或声明文件传递。
  这条边界连同另外八条一起写进 [`boundaries.yml`](boundaries.yml)，由 `tools/check_boundaries.py` 静态检查。
- **下一步**：接入本地 CI 编排，让 `.gitlab-ci.yml` 成为环境变更的唯一入口
  （`validate → plan → apply（人工放行）→ configure → verify`，`apply` 用的是 `plan` 产出的那份计划文件）。

## 2. 整体逻辑

见 [`LOGIC.md`](LOGIC.md)：代码怎么分层、按什么顺序启动、每段为什么这么写，以及**验证状态**（含明确标注未验证的部分）。

## 3. 错误总结

见 [`PITFALLS.md`](PITFALLS.md)：49 条实践踩坑，按轮次分组、带索引，每条对应一种真实的失败模式。

---

## 复核

```bash
python3 tools/check_boundaries.py             # 九条边界逐条检查
python3 tools/check_boundaries.py --selftest  # 证明检查器确实会响：65 个用例
```

检查器只用 Python 标准库，不连云、不连机器、不装依赖，任何机器上都能复现同一结果。

## 注意

- 云端资源按量付费，实例存在期间持续计费，练习结束执行 `terraform destroy`；建议在独立测试账号内操作。
- 本地部分**默认只读**，首次 `apply` 不会改动任何内容；开启管理前先用盘点输出核对自己的实际配置。
- 不假设 Docker Hub 能连通：拉不到镜像时 playbook 停在「镜像就位」这一步并给出三条路（加速器 / 离线包 / 换镜像）。
- 这不是生产模板，也不是「一键部署」；完整的成本与风险清单见 [`LOGIC.md` 的「成本与风险提示」](LOGIC.md#成本与风险提示)。
