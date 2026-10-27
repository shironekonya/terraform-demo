# 踩坑清单

这个仓库在十二轮迭代里**实际修掉**的 46 个问题。全部来自真实报错、真机验证和破坏性验证，  
没有一条是「可能会踩」的推测。

它的编号就是文档正文里那些引用的编号 —— `LOGIC.md` 里写的「见踩坑 27」，  
指的就是下面第 27 条。每条都写清了三件事：**症状是什么、为什么编译期/静态检查抓不到、修法背后的规律是什么。**

> **带 ★ 的共 18 条，是最值得先看的那一类。** 典型是四种：  
> 不报错、但后果严重的静默失败（如第 24、28、29、32 条），  
> 看起来在保护你、其实没保护的假安全感（如第 30、34、35 条），  
> 同一个坑踩了第二次之后的结论（如第 39 条），  
> 以及报错信息指向的地方不是真正的原因、或遗留物在故障发生之后才现形（如第 43、45 条）。

**怎么用这份清单**

- **排查线上问题时先扫一遍。** 这里的症状描述比报错信息长，但往往一句就能对上号 ——  
  尤其是那些「报错信息指向的地方不是真正的原因」的条目（第 24、30、40、43 条）。000000000000000000000000000000000000000000000000000
- **写新代码前读对应的轮次。** 轮次是按模块分的，第 1~~4 轮是云端与容器，第 5~~6 轮是脚本，  
  第 7 轮是 Ansible，第 8~10 轮是边界与校验器，第 11 轮是 Terraform 交给 Ansible 之后的真机联调。
- **别只记结论，记规律。** 每条末尾的「规律」/「教训」是可以在别处复用的那一句话。

## 索引

| #  | 一句话                                                   | 轮次  |
| -- | ----------------------------------------------------- | --- |
| 1  | Terraform 没有 `filetimestamp()`，改用 `filesha1()`        | 第一  |
| 2  | `user_data_replace_on_change` 是 AWS 专有参数，阿里云不认        | 第一  |
| 3  | 输出引用敏感变量必须显式 `sensitive = true`                       | 第一  |
| 4  | destroy provisioner 不能引用 `var.*`（连 `interpreter` 也不行） | 第二  |
| 5  | `set -e` 与 `[ ] && { }` 的交互                           | 第二  |
| 6  | 同一个 destroy 陷阱又踩一次：`self.triggers.*` 必须先定义            | 第三  |
| 7  | 渲染占位符不能用 `sed`，要用 `replace()`                         | 第三  |
| 8  | `local` 值在整个模块内必须唯一                                   | 第四  |
| 9  | HCL 三元表达式换行必须加括号                                      | 第四  |
| 10 | `set -u` 下的空数组展开                                   | 第四  |
| 11 | `host_port_of` 的参数语义搞反了（容器端口 vs 宿主端口）                 | 第五  |
| 12 | HCL 模板插值与 shell 参数展开的字面冲突                             | 第五  |
| 13 | 旧的 `docker run` 方案会和 compose 抢对象                      | 第五  |
| 14 | `harbor_url` 少了 `/api/v2.0`，而且连通性检查是假的                | 第六  |
| 15 | `templatefile()` 会解析注释里的 `${...}`                     | 第六  |
| 16 | 把已 `jsonencode` 的字符串当对象嵌套 → 错误请求体                     | 第六  |
| 17 | 「不改本地机器」不能靠约定，要靠结构                                    | 第七  |
| 18 | 断言失败要给出「下一步怎么做」                                       | 第七  |
| 19 | ★ 更危险的能力必须蕴含较弱的能力                                     | 第七  |
| 20 | 测试要读被测文件本身，不要重抄一遍公式                                   | 第七  |
| 21 | 校验器必须被证明真的会响                                          | 第七  |
| 22 | 禁区判定要按路径段，不能按字符串前缀                                    | 第七  |
| 23 | 控制端跑不起来就说跑不起来                                         | 第七  |
| 24 | ★ `group_vars` 放错位置 = 整层保护被静默忽略                       | 第七  |
| 25 | Ansible 默认开 `trim_blocks`，块标签写 `{%-` 会吞换行             | 第七  |
| 26 | 模板引擎不区分注释与正文，注释里的 `{{ }}` 也会炸                         | 第七  |
| 27 | WSL 的 `/mnt/c` 是世界可写目录，`ansible.cfg` 被拒载              | 第七  |
| 28 | ★ 模板产物里不能嵌「当前时间」                                      | 第七  |
| 29 | ★ 边界写在文档里不等于守住了：state 全在本地                            | 第八  |
| 30 | ★ 校验器自己会犯错（自证抓出 4 个缺陷）                                | 第八  |
| 31 | 边界检查要用闭集，豁免要显式写出来                                     | 第八  |
| 32 | ★ 边界漏项的第二种形态：连载体都不存在                                  | 第九  |
| 33 | ★ 在 CI 里让 Terraform 触发 Ansible = 把耦合装回去               | 第九  |
| 34 | ★ 一个永远不会响的检查，比没有检查更糟                                  | 第九  |
| 35 | ★ 别把「校验器不报错」当成「已经验过了」                                 | 第九  |
| 36 | ★ 规则之间要划清管辖                                           | 第九  |
| 37 | ★ 要找的是「违反边界却查不到」的反例                                   | 第十  |
| 38 | ★ 隐式豁免是定时炸弹，两个方向都会炸                                   | 第十  |
| 39 | ★ 破坏性脚本里不要用会中途退出的下游命令                                 | 第十  |
| 40 | ★ `rsync -a` 会把「世界可写」一起搬过去                            | 第十  |
| 41 | ★ 已收敛的系统上跑「幂等」，证明不了变更路径是对的                            | 第十  |
| 42 | CRLF 行尾会让 `local-exec` 里整段 bash 语法错乱                  | 第十一 |
| 43 | ★ 镜像的系统 Python 决定了谁能管这台机器                             | 第十一 |
| 44 | 安全组只开了声明的端口，容器映射的端口没开 —— 验收连不进去                       | 第十一 |
| 45 | ★ `known_hosts` 是唯一跨 `destroy` 存活的遗留物                 | 第十一 |
| 46 | ★ 同一台机器开了两个对外端口，等于有了两个「首页」                        | 第十二 |
| 47 | ★ 拉不到镜像不是编排问题，但报错会让人这么以为                          | 第十三 |
| 48 | ★ 报错行号对不上，说明你排的是旧副本                                  | 第十四 |
| 49 | ★ 「5xx 才算故障」把「降级但仍在服务」判成了故障                        | 第十四 |
| 50 | ★ 回滚不是重跑一遍发布                                                | 第十五 |
| 51 | ★ 单文件 bind mount 把 inode 钉死，换内容不生效                        | 第十五 |

> 轮次导航：[第一轮](#第一轮阿里云部分) · [第二轮](#第二轮本地容器部分) · [第三轮](#第三轮cicd-部分) ·  
> [第四轮](#第四轮gitlab-部分) · [第五轮](#第五轮拿到真实-compose-文件之后) · [第六轮](#第六轮shell-改写成-python) ·  
> [第七轮](#第七轮加入-ansible-批量管理层) · [第八轮](#第八轮守住分层边界) · [第九轮](#第九轮cicd-成为唯一触发器--跨层衔接声明化) ·  
> [第十轮](#第十轮宿主机边界--豁免登记) · [第十一轮](#第十一轮真机联调terraform-交出去之后) ·  
> [第十二轮](#第十二轮把入口收成一个) · [第十三轮](#第十三轮镜像不是顺手就能拉到的) ·  
> [第十四轮](#第十四轮在真机上验失败路径不是应该会这样) ·  
> [第十五轮](#第十五轮发布与回滚回滚到底回的是什么)

---

## 第一轮：阿里云部分

1. **`filetimestamp()` 这个函数不存在** —— Terraform 没有"取文件修改时间"的函数。改用 `filesha1()` 取内容哈希，语义反而更准（内容真变了才重建）。
2. **`user_data_replace_on_change` 是 AWS 专有参数**，阿里云 provider 不认。改用 `lifecycle.ignore_changes`。
3. **输出引用敏感变量必须显式标注** `sensitive = true`，否则 `validate` 直接报错。

## 第二轮：本地容器部分

1. **destroy provisioner 不能引用 `var.*`** —— Terraform 硬性规则：销毁阶段的 provisioner 只能引用 `self` / `count.index` / `each.key`。原本在清理脚本里写了 `${var.ssh_port}`，直接编译失败。解决办法是把 `ssh_port`、`ssh_key`、`ssh_user`、`down_on_destroy` 全部预先放进 `triggers`，销毁时从 `self.triggers.*` 读。

   这个限制比想象中更硬：**连 `interpreter` 参数里想放一个变量路径都不行。** 我试过 `interpreter = [var.bash_path, "-c"]`，同样报 `Invalid reference from destroy provisioner`。所以"用变量指定 bash 路径"这条路只解决了一半问题，干脆放弃。
2. **`set -e` 与 `[ ] && { }` 的交互** —— 已实测确认：值相等时 `[ ]` 返回非零，但因为是 `&&` 左侧，不会触发 `set -e` 退出，`NEED_RECREATE` 正确保持 `false`。幂等路径是通的。

## 第三轮：CI/CD 部分

1. **同一个 destroy 陷阱又踩了一次** —— `cicd.tf` 的销毁脚本引用了 `self.triggers.cleanup` 和 `self.triggers.harbor_pass`，但这两个 key 忘了放进 `triggers`。已修正。

   这个坑值得单独记住：**写 destroy provisioner 时，先把用到的 `self.triggers.*` 全部列出来，再回头逐个确认它们在 triggers 里有定义。** 顺序反过来（先写脚本再想 triggers）必然漏。
2. **渲染占位符不能用 `sed`，要用 `replace()`** —— `sed` 的替换串需要转义 `\`、`&`、`|`。Harbor 密码和 Jenkins Token 里完全可能出现这些字符，一旦出现就会**渲染错**，而且错得很隐蔽：脚本照常执行，只是密码是错的，报错信息指向认证失败，很难联想到是渲染问题。

   Terraform 的 `replace()` 是纯字面量替换，对任意字符（包括引号、斜杠、换行）都安全。代价是写法啰嗦一点，但值得。
   > **后来这个方案又被替换掉了。** `replace()` 本身没错，但"每个字段一个占位符"很难维护：  
   > 渲染 compose 要 10 个、渲染脚本要 7 个，还必须保证占位符之间不构成前缀关系，  
   > 否则短的会先把长的替换掉一半。现在改成**整份配置 `jsonencode` 后再 `base64encode`**，  
   > 每个脚本只剩一个 `__WF_CONFIG_B64__`（详见上文「配置注入：为什么用 base64 包一层」）。  
   > 教训是：`replace()` 只解决了「字符安全」，没解决「占位符数量爆炸」。

## 第四轮：GitLab 部分

1. **本地值（local）在整个模块内必须唯一** —— 我在 `containers.tf` 里新加了一个 `jenkins_script`，而 `cicd.tf` 里早有一个同名 local（那是 Jenkins「任务」的脚本）。`validate` 直接报 `Duplicate local value definition`。改名为 `jenkins_container_script`，后来整个文件重写后是 `jenkins_compose_script`。

   ⚠️ 顺带发现一个**更隐蔽的同名问题**：同一个 `triggers = {}` 对象里重复写同一个 key，Terraform 会报错，但如果你把 GitLab 的「容器 shell 端口」和「宿主机 SSH 端口」都命名成 `ssh_port`，那就真的会在覆盖后悄悄失效——**这次是 validate 帮我抓住了**。涉及两台机器时，命名字段一定要带上下文。
2. **HCL 里三元表达式换行必须加括号** —— 写 `x = cond` 换行再写 `? a : b` 会直接编译失败，报 `Invalid expression`。

   原因：HCL 遇到换行就认为一个完整表达式结束了，下一行开头的 `?` 成了非法 token。**必须写成 `x = (cond ? a : b)` 或把整个条件用括号包起来。** 这是很容易反复踩的一条，已在代码注释里标注。
3. **`set -u` 下的空数组展开** —— 用条件拼 `docker run` 参数时，`"${ARR[@]}"` 在旧版 bash 下遇到空数组会报 `unbound variable`。改用 `${ARR[@]+"${ARR[@]}"}` 这种写法，两种版本都安全。

## 第五轮：拿到真实 compose 文件之后

这一轮的问题**全部是"代码没错、默认值错了"**——最危险的一类。语法检查、`-validate`、甚至 `plan` 都是绿的，只有真跑到机器上才会出事。

1. **`host_port_of` 的参数语义搞反了** —— 这个函数用 `docker inspect` 取容器的宿主机映射端口，它的参数必须是**容器内端口**（`8080`），我却传了**宿主机端口**（`8081`）。

   为什么之前一直没暴露：最初的默认值里宿主机和容器端口**都是 8080**，两者相等，传哪个都对。改成真实的 `"8081:8080"` 之后，函数拿 `8081` 去匹配容器端口 `8080`，永远匹配不到，返回空——于是脚本会一直认为"端口漂移了"，每次 apply 都触发重建。

   修正后调用是 `host_port_of 8080` 和 `host_port_of 50000`，并在函数上加了一行注释写明参数是容器端口。**教训：当两个值的语义容易混淆时，函数名或注释里必须点明单位/视角，否则默认值一旦变化就会静默出错。**
2. **HCL 模板插值和 shell 参数展开的字面冲突** —— 脚本里写 `${CFGFILE:-（标签为空）}` 和 `${CONFIG_HASH:0:12}`，`validate` 直接报 `Template interpolation doesn't expect a colon at this location`。

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
3. **旧的 `docker run` 方案会和 compose "抢对象"** —— 这不是编译错误，是设计错误，但危害最大。原方案要 `docker run` 一个名为 `jenkins` 的容器；而机器上那个 `jenkins` 是 compose 管的、占着 `8081` 和 `50000`、并挂了 6 个宿主资源路径。

   真跑下去的结果有且只有两种：要么因端口冲突起不来，要么侥幸起来但**那些挂载全丢**（`docker.sock`、`k3s`、`kubectl`、`rancher/k3s`、`.kube`、`/usr/bin/docker`），后者更糟——Jenkins 里的 k8s 部署 stage 会在某个深夜突然失败。

   修正方式是把「创建容器」整体换成「核对编排栈」，并把旧脚本 `scripts/jenkins.sh` **删除**，换成 `scripts/jenkins-compose.sh`（后来改写成 `scripts/jenkins-compose.py`）。**同一份声明式配置（compose 文件）不应该被两个工具同时声明。**

## 第六轮：shell 改写成 Python

这一轮不是"修 bug"，是主动重构。改写过程中又抓出 3 个真问题，其中 1 个是**一直藏在 shell 版本里的真实缺陷**。

1. **`harbor_url` 少了 `/api/v2.0`，而且连通性检查是假的** —— 这是**顺带抓出来的老 bug，不是重写引入的**。

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
2. **`templatefile()` 会解析注释里的 `${...}`** —— 给 compose 模板写的头部注释里正好出现了 `${name}`（想说明"这个文件叫什么"），`validate` 直接报：
   ```
   templates/jenkins-compose.yaml.tpl:11,8-11: Invalid expression;
   Expected the start of an expression, but found an invalid expression token.
   ```
   模板引擎不区分"注释"和"正文"，只看 `${`。**模板里任何字面量 `${...}`（包括注释、文档字符串、shell 的 `${VAR}`）都必须写成 `$${...}`。** 这条和第五轮第 12 条是同一类坑的两个面：那边是 shell 片段嵌进 HCL，这边是任意文本进模板 —— 触发条件都是"文本里出现了 `${`"。
3. **把已 `jsonencode` 的字符串当对象嵌套 → 会发出错误的请求体** —— 这条最隐蔽，**编译期完全静默**。

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

## 第七轮：加入 Ansible 批量管理层

这一轮的目标是「批量管云服务器和本地服务器，但本地服务器不覆盖」。  
按老规矩，先列出的都是**容易踩的坑** —— 其中第 19、24、25、26、28 条是这一轮  
（离线测试 + 真机验证）真的抓出来的问题。

1. **「不改本地机器」不能靠约定，要靠结构。** 最容易写出来的方案是"跑的时候记得加 `--limit`"  
   或者"在这个任务上写个 `when`" —— 两者都是流程约定，都会忘。最后落成三层：  
   **结构性隔离**（会改东西的 role 只出现在 `hosts: cloud` 的幕里）+  
   **运行前断言**（`guard` 在变更型幕里硬断言，防的是第一层被绕过）+  
   **逐任务许可**（"新增"不带条件、"覆盖"必须带 `wf_can_write`）。  
   关键在于三层的失效模式不同：忘写 `when` 会被第一层挡住，改错 `hosts` 会被第二层挡住，  
   而第一层根本不依赖任何人记得什么。
2. **断言失败要给出"下一步怎么做"，不能只说"不行"。** `guard` 的 `fail_msg` 里直接写了三条命令：  
   先只读体检、再 `--check --diff` 干跑、最后才真改。  
   如果只报一句 `assertion failed`，用的人下一步一定是去搜"怎么关掉这个断言" ——  
   那保护就白做了。
3. **★ 更危险的能力必须蕴含较弱的能力 —— 这是本轮真抓出来的问题。** 原来 `wf_can_write` 的公式是  
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


## 第八轮：守住分层边界


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


## 第九轮：CI/CD 成为唯一触发器 + 跨层衔接声明化


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



## 第十轮：宿主机边界 + 豁免登记


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

## 第十一轮：真机联调（Terraform 交出去之后）

前十轮全部在「验静态」和「验本机」的范围内。这一轮是第一次真的按文档从
`terraform apply` 一路跑到 `ansible-playbook site.yml --limit cloud` ——
**于是暴露了一批只有在两台不同来源的机器之间才出现的问题**：Windows 上 checkout 的行尾、
云镜像自带的解释器、安全组与容器端口的两处声明、以及 `destroy` 之后仍然活着的本地状态。

这四条的共同点：**它们都不在这四个检查器（B1~B9、guard、selftest）的管辖范围内**，
因为检查器读的是仓库，而这些问题的输入来自仓库之外 —— 操作系统、网络、以及上一轮跑过什么。

42. **CRLF 行尾会让 `local-exec` 里整段 bash 语法错乱。**

    `terraform apply` 在盘点资源上失败，报错三连：

    ```
    bash: line 1: set: pipefail: invalid option name
    bash: line 3: $'\r': command not found
    bash: -c: line 5: syntax error near unexpected token `"agent:192.168.193.139:..."'
    ```

    看着像脚本写错了，其实每一个 `\r` 都被 bash 当成了一个字符：
    `set -uo pipefail\r` 里 `\r` 挂在选项名尾上（于是报「无效选项名」），
    引号对里多出的 `\r` 又把下一行的字符串拼接到了一起（于是「语法错误」）。

    根因不在脚本，在 checkout：仓库索引里 18 个文件全是 LF，但 Windows 上
    `core.autocrlf=true` 会在**取出来的时候**转成 CRLF。`git diff` 看不出任何差异
    （因为比较的是走过同一套转换的内容），而 `.tf` 里内嵌的 bash、`ansible` 模板里
    内嵌的 shell 都是**拿工作区文件直接喂给解释器**的，于是这些文件的行尾变成了语法的一部分。

    修法分两层：先清工作区（`sed -i 's/\r$//'`，再用 `git add --renormalize` 证明与索引零差异），
    再从根上补 `.gitattributes`：

    ```
    * text=auto eol=lf
    ```

    验证方式是把文件删掉重新 `checkout`，确认取出来的就是 LF —— 只改工作区不补规则，
    下一个人 clone 下来照样撞。

    **规律：凡是「一种语言的源码嵌在另一种语言的文件里」（`.tf` 里的 bash、模板里的 shell），
    行尾就不是格式问题，而是语法问题。这类文件必须由仓库自己钉住，不能依赖每个人的本地配置。**

43. **★ 镜像的系统 Python 决定了谁能管这台机器。**

    第一次真机跑 Ansible，第一台机器在 `gather_facts` 就失败：

    ```
    ansible.legacy.setup: Module result deserialization failed: No start of json char found
    module_stderr: File "<stdin>", line 3
                  SyntaxError: future feature annotations is not defined
    ```

    这条报错读起来像「模块传输坏了」（`No start of json char found`），实际是
    **被管机的 Python 太旧**：模块载荷第 3 行是 `from __future__ import annotations`，
    那是 3.7 才有的语法。目标镜像是 Alibaba Cloud Linux 3，`/usr/bin/python3` 是 **3.6**，
    而且**不能升级** —— `dnf` 自己的脚本就挂在这个解释器上。

    这一类问题最典型的特征是：**报错信息指向的地方不是原因**。
    它报的是"模块结果解析失败"，指向 Ansible 的模块机制；
    真正的原因在镜像的 `image_id` 里，而那个值在仓库的另一个文件里，看起来只是"一个字符串"。

    第一版修法是钉死 `ansible-core<2.17`（最后一个支持 3.6 目标的系列）—— 但那是治标：
    2.16 已 EOL，控制端还被限制在 Python 3.10~3.12。真正的修法是换镜像：
    默认改成 Ubuntu 24.04（系统 Python 3.12），把 `image_id` 写死改成用
    `data "alicloud_images"` 按发行版族查最新构建，ALinux3 退化成 `image_os` 的一个可选项。

    顺带一条：**`ignore_changes = [image_id]` 在这里是错的。** 它本来是"别因为改个镜像就重建"，
    但换镜像就是换操作系统 —— 保留它只会得到一台"配置说 Ubuntu、实际还是 ALinux3"的机器，
    然后在同一个地方再失败一次。所以 `ignore_changes` 收窄成 `[user_data]`。

    **规律：镜像不是配置项，是基础设施的一部分 —— 它决定了后续所有工具链的版本下限。
    一个 `image_id` 字符串能决定「这台机器归谁管、用什么版本管」，而这件事在
    validate、plan、四个检查器里都看不出来，只有真连上去才现形。**

44. **安全组只开了声明过的端口，容器映射的端口没开 —— 验收连不进去。**

    安全组建了四条规则：22 / 80 / 443 / ICMP，都是"一个 Web 服务该有的端口"。
    但 `app_stack` 把宿主机 **8080** 映射到容器 80（刻意避开 80，因为系统的 nginx 已经占着）。
    于是 `terraform apply` 成功、`ansible-playbook` 全部 green、`docker ps` 两个容器 Up，
    而浏览器打不开 —— `curl` 是超时，不是拒绝连接。

    两条声明各自都正确，问题在它们之间：**「谁声明端口」和「谁放行端口」在两个
    Terraform/Ansible 的分层里，读任何一边都看不出缺了哪一条。**

    修法是把端口做成变量（`var.app_stack_port`，默认 8080），让安全组规则和 role 的默认值
    引用同一个数。这样它从"两处巧合对上"变成"一处定义、两处使用"。

    **规律：端口、路径、用户这类"两边都要写一遍"的常量，只要有一边是硬编码，
    就一定会在某次改动后对不上。而它失败时的现象（超时）离原因（少一条规则）很远。**

45. **★ `known_hosts` 是唯一跨 `destroy` 存活的遗留物。**

    换完镜像重建实例，`ansible-playbook site.yml --limit cloud` 在第一幕就停：

    ```
    @    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
    Offending ECDSA key in /root/.ssh/known_hosts:10
    ssh-keygen -f '/root/.ssh/known_hosts' -R '121.196.148.245'
    Host key verification failed.
    ```

    原因是阿里云的公网 IP 会**回收再分配**：`destroy` 之后重建，新机器大概率拿到同一个 IP，
    而控制端的 `known_hosts` 里还留着上一台机器的键。这是 `accept-new` 在正常工作 ——
    没见过的机器自动接受，见过的机器换了键就拒绝。

    三件事让它特别容易误判：

    - **报错文本把人往安全事件带**（MITM 警告），第一反应是"我是不是被劫持了"，而不是"IP 被复用了"
    - **它在 `destroy` 时完全不可见**：回收清单里原来只有 `hosts.cloud.yml` 和 `tf-outputs.json`，
      两样都是"删了就干净"的文件，而这条记录躺在 `~/.ssh/` 里，不属于任何一层
    - **它在下一次 `apply` 之后才发作**，中间隔着一整个建机过程

    所以修法不是关校验（改成 `StrictHostKeyChecking=no` 等于放弃对新机器的校验，而云上
    "同一个 IP 背后换了台机器"恰恰是常态），而是**回收时把它列进清单**：

    ```bash
    ssh-keygen -f ~/.ssh/known_hosts -R <该 IP>
    ```

    顺带记一个配置语义的坑：`ansible.cfg` 里同时写了 `host_key_checking = False`
    和 `ssh_args = ... -o StrictHostKeyChecking=accept-new`。**`-o` 是最后拼进命令行的，
    后者胜出** —— 所以文件里那句 `False` 从来没生效过。这次是幸运（保护其实开着），
    但"两处配置互相覆盖、生效的是哪一个要靠猜"本身就是缺陷，
    已在 `ansible.cfg` 的注释里写明。

    **补充（2026-10-27）：这一步已经自动化，不再依赖人记得。**
    蓝绿灰度每发布一次就 `destroy` 一台、`create` 一台，所以"IP 被复用"不是偶发，
    而是每次的常态 —— 靠文档提醒"回收时记得 `ssh-keygen -R`"必然漏。
    现在 `tools/gen_cloud_inventory.py` 在**生成这批 IP 的清单的同一刻**把它们在
    `known_hosts` 里的旧键删掉（`--no-purge-known-hosts` 可关），
    清单之外的主机一律不动，校验仍是 `accept-new`。
    两个细节：① 要认得出哈希写法（`|1|salt|hmac`，Debian/Ubuntu 默认开
    `HashKnownHosts`，纯文本匹配会"清了个寂寞"）；② 只删本次清单里的地址，
    不做任何 `StrictHostKeyChecking=no` 式的放宽。

    **规律：回收清单要按「谁还记着这台机器」来列，不能按「Terraform 建了什么」来列。
    只要一个地址会被复用，那么上一轮的记录就会变成下一轮的故障 —— 而且它不在任何一层里。
    进一步说：这种"每轮都会发生"的遗留物，一旦确认了处理方式，就该钉进代码，
    留在文档里等于留给下一个人重新踩一遍。**

---

## 第十二轮：把入口收成一个

46. **★ 同一台机器开了两个对外端口，等于有了两个「首页」。**

    云机器起来后有两副面孔：`http://IP/` 是 `user_data` 写的静态欢迎页，
    `http://IP:8080/` 才是真正的应用。两个地址都打得开、都"看着正常"，
    于是验收时说不清哪一个才算数；改完之后还要记得两边都看一遍。
    上一轮为了让 8080 能访问，还专门给安全组加了一条规则（踩坑 44）——
    等于把一个**内部**端口升格成了第二个公网入口。

    根子不在端口号，在**没有人回答「这台机器对外长什么样」**：
    80 归 Terraform 的启动脚本，8080 归 Ansible 的 role，
    两边各自都正确，合起来却没有一个统一的入口。

    修法是让容器退回内部：compose 只绑 `127.0.0.1:8080`，
    系统 nginx 的 80 反代过去，安全组里删掉那条 8080。
    对外的只有一个地址 —— 而且 `apply` 刚结束、Ansible 还没跑时，80 仍然有响应：
    反代失败会落到一张「机器在、应用未就绪」的待部署页，存活信号没有丢。

    反代配置放在 Ansible 而不是 `user_data`，也是这条边界的一部分：
    Terraform 管资源是否存在，Ansible 管机器内部服务怎么配。
    写进 `user_data` 的话，`ignore_changes = [user_data]` 会让它对存量机器**不生效**（踩坑 43 的同一个陷阱）。

    **规律：对外的端口数是一个产品决策，不是配置细节。**
    每多一个对外端口，就多一份「哪个才是真的」的歧义；
    而歧义不会在 `validate`、`plan` 或任何一个检查器里报错 —— 它只在人看图的时候出现。
    判据很短：**不需要被外面访问的端口，就不该出现在安全组里。**

---

## 第十三轮：镜像不是「顺手就能拉到」的

47. **★ `docker compose up -d` 里拉不到镜像 —— 报错说的是网络，坏掉的是整幕。**

    云机器刚建好，`ansible-playbook playbooks/site.yml --limit cloud` 在 D 幕停住：

    ```
    fatal: [demo-cnhangzhou-ecs-1]: FAILED! => {
      "changed": true,
      "cmd": ["docker","compose","-f","/opt/wf/stacks/wf-web/docker-compose.yml","up","-d"],
      "delta": "0:00:30.078309",
      "rc": 1,
      "stderr": " wf-web Pulling \n wf-web Error failed to resolve reference
        \"docker.io/library/nginx:alpine\": ... dial tcp 122.248.226.57:443: i/o timeout"
    }
    ```

    三个问题同时存在，而且互相掩盖：

    - **位置错了。** 拉镜像是「网络能不能拿到物料」，`up -d` 是「拿到的物料怎么编排」。
      塞进同一个命令里，网络问题就会以编排失败的样子出现 —— 第一反应是去改 compose。
    - **报错没有出口。** dockerd 原文只说 dial timeout，不说"下一步做什么"；
      而且它花 30 秒才吐出来，还挂着 `changed: true`（`changed_when: true` 在判定失败之前就生效了）。
    - **拖垮了下游。** 这个 task 一失败，同一个 block 后面的任务全都不执行 ——
      包括「把 80 配好、给一张待部署页」那段。于是镜像拉不到的结果是：
      80 回到 `user_data` 的欢迎页，人看到的是"这台机器好像没配上"。

    修法分三块，一块对一条：

    ① **把「拿镜像」拆成独立任务**（探测 → 离线导入 / 远程拉取 → 判定），
       `up -d` 只负责编排，并以「镜像已就位」为硬门槛；
    ② **拉取外面套 `timeout`**，给一个硬上限；失败不当场 raise，
       而是把真实 stderr 和各镜像站的可达性探测一起留给最后那条失败信息；
    ③ **失败留到最后统一收场**：80 的配置照跑（给待部署页），
       全部走完再 `fail`，信息里写清三条路（加速器 / 离线包 / 换镜像）。

    顺带修掉一个同源的坑：`daemon.json` 里写了 `registry-mirrors` 却没重启 docker，
    于是"我明明配了加速器，怎么还是 i/o timeout"。现在 `docker_engine` 写完会
    `flush_handlers` 重启，**再问守护进程本身**（`docker info` 的 Registry Mirrors 段），
    看不到就红 —— 配置文件是"我以为的状态"，`docker info` 才是"它实际的样子"。

    **规律：一条命令里混了两件事（拿物料 / 用物料），它的失败就不可能被正确归因。**
    拆开的成本是一个 task，不拆的成本是每次失败都要重新猜一遍。
    另外，「写进配置文件」和「进程真的用上了」之间隔着一次重启 ——
    凡是只在启动时读配置的服务，都得有一条"问它自己怎么想"的断言。

---

## 第十四轮：在真机上验失败路径（不是"应该会这样"）

48. **★ 报错里的行号对不上 —— 你在排错的那份代码不是跑的那份。**

    改完镜像前置检查，真机再跑，报的还是老样子，而且行号指向 `main.yml:96:7`：

    ```
    Origin: /data/terraform-demo/ansible/roles/app_stack/tasks/main.yml:96:7
    94     # ★ 只在真的有理由时才动手：
    95     #   文件变了（含首页） 或 栈里一个容器都没在跑
    96     - name: "app_stack · 拉起容器栈"
    ```

    而本地这一版第 96 行早就不是这段了（`拉起容器栈` 挪到了 200 行，前面多了 80 行镜像预检）。
    原因很朴素：**代码写在一台机器上，playbook 跑在另一台机器上，中间没有同步**。
    服务器上 `/data/terraform-demo` 还停在上一轮的版本，
    `grep 镜像 ansible/roles/app_stack/tasks/main.yml` 一个字都搜不到 —— 这一条就能直接判死。

    教训有两层：

    - **先确认"跑的到底是哪份代码"。** 报错行号、`grep` 关键词、`sha1sum` 三者任一不符，
      就不要再读报错内容了 —— 读的是历史。最快的自查是在报错文件里搜自己刚加的那行注释，
      搜不到就是没同步。
    - **同步本身也要验。** 推完立刻在目标机 `sha1sum` 一下同样的文件，两侧一致再开跑。
      （两侧结尾符不同会让 sha1 不等 —— 推之前统一成 LF，比事后猜"为什么字节数差一个"省事。）

    **规律：跨机器调试时，"当前代码是什么"是一个需要主动验证的假设，不是默认前提。**

49. **★ 断言写成「5xx 才算故障」，把「降级但仍在服务」判成了故障。**

    镜像没拿到时的自检本来只接受 `[502, 503, 504]`：

    ```yaml
    - ansible.builtin.uri:
        url: "http://127.0.0.1/"
        status_code: [502, 503, 504]
    ```

    但"镜像没拿到"不等于"80 一定 5xx" —— **旧容器还在跑**（新的起不来，旧的不会自己停）。
    这时 80 老老实实返回 200，uri 模块当场报 `Status code was 200 and not [502, 503, 504]`，
    整幕就断在这条自检上，后面那条真正的失败原因（镜像没拿到）根本没机会说出口。

    修法是把这个检查改成"接受降级"：

    ```yaml
    status_code: [200, 502, 503, 504]
    failed_when: false
    # 断言里：200 = 旧容器在服务（说得过去）；5xx = 必须真的是待部署页
    that:
      - (wf_front_pending.status | default(0)) == 200
        or '应用还没起来' in (wf_front_pending.content | default(''))
    ```

    于是两条分支各自有话说：
    容器停了 → `80 返回 502，给的是待部署页（容器没起来）`；
    旧容器还在 → `80 返回 200，是旧容器在服务（本次镜像没拿到，容器没更新）`。

    **规律：断言要覆盖"所有说得过去的状态"，而不是只覆盖"我预想的那一种失败"。**
    预期内的分支一旦让模块自己报错，它的报错就会盖掉真正的原因 —— 这和 47 是同一个病：
    一个位置只能有一个声音。

---

## 第十五轮：发布与回滚（"回滚"到底回的是什么）

50. **★ 回滚 ≠ 重跑一遍发布。**

    最自然的写法是：回滚时把版本号传回去，再跑一次 playbook。

    ```bash
    ansible-playbook playbooks/site.yml --limit cloud -e wf_release=v1
    ```

    这条命令看起来完全正确，实际上做的是**用当前模板重新渲染一遍**。
    而"需要回滚"的典型场景恰恰是模板改坏了 —— 于是它渲染出来的是
    **旧版本号 + 新内容**：页面上写着 v1，内容和 v1 一点关系都没有。
    这比不回滚更糟，因为它把"现在跑的是哪一版"这条信息也弄坏了。

    回滚只能发生在**产物**上。所以发布时先把渲染结果落成一份不可变产物：

    ```
    releases/<版本>/index.html   ← 那次发布留下的原件
    public/index.html            ← 线上真正被读的那份（产物的拷贝）
    current                      ← 现在生效的是哪一版
    history                      ← 发布过哪些版本，顺序即发布顺序
    ```

    回滚 = 挑版本 → 拷产物 → 从 80 读回版本标记。全程**不出现 template 模块**，
    这条现在是自测里的一条硬断言（`[5c]`），改坏了会红。

    **规律：凡是"回到之前某个状态"的需求，都得先有那个状态的可寻址副本。
    「重新生成一次」在生成逻辑没变时才等价于回滚 —— 而逻辑变了才是要回滚的时候。**

51. **★ 单文件 bind mount 把 inode 钉死了，页面换不掉还不报错。**

    compose 原来挂的是单个文件：

    ```yaml
    volumes:
      - /opt/wf/stacks/wf-web/index.html:/usr/share/nginx/html/index.html:ro
    ```

    `docker compose up` 创建容器时，这个路径就被解析成了宿主机上的具体 inode。
    之后即使把 `index.html` 换成新内容（`copy` 写临时文件再 rename，inode 变了），
    容器里读到的还是旧 inode —— **页面永远停在上一版，没有任何报错**。
    `docker exec cat` 看到的是新内容、`curl` 出来是旧内容，这种不对称最难查。

    改成挂目录：

    ```yaml
    volumes:
      - /opt/wf/stacks/wf-web/public:/usr/share/nginx/html:ro
    ```

    目录挂载按路径解析，换目录里的文件立刻生效，**也不用重建容器** ——
    发布和回滚因此可以做到"不停容器换内容"。自测里有一条断言专门钉它的形状
    （挂的是 `public:` 目录，且 volumes 里不能出现 `index.html`）。

    **规律：bind mount 的粒度就是"变更粒度"。要换内容，就挂到能装下变化的那一层。**

52. **`roles:` 列表的 dict 写法会静默忽略 `tasks_from`，回滚变成"又发布一次"。**

    想在入口里复用已有 role 的另一个任务文件，第一反应是这么写：

    ```yaml
    - hosts: cloud
      roles:
        - guard
        - role: app_stack        # ← 看起来很清楚
          tasks_from: rollback   # ← 这一行 Ansible 不看
    ```

    结果它老老实实跑了 `app_stack/tasks/main.yml`。**没有任何警告、没有 lint 报错**，
    只是"回滚"执行完，页面上的版本号变成了最新版 —— 而且还是"变最新一次"，
    所以不看 history 根本发现不了。

    原因：`roles:` 的 dict 写法只认 `role` / `vars` / `when` / `tags` 这几个键，
    `tasks_from` 不在其中，被直接丢掉。真正支持 `tasks_from` 的是
    **`tasks:` 里的 `import_role` / `include_role`**：

    ```yaml
      roles:
        - guard
      tasks:
        - ansible.builtin.import_role:
            name: app_stack
            tasks_from: rollback
    ```

    `import_role` 和 `include_role` 的取舍：这里要 `import_role`（静态展开），
    因为它给出了 `--list-tasks` 里能看见的完整任务列表；
    `include_role` 是运行时才展开，看上去更灵活，但出问题时 `-vvv` 之外什么线索都没有。

    **规律：能"被忽略"的配置项比报错的配置项危险得多。复用的入口只有一个，
    写完后要有一个形状断言钉住它 —— 现在是自测里的两条：`tasks` 里必须有
    `app_stack/rollback`，且 `roles:` 列表里不许出现 dict。**

53. **★ 静态护栏不跟进 `import_role`，`tasks_from` 指向的那份文件对检查器不存在。**

    `tools/check_guard.py` 遍历 role 的任务时，原本只跟进 `include_tasks` /
    `import_tasks`。但上面第 52 条之后，入口长成了 `tasks: [{import_role: ...}]` ——
    于是 `roles/app_stack/tasks/rollback.yml` 里的两个 `copy`
    （把产物拷回 `public/`、改 `current` 指针）**一次都没进过 P6 的视野**：
    往那里再加写操作、或者把这个 play 改成 `hosts: local`，静态检查都是绿的。

    同一个盲区还有一层：`check_playbook` 从 `play["roles"]` 取 role 列表，
    `tasks:` 里的 `import_role` 不算「这个 play 引用了它」。所以判
    "解锁型 play 有没有引用 cloud-only role" 时，`app_stack` 根本不在候选里。

    修法就是让检查器的视野和 Ansible 的**实际执行路径**对齐，而不是和
    "我们以为的写法"对齐：

    ```python
    if short_module(module) in ("include_role", "import_role") and isinstance(args, dict):
        target_role = str(args.get("name", "")).strip()
        tasks_from = str(args.get("tasks_from", "main")).strip() or "main"
        if target_role and tasks_from != "main":   # main 已由按 role 的遍历覆盖
            walk(root / "roles" / target_role / "tasks" / f"{tasks_from}.yml", own_gates, seen)
    ```

    **规律：校验器一旦落后于写法一次，它就不再是一个保护。每引入一种新的
    任务组织方式（include / roles_dict / tasks_from），都要回头问一句：
    静态检查的眼睛跟到那里了吗？—— 这次两条新自证用例就是答案的证据。**

## 第十七轮：在 147 上第一次真跑 bootstrap（2026-10-26）

### 第 54 条：glrt- token 注册 runner，配置参数会直接 FATAL

**现象**：`gitlab-runner register --token glrt-… --tag-list wf-infra --run-untagged=false`
报 `FATAL: Runner configuration other than name and executor configuration is reserved …
cannot be specified when registering with a runner authentication token`。

**原因**：GitLab 新版 runner 创建流程里有两种 token，权限模型完全不同：

| | 旧式 registration token | 新式 authentication token（glrt-） |
|---|---|---|
| tag / locked / run-untagged 等在哪定 | 注册命令行 | **GitLab 服务端**（建 runner 的地方 / API） |
| register 能带哪些参数 | 全部 | 只有 name / executor / url / token |

我们的 runner 是走 API 建的（`POST /user/runners`，tag_list 已在服务端设好），
所以拿到的必然是 glrt- —— 注册命令里那三个参数不是"多余"，是**不允许**。

**修法**：install_runner.sh 按 token 前缀分流，glrt- 只传 url/token/executor/shell/description。

**规律：同一个工具的同一个命令，token 类型一换，合法参数集就变。
报错文本把"哪些参数被保留"列得很全，但只有真的撞过才知道它是终结性的。**

### 第 55 条：alicloud provider 的 versioning 块用 status，不是 enable

**现象**：`alicloud_oss_bucket` 里写 `versioning { enable = true }`，
plan 直接报两条错：`The argument "status" is required` + `An argument named "enable" is not expected`。

**原因**：alicloud provider 某个大版本把 versioning 块的布尔开关改成了
`status = "Enabled" | "Suspended"`（对齐 AWS S3 的写法）。网上大量教程还是旧写法，
照抄就在新 provider 上翻车 —— 而我们锁了 `>= 1.210`，必然是新写法。

**修法**：`versioning { status = "Enabled" }`。

**规律：Terraform 的错误定位给到行号时是最便宜的错；贵的是 provider 在两个大版本间
**改了参数名**而不报 deprecated —— validate 阶段能拦住的错，永远不要让它流到 147 上才炸。
（本地有 provider 缓存的话，`terraform validate` 半分钟就能验完这段。）**

### 第 56 条：OSS 桶名是跨账号全局唯一的，"名字被占"不等于"我没有"

**现象**：`alicloud_oss_bucket` 报
`The specified bucket name: "cloud-native-demo" is not available. The bucket namespace is shared by all users of the OSS system.`

**原因**：OSS 的桶名空间全站共享（所有账号、所有 region 一个池子），
`CreateBucket` 对**任何已存在的桶**都返回这句 —— 包括**你自己账号里已经建过的那个**。
所以这条报错有两种完全不同的含义，处理方式相反：

| 桶属于谁 | 正确处理 |
|---|---|
| 自己账号（之前手工建过） | `terraform import` 接管，别重建（重建必然失败） |
| 别人 / 另一个 region | 换一个带后缀的名字，四处同步 |

**修法**：bootstrap 里先探一次再决定
（`https://<bucket>.oss-<region>.aliyuncs.com`：403 = 存在且私有，404 = 不存在）：

```bash
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
        "https://${BUCKET}.oss-${REGION}.aliyuncs.com" || true)"
if [ "${CODE}" = "403" ]; then
  terraform import -input=false alicloud_oss_bucket.tfstate "${BUCKET}" || {
    echo "不属于本账号，换名"; exit 1; }
fi
terraform apply -auto-approve -input=false   # 再把版本化/加密对齐
```

**实测（2026-10-27，147 上）**：`cloud-native-demo` 在 cn-hangzhou 返回 403，
`terraform import` 也失败。当时的实现把 import 的 stderr 丢进了 `/dev/null`，
于是只看到一行"import 失败"，既不知道是权限、region 还是归属 ——
就顺手下了"这个桶不属于本账号"的结论并改名。**这个结论下早了**：
403 是匿名请求的语义（私有桶一律 403），只能说明"桶存在"，**看不出 owner**；
而 import 失败同样可能是 RAM 子用户对该桶没权限。两者现象一样，处理方式相反。
桶名后来改回 `cloud-native-demo`（使用者指定），脚本只保留一条原则：**按原话判读，不猜**。

```
桶已存在（HTTP 403：私有但存在），import 接管而不是重建
✗ import 失败 —— 这个桶不属于本账号 ...
```

**所以 import 分支必须二件事一起做**：① 把 terraform 的原话打出来（`2>&1` 存变量再 `sed 's/^/    /'`）；
② 失败时的指引要能直接照做 —— 四处同步改名字 + 用 curl 验新名字是 404。
失败指引按报错分四支（AccessDenied/Forbidden.RAM → 补子用户权限；does not belong to you /
BucketAlreadyExists → 才换名；NoSuchBucket / must be addressed using... → region 不对；
resource address ... does not exist → 资源名写错），并给两条自查：
`aliyun sts GetCallerIdentity` 看这对 AK 是谁、`aliyun oss ls oss://<bucket>/` 看碰不碰得到。

**规律：全局命名空间的资源，"创建失败"这条路径上必须有一个 import 分支。
不然每次报错都在逼你做选择题，而选择题的两个答案往往都是错的。
而且 import 失败时**永远不要吞掉 stderr** —— 那条错误信息是唯一能区分"权限不够 / region 不对 / 不是你的"的东西。**

## 第十九轮：镜像仓库 —— 「可达」不等于「拉得到」

### 第 57 条：`/v2/` 返回 401 不是坏消息，`000` 之下还藏着一个假象

**现象**：`gray:configure` 阶段，机器已经配好、80 有响应，但容器起不来：

```
Error response from daemon: failed to resolve reference "docker.io/library/nginx:alpine":
  failed to do request: Head "https://registry-1.docker.io/v2/library/nginx/manifests/alpine":
  dial tcp 104.244.46.186:443: i/o timeout
```

而 role 自己探测镜像站的结果长这样：

```
docker.m.daocloud.io → 401
docker.1ms.run       → 401
registry-1.docker.io → 000
```

**两个误读**：

1. **把 401 当成"这个站不能用"**。401 是 registry 协议的正常应答 ——
   `/v2/` 本来就会回 `WWW-Authenticate: Bearer realm=...`，客户端拿 token 再请求。
   拉**公开镜像**全程都是匿名的，但匿名不等于不需要 token。
   所以「401」的真实含义是"这是个能说话的 registry"，而不是"我没权限"。

2. **把 `docker info` 里的 Registry Mirrors 当成"已经生效"**。
   `daemon.json` 只在 dockerd **启动时**读一次。而 `docker_engine` 里那句
   `systemctl restart docker` 是 `failed_when: false`（不能让重启失败把整幕带走），
   于是存在夹缝：文件里写了镜像站、`docker info` 复核也看到了，
   pull 却仍旧走老路 —— 因为守护进程根本没换过配置。

**怎么验才算验过**：不要停在 `/v2/` 的状态码上，走完完整的认证链路再下结论：

```
GET  /v2/                                    -> 401 + WWW-Authenticate: Bearer realm=...
GET  <realm>?service=..&scope=repository:library/nginx:pull  -> 200 + token
GET  /v2/library/nginx/manifests/alpine      + Authorization: Bearer <token>
                                             -> 200 = 真的能拉到
```

2026-10-27 实测（从外部跑这段）：两个第三方站 `manifest=200`、16 个架构；
阿里云公共仓库 `registry.cn-hangzhou.aliyuncs.com` 对 `library/nginx` 回 401 ——
**同一个状态码，一个是"能拉"，一个是"没有"。只看状态码分不出来。**

**修法（两层，互不依赖）**：

1. 在 `group_vars/cloud.yml` 里**声明**镜像站（`wf_docker_registry_mirrors`），
   `docker_engine` 写 `daemon.json` 并重启 docker，再回读 `docker info` 复核；
2. `app_stack` 在直连失败后再走一条兜底：把仓库名换成镜像站前缀去拉
   （`nginx:alpine` → `docker.m.daocloud.io/library/nginx:alpine`），
   拉下来 `docker tag` 回原名，compose 文件一个字不改。
   这条**不依赖守护进程配置**，正好补上第 2 点那个夹缝。

兜底只在**已声明**的镜像站之间试 —— 用谁的机器拉镜像是使用者写在配置里的决定，
不该由 role 自动发现。第一段带 `.` 或 `:` 的镜像（别的 registry）直接跳过，
改写过去只会 404。

**规律：可达性探测能给的结论非常弱 —— 一个 200/401 只说明"TCP 通了、有 HTTP 服务在说话"，
不说明"我要的东西在它这儿"。要下结论就验到真实产物（manifest / blobs），
否则那份"探测结果"会把人带到错误的岔路上，比没有更糟。**

## 这 57 条怎么分布

| 轮次 | 主题 | 条目 | 条数 |
|---|---|---|---|
| 第一轮 | 阿里云部分 | 1–3 | 3 |
| 第二轮 | 本地容器部分 | 4–5 | 2 |
| 第三轮 | CI/CD 部分 | 6–7 | 2 |
| 第四轮 | GitLab 部分 | 8–10 | 3 |
| 第五轮 | 拿到真实 compose 文件之后 | 11–13 | 3 |
| 第六轮 | shell 改写成 Python | 14–16 | 3 |
| 第七轮 | 加入 Ansible 批量管理层 | 17–28 | 12 |
| 第八轮 | 守住分层边界 | 29–31 | 3 |
| 第九轮 | CI/CD 成为唯一触发器 + 跨层衔接声明化 | 32–36 | 5 |
| 第十轮 | 宿主机边界 + 豁免登记 | 37–41 | 5 |
| 第十一轮 | 真机联调（Terraform 交出去之后） | 42–45 | 4 |
| 第十二轮 | 入口收敛：80 反代容器栈 | 46 | 1 |
| 第十三轮 | 镜像就位：拉不到时不装死 | 47 | 1 |
| 第十四轮 | 真机验失败路径 | 48–49 | 2 |
| 第十五轮 | 发布与回滚 | 50–51 | 2 |
| 第十六轮 | 流水线驱动发布/回滚 | 52–53 | 2 |
| 第十七轮 | 147 真跑 bootstrap：runner 注册与 provider 版本 | 54–55 | 2 |
| 第十八轮 | 全局命名的资源：桶名被占 | 56 | 1 |
| 第十九轮 | 镜像仓库：可达 ≠ 拉得到 | 57 | 1 |

轮次不是按时间堆的，是按**模块**分的 —— 每一轮解决一个模块的问题，
所以第 7 轮（Ansible）条目最多：那一层的失败模式最不容易在本地复现。

第十一轮是唯一的例外：它不是按模块分的，是按**阶段**分的 —— 那一轮把前十个模块
连起来真跑了一遍，暴露的全是"模块之间"的东西（行尾、镜像、端口、残留状态）。
这也说明一件事：**单看每一层都验过了，不等于连起来能跑通；接缝处长出来的问题，
在每一层自己的检查里都没有位置。**
