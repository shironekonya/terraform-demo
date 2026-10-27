# 踩坑清单

十二轮迭代里实际修掉的 57 个问题，全部来自真实报错、真机验证和破坏性验证。
编号就是文档正文里引用的编号（LOGIC.md 里的「见踩坑 27」指第 27 条）。
带 ★ 的是「不报错但后果严重」或「看似在保护其实没保护」的那类，建议优先看。

排查线上问题时先扫一遍索引 —— 尤其是那些报错信息指向的地方不是真正原因的条目
（24、30、40、43、48）。

## 索引

| #  | 一句话 | 轮次 |
| -- | --- | --- |
| 1  | Terraform 没有 `filetimestamp()`，改用 `filesha1()` | 一 |
| 2  | `user_data_replace_on_change` 是 AWS 专有参数，阿里云不认 | 一 |
| 3  | 输出引用敏感变量必须显式 `sensitive = true` | 一 |
| 4  | destroy provisioner 不能引用 `var.*`（连 `interpreter` 也不行） | 二 |
| 5  | `set -e` 与 `[ ] && { }` 的交互 | 二 |
| 6  | 同一个 destroy 陷阱又踩一次：`self.triggers.*` 必须先定义 | 三 |
| 7  | 渲染占位符不能用 `sed`，要用 `replace()` | 三 |
| 8  | `local` 值在整个模块内必须唯一 | 四 |
| 9  | HCL 三元表达式换行必须加括号 | 四 |
| 10 | `set -u` 下的空数组展开 | 四 |
| 11 | `host_port_of` 的参数语义搞反了（容器端口 vs 宿主端口） | 五 |
| 12 | HCL 模板插值与 shell 参数展开的字面冲突 | 五 |
| 13 | 旧的 `docker run` 方案会和 compose 抢对象 | 五 |
| 14 | `harbor_url` 少了 `/api/v2.0`，而且连通性检查是假的 | 六 |
| 15 | `templatefile()` 会解析注释里的 `${...}` | 六 |
| 16 | 把已 `jsonencode` 的字符串当对象嵌套 → 错误请求体 | 六 |
| 17 | 「不改本地机器」不能靠约定，要靠结构 | 七 |
| 18 | 断言失败要给出「下一步怎么做」 | 七 |
| 19 | ★ 更危险的能力必须蕴含较弱的能力 | 七 |
| 20 | 测试要读被测文件本身，不要重抄一遍公式 | 七 |
| 21 | 校验器必须被证明真的会响 | 七 |
| 22 | 禁区判定要按路径段，不能按字符串前缀 | 七 |
| 23 | 控制端跑不起来就说跑不起来 | 七 |
| 24 | ★ `group_vars` 放错位置 = 整层保护被静默忽略 | 七 |
| 25 | Ansible 默认开 `trim_blocks`，块标签写 `{%-` 会吞换行 | 七 |
| 26 | 模板引擎不区分注释与正文，注释里的 `{{ }}` 也会炸 | 七 |
| 27 | WSL 的 `/mnt/c` 是世界可写目录，`ansible.cfg` 被拒载 | 七 |
| 28 | ★ 模板产物里不能嵌「当前时间」 | 七 |
| 29 | ★ 边界写在文档里不等于守住了：state 全在本地 | 八 |
| 30 | ★ 校验器自己会犯错（自证抓出 4 个缺陷） | 八 |
| 31 | 边界检查要用闭集，豁免要显式写出来 | 八 |
| 32 | ★ 边界漏项的第二种形态：连载体都不存在 | 九 |
| 33 | ★ 在 CI 里让 Terraform 触发 Ansible = 把耦合装回去 | 九 |
| 34 | ★ 一个永远不会响的检查，比没有检查更糟 | 九 |
| 35 | ★ 别把「校验器不报错」当成「已经验过了」 | 九 |
| 36 | ★ 规则之间要划清管辖 | 九 |
| 37 | ★ 要找的是「违反边界却查不到」的反例 | 十 |
| 38 | ★ 隐式豁免是定时炸弹，两个方向都会炸 | 十 |
| 39 | ★ 破坏性脚本里不要用会中途退出的下游命令 | 十 |
| 40 | ★ `rsync -a` 会把「世界可写」一起搬过去 | 十 |
| 41 | ★ 已收敛的系统上跑「幂等」，证明不了变更路径是对的 | 十 |
| 42 | CRLF 行尾会让 `local-exec` 里整段 bash 语法错乱 | 十一 |
| 43 | ★ 镜像的系统 Python 决定了谁能管这台机器 | 十一 |
| 44 | 安全组只开了声明的端口，容器映射的端口没开 | 十一 |
| 45 | ★ `known_hosts` 是唯一跨 `destroy` 存活的遗留物 | 十一 |
| 46 | ★ 同一台机器开了两个对外端口，等于有了两个「首页」 | 十二 |
| 47 | ★ 拉不到镜像不是编排问题，但报错会让人这么以为 | 十三 |
| 48 | ★ 报错行号对不上，说明你排的是旧副本 | 十四 |
| 49 | ★ 「5xx 才算故障」把「降级但仍在服务」判成了故障 | 十四 |
| 50 | ★ 回滚不是重跑一遍发布 | 十五 |
| 51 | ★ 单文件 bind mount 把 inode 钉死，换内容不生效 | 十五 |
| 52 | `roles:` 列表的 dict 写法会静默忽略 `tasks_from` | 十六 |
| 53 | ★ 静态护栏不跟进 `import_role`，`tasks_from` 指向的文件对检查器不存在 | 十六 |
| 54 | glrt- token 注册 runner，配置参数会直接 FATAL | 十七 |
| 55 | alicloud provider 的 versioning 块用 `status`，不是 `enable` | 十七 |
| 56 | OSS 桶名跨账号全局唯一，「名字被占」不等于「我没有」 | 十八 |
| 57 | `/v2/` 返回 401 不是坏消息，`000` 之下还藏着一个假象 | 十九 |

---

## 第一轮：阿里云部分

1. `filetimestamp()` 不存在 —— 改用 `filesha1()` 取内容哈希，语义反而更准（内容真变了才重建）。
2. `user_data_replace_on_change` 是 AWS 专有参数，阿里云不认。改用 `lifecycle.ignore_changes`。
3. 输出引用敏感变量必须显式 `sensitive = true`，否则 `validate` 直接报错。

## 第二轮：本地容器部分

4. destroy provisioner 只能引用 `self` / `count.index` / `each.key`，连 `interpreter = [var.bash_path, "-c"]` 都报
   `Invalid reference from destroy provisioner`。修法：把 `ssh_port` / `ssh_key` / `ssh_user` / `down_on_destroy`
   预先放进 `triggers`，销毁时读 `self.triggers.*`。先列全用到的 `self.triggers.*` 再写脚本，顺序反过来必然漏。
5. `set -e` 与 `[ "$A" = "x" ] && { }`：值相等时 `[ ]` 返回非零，但作为 `&&` 左侧不触发退出，实测幂等路径是通的。

## 第三轮：CI/CD 部分

6. destroy 脚本引用了 `self.triggers.cleanup` / `self.triggers.harbor_pass`，但忘了放进 triggers。
   同第 4 条，第二次踩。
7. 渲染占位符不能用 `sed`（替换串要转义 `\` `&` `|`，密码/Token 里可能出现，渲染错了报错还指向认证失败）。
   `replace()` 是纯字面量替换，字符安全。后来连 `replace()` 也替换掉了：
   每字段一个占位符要维护 10+7 个、还要防前缀互吞，改成整份配置 `jsonencode` + `base64encode`，
   每个脚本只剩一个 `__WF_CONFIG_B64__`。

## 第四轮：GitLab 部分

8. `local` 值模块内必须唯一（`jenkins_script` 撞名报 `Duplicate local value definition`）。
   更隐蔽的变体：两台机器的「容器 shell 端口」和「宿主 SSH 端口」都叫 `ssh_port`，validate 抓不住，
   覆盖后悄悄失效。命名字段带上下文。
9. HCL 三元表达式换行必须整体加括号：`x = cond` 换行写 `? a : b` 直接编译失败（换行即表达式结束）。
10. `set -u` 下空数组展开 `"${ARR[@]}"` 在旧版 bash 报 `unbound variable`，改用 `${ARR[@]+"${ARR[@]}"}`。

## 第五轮：拿到真实 compose 文件之后

这一轮全是「代码没错、默认值错了」：语法检查、validate、plan 全绿，真跑到机器上才出事。

11. `host_port_of` 参数必须是**容器内端口**（8080），传了宿主端口（8081）。之前没暴露是因为默认值里两者相等。
    两个值语义易混时，函数名/注释必须点明单位或视角，否则默认值一变就静默出错。
12. 脚本嵌在 HCL 里时，`${CFGFILE:-...}`、`${CONFIG_HASH:0:12}` 这类带冒号的 shell 展开会撞上
    HCL 模板插值。修法不是转义，是换成 if 判断 / `printf '%.12s'`。
    规律：嵌进 Terraform 的 shell 片段只用 `$VAR` 最简形式。
13. 旧的 `docker run` 方案和 compose 抢同一个 `jenkins` 容器：要么端口冲突起不来，要么侥幸起来但
    6 个宿主资源挂载（docker.sock、k3s、kubectl、rancher/k3s、.kube、/usr/bin/docker）全丢，
    Jenkins 部署任务在某个深夜开始失败。修法：删掉创建逻辑，整体换成「核对编排栈」。
    **同一份声明式配置不该被两个工具同时声明。**

## 第六轮：shell 改写成 Python

14. `harbor_url` 默认值没带 `/api/v2.0`，请求打到前端页面返回 404；而连通性检查用 curl 退出码且没加 `-f`，
    404 的退出码是 0 —— 「检查通过」后脚本继续跑。修法：入口处 `normalize_base()` 归一化。
    判断 HTTP 接口能不能用看状态码，不看进程退出码；可写成两种形式的值在入口归一化一次。
15. `templatefile()` 不区分注释和正文，compose 模板注释里的 `${name}` 直接 validate 报错。
    任何字面量 `${...}` 都要写 `$${...}`。
16. `jsonencode` 过的字符串被当成对象嵌进另一个 `jsonencode` → 双重编码，GitLab 收到一整串带引号的文本，
    报 400 且看不出原因。修法：嵌套值保持 HCL 对象，只在最终出口 `jsonencode` 一次。

顺带：4 个 `scripts/*.sh` 改写成 `.py`，`inventory.tf` 里几百行内联 shell 抽成 `scripts/inventory.py`
（`.tf` 里只剩 4 行 `environment`）。可读性的正解不是把 shell 翻译成另一种 shell，是让它离开 `.tf`。

## 第七轮：加入 Ansible 批量管理层

17. 「不改本地机器」不能靠约定（记得加 `--limit`、记得写 `when`），要靠结构：
    结构性隔离（变更 role 只在 `hosts: cloud` 的幕里）+ 运行前断言（`guard`）+ 逐任务许可（覆盖必须 `wf_can_write`）。
    三层失效模式不同，忘写 when、改错 hosts 各有下一层兜底。
18. 断言失败要给出「下一步怎么做」。`guard` 的 `fail_msg` 直接写三条命令（体检 → 干跑 → 真改）。
    只报 `assertion failed`，用的人下一步一定是去搜怎么关掉断言。
19. ★ 更危险的能力必须蕴含较弱的能力。原来 `wf_can_write` = 「不在 local 组 或 开了覆盖开关」，
    穷举 16 种组合时发现：只给 `local_overwrite_allowed=true` 时已拿到覆盖权限（没解锁变更）。
    真实路径上有幕 E 门闩拦着，但那是 playbook 写法保证的，不是公式本身 —— 公式必须单调：

    ```yaml
    wf_can_write: >-
      {{ (not (wf_in_local_group | bool))
         or ((local_mutation_allowed | default(false) | bool)
             and (local_overwrite_allowed | default(false) | bool)) }}
    ```
20. 测试要读被测文件本身：`selftest.py` 用 `yaml.safe_load` 打开 `roles/guard/tasks/main.yml`
    取出公式求值，另配「公式里只允许出现这 4 个已知开关」断言。公式被抄进测试，改公式测试照样绿。
    权限公式的测试用 `StrictUndefined`：可链式未定义会把写错的变量名静默变成 False，
    而 False 恰好意味着「不保护」。
21. 校验器必须被证明真的会响：故意把 `hosts: cloud` 改成 `all`、删掉门闩、公式改松，确认都被抓到。
    没被故意破坏过的校验器等于没校验器。
22. 「路径在不在禁区之下」按路径段判断：`/data/jenkins-data-OLD` 是字符串前缀但不在禁区之下。
    `wf_under_any` 先 `posixpath.normpath` 再按段比较。
23. 控制端跑不起来就说跑不起来（Ansible 依赖 `os.fork`，Windows 没有）。如实标注未验证的部分，
    比「看起来验证过」安全。
24. ★ `group_vars` 放错位置 = 整层保护被静默忽略。Ansible 只加载清单旁或 playbook 旁的 `group_vars`，
    原来放在 `ansible/group_vars/` 两边都不沾，于是 `wf_local_forbidden_paths` 整个为空、报告写到 default 目录，
    而 guard 照常全绿（它用的魔法变量不依赖 group_vars）—— 保护变量加载失败但保护检查全绿，
    是最坏的失败模式。修法：挪到 `inventory/group_vars/`，并给 `check_guard.py` 加第 8 类不变量（P8）
    按 Ansible 真实查找规则验证变量真的会被读到。
25. Ansible 的 Jinja 默认开 `trim_blocks`，块标签写 `{%-` 会额外吞掉上一行的换行，报告全挤成一行。
    修法：删掉全部块标签前导 `-`，离线渲染环境配 `trim_blocks=True`，报告测试用**按行断言**
    （子串断言对换行丢失是盲的）。
26. Ansible 会把整个 `shell:` 字符串先过 Jinja2 再交给 bash，注释里的字面 `{{ }}` 也炸。
    修法：`selftest.py` 的 `test_shell_bodies_compile()` 抽出全部 shell/command body 逐个
    `env.parse()`，配反向验证（字面 `{{ }}` 必须报错）和假阳性守卫。
27. WSL 的 `/mnt/c` 是世界可写目录，Ansible 拒绝加载里面的 `ansible.cfg`（只 WARNING，inventory/roles_path
    静默失效）。解法：`ANSIBLE_CONFIG=<绝对路径>`，或拷到 ext4（必须补 `chmod -R go-w`，见 40）。
    同类：`inventory/` 里放 `*.example` 会被当清单解析（`Exec format error`），示例文件放 `examples/`。
28. ★ 模板产物不能嵌「当前时间」。`index.html.j2` 页脚写了 `渲染于 {{ now() }}`，
    template 靠「渲染结果 == 线上文件」判变更，时间戳每次都变 → 每次跑 playbook 容器都被无谓重启。
    修法：删时间戳（看部署时间去 `docker inspect` 的 StartedAt）。
    参与变更检测的产物，内容必须是「事实的函数」，不能是「时间的函数」。

## 第八轮：守住分层边界

29. ★ 边界写在文档里不等于守住了：前七轮做了三层防护 + 8 类不变量，但全项目 state 都在各人电脑上，
    README 只有一句「多人协作应改用远程后端」—— 一句话等于零落实。它不报错：单人开发永远跑不出问题，
    直到两个人同时 apply，症状是「资源在云上、state 里没有」，还持续计费。
    修法：`backend.tf.example` 备好，B4 每次跑都提醒，CI 加 `--require-remote-state` 升级为硬失败。
    默认配置下就被违反的边界，必须每次都被提示出来。
30. ★ 校验器自己会犯错 —— 自证 29 个用例跑出 4 个失败，全是检查器的漏洞：
    - 自我误报：把 selftest 里故意写的越界样例当真违规 → 扫描排除自身文件
    - argv 写法漏检（最值得记）：`['systemctl','restart','docker']` 匹配不上 `systemctl\s+restart`，
      规范写法反而漏检 —— 比不检查更糟，给人「查过了」的错觉。修法：匹配前摊平 argv
    - 只扫 subprocess 附近：`open('terraform.tfstate')` 不经过 subprocess，上下文过滤要按规则的可能形态定
    - 提示文本未放行：`echo "sudo apt-get install..."` 不是执行。判定原则：只看会被执行的命令
31. 边界检查用闭集：`kubernetes_` 开头一刀切会把 `kubernetes_namespace` 这类周边对象也拦掉，
    用户第一反应是关掉整个检查。豁免也要显式（`user_data` 装包属于实例启动配置，显式写进规则表），
    靠「大家心里都清楚」的豁免，下一个人重构时一定踩。

## 第九轮：CI/CD 成为唯一触发器 + 跨层衔接声明化

32. ★ 边界漏项的第二种形态：连载体都不存在 —— 整个仓库零个 CI 配置。
    「CI 是唯一触发器」不是守得松，是根本没守；仓库明明管着 GitLab+Jenkins+Harbor，
    但那是应用交付链路，基础设施变更 100% 靠人手跑。连载体都没有时，第一步不是加检查，是先把载体建出来。
33. ★ CI 里让 Terraform 触发 Ansible = 把耦合装回去：`deploy` job 里先 apply 再 playbook，
    前半截成功后半截失败时，「资源建没建、机器配没配」说不清。正确形态是两个 job + artifact
    （`terraform output -json` → `.ci-artifacts/tf-outputs.json`），Ansible 侧没有 state 没有云凭证，
    越界是「做不到」而不是「约好了不做」。判断该不该耦合，看「失败后状态归谁」。
34. ★ 永远不会响的检查比没有检查更糟：B8 顺手加的「声明不许成环」检测不可达 ——
    方向钉死成 `X → terraform` 后环构造不出来。留着它让人以为这个性质查过了。
    一个检查要么能举出触发它的小例子，要么它就是在骗人。
35. ★ 「校验器不报错」不等于「验过了」，要拿真实仓库做破坏性验证（每次只改一处，跑完立刻还原）。
    第一遍不可信：S1 之后忘了还原 `.gitlab-ci.yml`，后面几个场景全带着前一个伤口。
    破坏性验证里，还原是测试的一部分。
36. ★ 规则之间划清管辖：S2（已声明文件里加 `terraform apply`）期望 B8 报，实际 B2 报 —— 这是对的，
    B8 管「有没有登记」，B2 管「行为是不是只读」。为了「更全面」把判定抄进 B8，是制造两份会漂移的逻辑。
    B1/B2/B8 共用同一份遍历生成器，防止输出自相矛盾。

## 第十轮：宿主机边界 + 豁免登记

37. ★ 边界宣布了不代表没有反例：`archive-vm-clone/`（vmrun 克隆宿主机虚机）和
    `vmware-vsphere-reference/` 都违反 B9 且没有任何规则能发现 —— 当初就没打算跑的代码，
    连「要不要被检查」都没被问过。新增 B9 时顺手把 vsphere-reference 从「在用模块」摘出来
    （它被 B4 要求配远程后端，这种噪音会淹掉真信号）。
38. ★ 隐式豁免是定时炸弹：「目录名以 archive 开头就跳过」两个方向都炸 —— 改名静默失效（全是噪音），
    真在跑的代码放进 archive- 目录永远查不到。改成 `boundaries.yml` 登记制 + 清单自身被检查
    （缺 why、登记不存在的路径都报）。豁免对所有检查生效，不搞每条规则一个跳过开关。
    验证靠真实仓库改名：S11 把目录改成 `old-vm-clone/`，立刻报 29 个问题。
39. ★ 破坏性脚本里别用会中途退出的下游命令：`detail()` 里的 `| head -6` 在输出超 6 行时提前退出，
    上游撞 SIGPIPE 挂住，`trap` 里的还原不执行，S4 的破坏留在了工作区。同一个坑第二次踩
    （第八轮 head 打断过 S5），说明得改结构：输出先落盘，后续从文件读。
40. ★ `rsync -a` 把「世界可写」搬过去：仓库已在 ext4 上仍报 world writable WARNING，
    因为 `-a` 隐含 `-p`，源头 drvfs 的 777 跟着权限位一起过来了。「拷到 ext4 就好了」只做对一半，
    生效的是拷完那次 `chmod -R go-w`。检查方式只有 `ansible --version | grep "config file"`。
41. ★ 已收敛的系统上跑「幂等」证明不了变更路径：`site.yml -e local_mutation_allowed=true` 是 changed=0，
    因为上一轮已把东西补齐，它只证明了「无事可做时不乱做事」。
    验证「只补缺不覆盖」要先制造差异：注入一个不存在的目录 → 补缺 changed=1，
    同时比对覆盖类文件 md5 前后一致。验收幂等和验收变更需要两种前置状态。

## 第十一轮：真机联调

这四条的输入来自仓库之外（操作系统、网络、上一轮跑过什么），四个检查器都管不到。

42. CRLF 行尾让 `local-exec` 里的 bash 语法错乱（`set: pipefail: invalid option name`、
    `$'\r': command not found`）。仓库索引是 LF，但 Windows `core.autocrlf=true` 取出时转成 CRLF，
    `git diff` 看不出差异，而内嵌 bash 的文件是拿工作区文件直接喂解释器的。
    修法：清工作区 + `.gitattributes` 写 `* text=auto eol=lf`。只清工作区不补规则，下一个人 clone 照样撞。
    嵌套语言的文件里，行尾是语法问题不是格式问题。
43. ★ 镜像的系统 Python 决定谁能管这台机器。Ansible 在 `gather_facts` 失败，报
    `Module result deserialization failed` + `SyntaxError: future feature annotations is not defined`
    —— 报错像模块传输坏了，实际是 ALinux3 的 `/usr/bin/python3` 是 3.6（且 dnf 依赖它不能升级），
    `from __future__ import annotations` 是 3.7 语法。钉 ansible-core<2.17 是治标（2.16 已 EOL），
    真修法是默认镜像换 Ubuntu 24.04，`image_id` 用 `data "alicloud_images"` 按发行版族查最新。
    顺带：`ignore_changes = [image_id]` 在这里是错的 —— 换镜像就是换操作系统，必须重建。
44. 安全组只开了 22/80/443/ICMP，容器映射的 8080 没开 —— apply 成功、playbook 全绿、容器 Up，
    浏览器超时。两条声明各自正确，问题在它们之间：端口定义和端口放行在两个层里。
    修法：端口做成变量（`var.app_stack_port`），两边引用同一个数。
45. ★ `known_hosts` 是唯一跨 destroy 存活的遗留物。IP 回收复用后 ssh 报
    `REMOTE HOST IDENTIFICATION HAS CHANGED`，Ansible 第一幕 UNREACHABLE；
    MITM 式的报错文本把人往安全事件带，且它不属于任何一层、在下一次 apply 之后才发作。
    修法不是关校验（`StrictHostKeyChecking=no` 放弃了对新机器的校验），而是清掉那条记录 ——
    **已自动化**：`gen_cloud_inventory.py` 生成清单时同步删掉本批 IP 的旧键
    （含 `|1|salt|hmac` 哈希写法），清单外主机不动。手工清用 `ssh-keygen -R <IP>`。
    顺带：`ansible.cfg` 里 `host_key_checking = False` 从来没生效过 ——
    `ssh_args` 的 `-o StrictHostKeyChecking=accept-new` 最后拼进命令行，后者胜出。

## 第十二轮：把入口收成一个

46. ★ 一台机器两个对外端口（80 静态页 + 8080 应用）= 两个「首页」，验收说不清哪个算数。
    根子是没有人回答「这台机器对外长什么样」。修法：容器退回绑 `127.0.0.1:8080`，
    系统 nginx 80 反代，安全组删掉 8080。apply 刚结束 Ansible 未跑时，80 落到待部署页，存活信号不丢。
    不需要被外面访问的端口，就不该出现在安全组里。

## 第十三轮：镜像不是顺手就能拉到的

47. ★ `docker compose up -d` 里拉不到镜像，报错说的是网络，坏掉的是整幕。三个问题互相掩盖：
    拉镜像（拿物料）和 up -d（用物料）混在一条命令，网络问题以编排失败的样子出现；
    dockerd 只说 dial timeout 不说下一步，还挂着 `changed: true`；
    这个 task 一失败，同 block 后面「把 80 配好给待部署页」全不执行。
    修法：拉镜像拆成独立任务（探测 → 离线导入/远程拉取 → 判定），`up -d` 以镜像就位为硬门槛；
    拉取套 `timeout`，失败不当场 raise，stderr 和镜像站探测留给最后统一收场；
    失败信息里写清三条路（加速器 / 离线包 / 换镜像）。
    同源坑：`daemon.json` 写了 `registry-mirrors` 没重启 docker。现在写完 `flush_handlers` 重启，
    再回读 `docker info` 复核 —— 文件是「我以为的状态」，守护进程才是「它实际的样子」。

## 第十四轮：在真机上验失败路径

48. ★ 报错行号对不上 = 你排的是旧副本。真机报 `main.yml:96:7`，本地第 96 行早不是那段 ——
    服务器上 `/data/terraform-demo` 停在上一轮版本。先确认「跑的是哪份代码」：
    报错文件里搜自己刚加的注释，搜不到就是没同步；推完在目标机 `sha1sum` 核对（先统一 LF 再推）。
    跨机器调试时，「当前代码是什么」是要主动验证的假设。
49. ★ 自检写「5xx 才算故障」把「降级但仍在服务」判成了故障：镜像没拿到时旧容器还在跑，80 返回 200，
    uri 模块当场报错，盖掉了真正的失败原因。修法：`status_code: [200, 502, 503, 504]` + `failed_when: false`，
    断言里分判 —— 200 = 旧容器在服务；5xx = 必须真的是待部署页。
    断言要覆盖所有说得过去的状态，预期内分支让模块自己报错，会盖掉真正的原因。

## 第十五轮：发布与回滚

50. ★ 回滚 ≠ 重跑一遍发布：`-e wf_release=v1` 再跑 playbook 是**用当前模板重新渲染**，
    而需要回滚的典型场景恰恰是模板改坏了 —— 结果是旧版本号 + 新内容，比不回滚更糟。
    回滚只能发生在产物上：发布时落 `releases/<版本>/`（不可变产物），`public/` 是产物的拷贝，
    `current` 记生效版本，`history` 记发布顺序。回滚 = 挑版本 → 拷产物 → 从 80 读回版本标记，
    全程不出现 template 模块（自测硬断言）。
    凡是「回到之前某个状态」的需求，都得先有那个状态的可寻址副本。
51. ★ 单文件 bind mount 把 inode 钉死：`index.html:/usr/share/nginx/html/index.html` 在容器创建时
    解析成具体 inode，之后换文件（copy 写临时文件再 rename，inode 变了）容器读到的还是旧的 ——
    页面停在上一版，无任何报错，`docker exec cat` 是新内容、`curl` 出来是旧内容。
    改挂目录：按路径解析，换文件立刻生效、不用重建容器。
    bind mount 的粒度就是变更粒度。

## 第十六轮：流水线驱动发布/回滚

52. `roles:` 列表的 dict 写法只认 `role` / `vars` / `when` / `tags`，`tasks_from` 被静默丢掉 ——
    「回滚」老老实实跑了 `main.yml`，页面版本号变成最新版。无警告、无 lint 报错。
    支持 `tasks_from` 的是 `tasks:` 里的 `import_role` / `include_role`；这里用 `import_role`
    （静态展开，`--list-tasks` 可见）。能被忽略的配置项比报错的危险；自测钉了两条形状：
    `tasks` 里必须有 `app_stack/rollback`，`roles:` 列表不许出现 dict。
53. ★ 静态护栏不跟进 `import_role`：`check_guard.py` 原本只跟进 `include_tasks` / `import_tasks`，
    于是 `rollback.yml` 里的两个 copy 从没进过 P6 视野 —— 往那里加写操作、改成 `hosts: local` 都是绿的。
    同一层盲区：`play["roles"]` 不含 `tasks:` 里的 `import_role`，判「解锁型 play 引用 cloud-only role」
    时候选里根本没有它。修法：让检查器的视野对齐 Ansible 的实际执行路径
    （跟进 `import_role`/`include_role` 的 `tasks_from`）。
    校验器落后于写法一次，它就不再是一个保护。

## 第十七轮：147 真跑 bootstrap

54. glrt- token 注册 runner 时，`--tag-list` / `--run-untagged` / `--locked` 直接 FATAL：
    新式 authentication token 的这些配置在 **GitLab 服务端**定，register 只能带 name / executor / url / token。
    `install_runner.sh` 按 token 前缀分流。同一个命令，token 类型一换，合法参数集就变。
55. alicloud provider 把 versioning 块从 `enable = true` 改成 `status = "Enabled"`（对齐 AWS S3），
    网上教程大量是旧写法。本地有 provider 缓存的话 `terraform validate` 半分钟就能拦住，
    别让它流到 147 上才炸。

## 第十八轮：桶名被占

56. OSS 桶名跨账号全局唯一，`CreateBucket` 对任何已存在的桶都报 `is not available` ——
    包括你自己账号里的。两种含义处理方式相反：自己的 → `terraform import` 接管；别人的/别的 region → 换名。
    判读只能按 terraform 原话分四支：`AccessDenied/Forbidden.RAM` → 补子用户权限；
    `does not belong to you / BucketAlreadyExists` → 才换名；`NoSuchBucket / must be addressed using...` → region 不对；
    `resource address ... does not exist` → 资源名写错。
    **import 失败永远不要吞 stderr** —— 那是唯一能区分三种原因的东西
    （曾把 stderr 丢进 /dev/null，只看到一行「import 失败」，顺手下了错误结论）。
    自查：`aliyun sts GetCallerIdentity` 看这对 AK 是谁、`aliyun oss ls oss://<bucket>/` 看碰不碰得到。

## 第十九轮：镜像仓库 —— 可达 ≠ 拉得到

57. `/v2/` 返回 401 不是坏消息：那是 registry 协议的正常认证挑战（匿名拉公开镜像也要先取 token），
    「401」说明这是个能说话的 registry。而 `docker info` 里看到 Registry Mirrors 也不等于生效 ——
    `daemon.json` 只在 dockerd 启动时读，`systemctl restart` 又是 `failed_when: false`，存在夹缝。
    验证要走到真实产物：`/v2/`（401+WWW-Authenticate）→ 取 token（200）→
    `GET /v2/<repo>/manifests/<tag>`（200 才算能拉）。实测同一个 401：daocloud/1ms 是「能拉」，
    阿里云公共仓库对 `library/nginx` 是「没有」，只看状态码分不出来。
    修法两层：`wf_docker_registry_mirrors` 声明镜像站 → daemon.json + 重启 + `docker info` 复核；
    `app_stack` 直连失败后把仓库名换成镜像站前缀拉、`docker tag` 回原名（不依赖守护进程配置，
    只在已声明的镜像站之间试；带 `.` 或 `:` 的镜像名跳过）。
    可达性探测给出的结论非常弱，下结论要验到真实产物。

---

## 分布

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

轮次按模块分，不是按时间堆。第七轮（Ansible）条目最多，因为那一层的失败模式最不容易在本地复现；
第十一轮是唯一的例外，它按阶段分 —— 把前十个模块连起来真跑一遍，暴露的全是模块之间的东西
（行尾、镜像、端口、残留状态）。单看每一层都验过了，不等于连起来能跑通。
