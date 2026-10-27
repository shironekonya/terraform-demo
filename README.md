# 个人云原生实战：Terraform + Ansible 全链路

自用实战项目，工具链：GitLab + GitLab Runner → Terraform → Ansible

## 分工与边界

- **Terraform**：声明式管理阿里云 VPC、交换机、安全组、ECS；Harbor/Jenkins/GitLab 的 API 类资源也由此管理。虚机系统安装不归它管。
- **Ansible**：主机基线（Docker、镜像加速、权限目录）、容器栈、Nginx 反向代理。操作幂等，执行后自带自检。
- **分层隔离**：Terraform 与 Ansible 互不调用，跨层数据仅通过 CI artifact / 配置文件传递。9 条约束写在 [boundaries.yml](boundaries.yml)，由 `check_boundaries.py` 静态校验。
- **CI 为唯一变更入口，蓝绿灰度**：`git push` 触发流水线。在蓝 / 绿独立 workspace 新建资源，不影响旧实例；部署后做健康检查。失败自动销毁新资源回滚；成功自动回收旧 ECS。
- 常规变更限定默认分支、自带回滚；`teardown` 销毁类高危操作保留人工确认。

文档：[LOGIC.md](LOGIC.md)（流程分层、风险）｜[PITFALLS.md](PITFALLS.md)（57 条踩坑记录）

## GitLab Runner 部署

Runner 部署在 `192.168.193.147`（预装工具、阿里云凭证、ECS 私钥，shell 执行器），通过 `tags: [wf-infra]` 绑定任务。

```bash
sudo GITLAB_URL=http://192.168.193.139:8000 \
RUNNER_TOKEN=glrt-xxxxxxxxxxxx \
./tools/install_runner.sh
```

一次性前置配置，执行 `RUNNER_147.local.sh`：

- 密钥收敛至 `/root/.wf-aliyun.env`（权限 600），CI 自动 source
- Terraform 变量文件放本地：`.wf-aliyun.tfvars`、`.wf-terraform.rc`（不入库）
- `state-bootstrap` 预先创建 OSS state 桶；单 runner 靠 `concurrent=1` 做状态保护

流水线：代码提交自动触发，`validate` 阶段无需云凭证优先校验；灰度部署依赖密钥、tfvars、OSS 就绪。

每次 push 临时新建一台按量 ECS，灰度双机计费；失败自动销毁，无残留资源。

## 校验

```bash
python3 tools/check_boundaries.py             # 边界静态检查
python3 tools/check_boundaries.py --selftest  # 校验器自检，69 用例
```

Ansible 内置 178 项离线断言，用法见 [LOGIC.md](LOGIC.md)。

## 提醒

- 按量付费，闲置执行 `terraform destroy`；建议独立测试账号。
- 默认只读，启用前核对环境；镜像拉取有加速器 / 离线包备选方案。
- 仅个人练习，不可直接用于生产，完整风险见 [LOGIC.md](LOGIC.md)。
