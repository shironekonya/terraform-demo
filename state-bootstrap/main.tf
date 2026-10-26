# ===========================================================================
# state-bootstrap —— 用 Terraform 建"远程后端本身"（OSS 状态桶）
#
# 为什么这个模块存在：
#   aliyun-ecs 和 vmware-workstation 的 backend 需要 OSS 桶，
#   而 Terraform 的 backend 不能用资源去创建自己 —— 这是鸡生蛋。
#   所以这个"底层资源"由一个**本地 state** 的迷你模块先建出来。
#
#   它和 tools/install_runner.sh 是同一种东西：一次性 bootstrap，
#   建完之后几乎不再运行。也因此登记在 boundaries.yml 的豁免段
#   （B4 不要求它自己配远程后端 —— 它没有更底层的后端可用了）。
#
# 状态锁：**不建 OTS**。并发保护由执行器保证（runner 的 concurrent = 1，
#   同一时刻只有一个 job 在 apply）。加第二个 runner 之前必须补锁：
#   建 OTS 实例 + LockID 主键锁表，并在两份 backend.tf 里配上
#   tablestore_endpoint / tablestore_table（模板见 backend.tf.example）。
#
# 用法（147 上，key 变量已收敛到 /root/.wf-aliyun.env）：
#   set -a; . /root/.wf-aliyun.env; set +a
#   terraform init && terraform apply
#
# state 放哪：本地（./terraform.tfstate）。它自己就是"最底层"，
#   内容不含敏感值（桶名），丢了可以重建，撞名会报错而不是覆盖。
# ===========================================================================

terraform {
  required_version = ">= 1.3"
  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = ">= 1.210"
    }
  }
}

provider "alicloud" {
  region = var.region
}

# ---------------------------------------------------------------------------
# 状态桶
#
# ★ 名字全局唯一（OSS 的桶不分 region 共享命名空间）。被占用时 apply 会报
#   BucketAlreadyExists —— 那就换一个：改 var.bucket 后，
#   aliyun-ecs/backend.tf 与 vmware-workstation/backend.tf 里的 bucket 也要跟着改。
# ---------------------------------------------------------------------------
resource "alicloud_oss_bucket" "tfstate" {
  bucket = var.bucket
  acl    = "private"

  # state 里有明文凭证（user_data、密钥），服务端加密成本为零
  server_side_encryption_rule {
    sse_algorithm = "AES256"
  }

  # 防误删：state 一丢，destroy 不掉的"幽灵资源"就只能手工清理了
  # （新 provider 用 status 而不是 enable —— 字段名跟着 provider 走）
  versioning {
    status = "Enabled"
  }
}
