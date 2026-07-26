# 本机轻量 watcher 运行说明

## WEB-010 单一每日流水线候选

当前 Git 候选把日常业务收敛为一条 `08:00` 外部 schedule：

```bash
python3 scripts/run_daily_workflow.py \
  --run-id <EXACT_RUN_ID> \
  --business-date <SHANGHAI_DATE> \
  --source-revision <EXACT_SOURCE_REVISION> \
  --publisher-url https://ai-account-workbench-v1.le-ei.chatgpt.site \
  --publisher-identity radar-production:daily-workflow.sqlite3
```

正式 owner-only Sites runtime 必须另外提供 `WEBSITE_PROJECTION_BEARER` 与
`WEBSITE_PROJECTION_SIWC_BYPASS_BEARER`，只从环境读取且不得进入 Prompt、命令
文本、Git 或日志。URL、authority identity 或任一 bearer 缺失时，统一
入口在创建 run、采集、Skill 或 projection 写之前 fail closed。相同 completed run
与相同 canonical contract identity 的重放只核对未决 projection；已 applied 且
read-back green 时保持 workflow SQLite、timestamps、Skill attempts、artifacts 和
receipts 原字节不变。

该入口在同一 exact run 内依次提交并 read-back `collection -> editorial -> scripts`。
来源配置继续只读 `output/state/source_control.sqlite3`，每日运行权威为 ignored
`output/state/daily_workflow.sqlite3`，网站 D1 仅保存 projection。正常调用图不读取
或写入飞书 01/02/03/04/06，不发送 Topic Card、callback 或成功/失败通知。

`ai-04-rebuild`、`ai-rebuild-2` 在发布候选中保持 PAUSED；下一次正常 `08:00`
三阶段 first-real-flow 与网站 exact read-back 全绿后，才通过官方 control plane
归档/删除。`watch_script_package_queue.py` 同样退出 active runtime，但保留为历史
维护代码，不得被统一入口调用。live automation、watcher 和腾讯云资源不在 Dev
修改。

当前正式脚本包生成走本机轻量 watcher。原因是 `06 完整脚本与制作包` 需要 Codex 和全局私有 Skill 参与，不能退化成纯模板代码；但也不应该每小时固定消耗 Codex automation 额度。

边界要清楚：

- 卡片点击回写仍由腾讯云 SCF receiver 承接。
- `06` 脚本包生成由本机轻量 watcher `scripts/watch_script_package_queue.py` 承接。
- watcher 每隔几分钟扫描飞书 `04`，空队列只做飞书 API 检查，不调用 Codex；只有存在待生成记录时才调用 `codex_script_package_runner.py` 和 `codex exec`。
- 自动队列默认和卡片有效期一致，只扫近 5 天推荐记录，并排除明显测试标题；旧记录或测试记录要用 `--record-id` / `--include-test-records` 手动补跑。
- 锁屏但 Mac 不睡眠、不断网时可以跑；睡眠后唤醒一般会继续跑；关机或重启期间不会跑，但登录后会由 LaunchAgent 自动拉起。

## 0. 生产窗口唤醒与保活

每日生产链路依赖本机 Codex 和网络环境。为了降低“不插电、屏幕熄灭、系统空闲睡眠”导致 08:00 任务延迟的风险，生产机应安装一个本机唤醒/保活窗口：

```bash
python3 scripts/install_production_keepawake.py --install --configure-wake
```

默认行为：

- `pmset repeat wakeorpoweron MTWRFSU 07:50:00`：每天 07:50 唤醒或开机。
- 用户级 LaunchAgent `com.austin.ai-account-radar.production-keepawake`：每天 07:50 执行 `/usr/bin/caffeinate -ims -t 10800`，保活 3 小时。
- 这个保活不强制点亮屏幕，会阻止系统空闲睡眠、磁盘睡眠，并在 AC 电源下请求 `PreventSystemSleep`，覆盖 08:00 采集、09:15 主编写回和 10:00 发卡。

查看状态：

```bash
python3 scripts/install_production_keepawake.py --status
```

只安装 LaunchAgent、不修改系统唤醒计划：

```bash
python3 scripts/install_production_keepawake.py --launch-agent-only
```

注意：这能提高“不插电但开盖/未合盖”的稳定性；不能保证 MacBook 在“不插电且合盖”时继续完整执行用户态任务。长期生产观察仍建议流程窗口内保持开盖，屏幕可以自动熄灭。

2026-07-04 生产复盘确认，旧命令 `/usr/bin/caffeinate -im -t 10800` 只产生 `PreventUserIdleSystemSleep` / `PreventDiskIdle`，无法防住 Maintenance Sleep / DarkWake 窗口。当前命令包含 `-s`，但 `PreventSystemSleep` 只在 AC 电源下有效；如果 MacBook 合盖进入 clamshell sleep，仍可能需要接电、开盖或外接显示器等系统条件配合。安装后可用 `--status` 检查实际 LaunchAgent 命令；若 status warning 提示缺少 `-s`，必须重新安装本项目 keepawake。

## 1. 当前生产 watcher

当前不使用旧的每小时自动任务。旧 `ai-06` 已停用，避免空队列时仍占用调度额度。

安装为登录后自动启动：

```bash
python3 scripts/install_script_package_watcher_launch_agent.py --interval-minutes 5 --limit 2 --max-age-days 5
```

这个命令会先把运行时同步到 `~/.codex/ai-account-radar-runtime`，再安装用户级 LaunchAgent。这样后台进程不直接读取 Desktop 下的仓库，避免 macOS TCC 拦截。

生成的 `06` 文档会通过项目文档库根目录下的固定入口查看：

```text
/Users/congcong/Desktop/AI/AI项目/AI账号工作流/06 完整脚本与制作包/
```

这个入口是一个指向 runtime 输出目录的软链接。后台真实写入 runtime，文件以 `YYYY-MM-DD_选题标题_完整脚本与制作包.md` 平铺保存；飞书 `06` 里的 `飞书文档` 字段是线上阅读入口，`飞书文件夹` 字段保存用户可见文件夹入口，`文档同步状态 / 文档同步错误` 用来暴露同步异常，`本地文档` 字段显示项目根目录下的备份路径。

写入用户可见飞书文件夹需要本机有用户身份 OAuth token。首次或 token 过期后运行：

```bash
python3 scripts/feishu_user_oauth.py --timeout-seconds 240
```

授权信息写入 `.env.local`，不进入 Git。生产仓库和 `~/.codex/ai-account-radar-runtime` 会通过 `RUNTIME_SOURCE.txt` 绑定，授权脚本和 runner 自动刷新 token 时会把用户 OAuth token 同步写回两边；如果 dev/test worktree 没有这个绑定，不会误写生产 runtime。开发者后台需要先开通 `offline_access`（持续访问已授权的数据），否则飞书不会下发 refresh token；如果 token 缺失、过期或被飞书判定为 revoked，runner 仍会生成本地 Markdown 和 06 记录，但 `文档同步状态` 会报警，并尝试发送“飞书文档同步授权失效”通知。

之后如果改了 watcher、runner、Skill 镜像或字段映射，要重新运行一次安装命令，或只同步 runtime：

```bash
python3 scripts/install_script_package_watcher_launch_agent.py --sync-runtime-only
```

同步 runtime 时会先比较生产仓库和 runtime 两边的用户 token 过期时间，保留更新的一份，避免 `.env.local` 被旧 refresh token 覆盖。

查看 LaunchAgent 状态：

```bash
python3 scripts/install_script_package_watcher_launch_agent.py --status
```

启动前台 watcher：

```bash
python3 scripts/watch_script_package_queue.py --interval-minutes 5 --limit 2 --max-age-days 5
```

启动后台 watcher：

```bash
screen -dmS ai06-watcher python3 scripts/watch_script_package_queue.py --interval-minutes 5 --limit 2 --max-age-days 5
```

查看是否在运行：

```bash
screen -ls
```

停止后台 watcher：

```bash
screen -S ai06-watcher -X quit
```

旧的“launchd 直接定时跑生成器”方案已删除。原因是它既不符合轻量 watcher 语义，也容易因为项目目录在 Desktop 下触发 macOS TCC 后台权限拦截。当前只使用 `install_script_package_watcher_launch_agent.py` 安装登录自启 watcher，且 LaunchAgent 从非 Desktop runtime 目录启动。

只检查队列、不调用 Codex：

```bash
python3 scripts/watch_script_package_queue.py --once --dry-run --limit 5 --max-age-days 5
```

手动立即生成：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --limit 2 --max-age-days 5
```

指定单条生成：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --record-id <04_record_id>
```

## 2. 日常采集和选题

当前 watcher 只负责生成 `06`，不会生成新选题，也不会发送第一张选题卡。

每日采集和第一张选题卡发送由 Codex automation 触发，不使用本机 LaunchAgent：

- 08:00：从 project-owned SQLite 读取 exact 来源计划，然后跑全源采集和选题。
- 10:00：发送第一张选题卡。发送前会检查当天 `daily_pipeline` 是否成功、该 pipeline 指向的 exact run 候选 CSV 是否非空；不满足就跳过，避免误发旧候选。

### Three Fixed Tasks heartbeat 发布合同

官方 control plane 每个 thread 最多允许一条 active heartbeat，因此三条正式 schedule 必须一一绑定三个永久 fixed tasks。侧边栏长期保留并 pin：`AI账号工作流 08:00 全源采集`、`AI账号工作流 09:15 主编写回`、`AI账号工作流 10:00 选题卡`。08:00 复用现有 thread `019f87c1-17b3-7230-8a28-113386192d9d` 并在 Production 迁移时改名；09:15 与 10:00 由 Production 通过官方 `create_thread` 创建，真实返回 ID 必须立即 read-back 并写入 release evidence，不能预造或猜测 ID。

三个 fixed tasks 都使用 app-owned project `local-2eaa91c9bf61570374dd9dddad48808a` 的 local execution、`gpt-5.6-luna` / `medium`，并保持 pinned、可见、不自归档。官方只读 task/catalog API 不暴露 pinned boolean，因此 pin 证据必须来自官方 `set_thread_pinned` 成功回执与按 task slot 记录的精确 action count，不能伪造 read-back。09/10 新 task 各需一次 acknowledged pin 操作；08 task 已有 Phase 1 pinned 证据，除非 exact identity drift，不为制造证据重复 toggle。

09/10 新 task 的初始化 Prompt 只能要求原样返回一行 literal `FIXED_TASK_READY_JSON`，不得使用“初始化、设置、创建或配置永久调度”等可能触发控制面操作的措辞，不得输出 commentary，也不得调用任何 tool、shell、命令、文件、网络、automation API、thread API 或业务入口。JSON 中 `tool_calls`、`automation_mutations`、`thread_mutations`、`business_writes`、`external_calls` 必须全部为 0，但任务自报不是验收依据：Production 必须在每一步初始化前后独立快照 exact automation inventory 与 semantic hashes，以及 task/thread inventory；除当前步骤唯一授权新建的 task 外，清单必须零漂移，未授权 automation ID 必须为 0。task turn tool-call detail 可读时还必须证明 0；官方接口不暴露该细节时，必须明确记录限制，并以 automation/thread inventory 和 hash 零漂移作为权威证据。任何多余文字、调度创建暗示、tool call、控制面漂移或非零计数都在 pin、pilot、正式迁移前停止，只通过官方 API 清理本步骤精确授权的新 task 和越权对象，三条正式 cron 保持不动。

现有 08:00 task 已有 Phase 1 no-business proof；除非 identity drift，不重复 pilot。新建 09:15/10:00 task 必须各自先绑定一条临时 heartbeat，仅运行一次 no-business pilot：证明回合进入同一 task、standalone task 新增为 0、production cwd 的临时状态可 create/read/delete 且最终不存在、对应 scheduled-flow preflight 与 DNS green、`business_writes=0`、`external_calls=0`。pilot Prompt 同样明确禁止 automation/thread/control-plane mutation；它允许的工具调用仅限精确临时文件 create/read/delete 和对应 `scheduled_flow_preflight --check-network`，所以不得把 pilot 误报为 `tool_calls=0`。完成后立即删除临时 heartbeat，并通过官方 read-back 证明不存在，才可迁移正式 schedule。

正式迁移只能通过官方 API 把既有 `ai-rebuild`、`ai-04-rebuild`、`ai-rebuild-2` 原位从 cron 转为 heartbeat，并分别绑定 08/09/10 task。不得创建 replacement/duplicate，不得 raw TOML edit，不得 run、preview 或 catch-up。name、ACTIVE、08:00/09:15/10:00 schedule、model/reasoning、project/local context 和全部业务 Prompt 语义保持不变；每条 Prompt 只增加一次独立回合约束。

每个 heartbeat 回合只信任当前日期、当前 exact run 的 run-scoped artifacts 和 durable logs，不依赖 fixed task 历史对话结论。pipeline、scheduled-flow、write/read-back 与 idempotency logs 仍是权威状态；失败留在对应永久 task 中显性可见。固定 tasks 和 Prompts 均不得 self-archive / auto-archive。

正式迁移是 all-or-nothing：每次 update 后必须 official view 回读 same ID、`kind=heartbeat`、exact assigned thread ID、name/status/schedule/model/reasoning/project/local 与 Prompt hash。若任何转换、动态 thread ID、read-back 或字段/hash 不确定，立即把所有已改正式 ID 恢复到 manifest 中的 exact prior cron/ACTIVE 定义；移除正式 heartbeat 后 archive/unpin 本次新建的 09/10 empty/test tasks，并把现有 08 task 标题恢复为 `AI账号工作流 每日运行台`。回滚状态不明确时停止，禁止 raw edit 或猜测修复。

Git candidate 与 shared control docs 只在三条正式 heartbeat read-back 全绿后应用。发布过程不跑当日业务；第一次正常次日 08:00 / 09:15 / 10:00 链路才是生产业务证明，不用手工 recovery/catch-up 代替。

08:00 采集任务会使用 `--defer-editorial`：仓库脚本只负责同步来源、采集素材、写入 `03 内容收件箱`、生成 raw shortlist，然后停止在“等待外层 Codex 主编判断”状态。外层 Codex automation 必须使用 Git 管理的 `skills/ai-account-editorial-director/SKILL.md` 和 `topic_editorial_state_machine.py`，依次完成精确来源打开、网页研究、主编判断、无损排序与运营字段映射；不得调用旧 one-shot runner、环境切换 Skill 或 deterministic editorial fallback。发布时才把 repo Skill 显式同步到 global private Skill 并做 hash read-back。

抖音采集使用与 worktree 无关的 canonical Chrome profile。每天 `07:45` 先运行 `python3 scripts/check_douyin_session.py --port 9333`；只有 profile 进程身份精确匹配且 `login_state=logged_in` 才允许启动抖音 source probe。门禁失败会在任何账号导航前写入 typed 暂停状态，不会随机寻找其他浏览器、端口或 profile。运行中出现滑块、验证码、短信、challenge、登出或跨账号 XHR 风控信号时，后续账号导航立即为 0；用户在既有 fixed 9333 profile 手工验证后，通过 `/sources` fresh preflight 只恢复 exact remaining accounts。完整节奏与 checkpoint 合同见 `docs/ar048_douyin_risk_control.md`，profile 迁移与回滚见 `docs/douyin_canonical_profile_runbook.md`。

在外层 Codex 完成收尾前，`daily_pipeline_YYYY-MM-DD.json` 会保持 `ok=false`，所以 10:00 守卫不会误发 raw 候选卡。只有收尾脚本成功后，10:00 才会正常发卡。

Codex 定时任务负责触发本仓库脚本和执行外层主编 Skill；迁移时必须原位更新既有 automation ID，并保留这个“defer editorial -> outer Codex editorial -> finalizer”的边界。

`06 完整脚本与制作包` 是另一条本机生成链路：它由本机 LaunchAgent watcher 负责，只扫描飞书 `04` 中已确认且已提交制作方向的记录。空队列只做飞书 API 检查，不调用 Codex；有待生成记录时才调用本机 `codex exec` 和全局私有 Skill。

08:00 只有一条正式业务路径：每个来源执行一次，失败来源贡献零行；成功行进入一次完整 owner/candidate 计划，随后写入 03 并立即 exact read-back。历史 owner 在这次计划中复用 remote record ID，只更新本次参与字段，不注入历史正文，也不存在单独 recovery、mirror 或 latest 路径。

08:00 的来源配置权威是 `output/state/source_control.sqlite3`。入口在浏览器或 Feishu 03
写入前读取 SQLite exact revision，并把 run-scoped config projection 交给来源采集。正常调用图
不再 reconcile、读取或回写 Feishu 01，也没有 Feishu 配置 fallback；Feishu 01 只保留迁移前历史。

正式收尾入口在 `--write-feishu` 模式下会自行通过 `local_env.load_local_env(required=True)` 加载仓库本地环境，再执行网络预检和 Feishu 04 写入。生产默认读取 `.env.local`；staging/test 必须使用 `AI_ACCOUNT_RADAR_ENV` 或 `AI_ACCOUNT_RADAR_ENV_FILE` 选择对应环境。Prompt 中的 `cd` 只确定工作目录，不能提供环境变量或扩大可写权限。

WeWe 定时运行只在 ignored project state 使用
`output/state/wewe-refresh/refresh.lock` 作为互斥锁。每次运行只请求 provider
一次并立即读取 live SQLite；失败则 WeChat 为零行，其他成功来源继续。定时链
不读取或写入历史成功标记、签名 lease/attempt/receipt、恢复日志或缓存替代数据。
provider SQLite/data、auth 和 container identity 仍保留在 canonical owner-only
runtime，只读使用。

反馈规则：

- 08:00 采集成功：不主动发消息，避免打扰。
- 08:00 采集失败：通过飞书发送“AI账号雷达采集失败”，包含失败阶段、退出码、日志路径和错误摘要。
- 10:00 发卡成功：选题卡本身就是反馈。
- 10:00 因当天采集失败、候选为空或 exact run artifact 不是当天结果而跳过：通过飞书发送“AI账号雷达今日未发选题卡”。
- 10:00 发卡命令失败：通过飞书发送“AI账号雷达选题卡发送失败”。
- 06 生成成功：如果配置了 `FEISHU_SCRIPT_PACKAGE_FEEDBACK_RECEIVE_TARGETS`，生成器只发送一张“06 完整脚本与制作包已生成”汇总卡，集中列出本轮所有交付文档，并把质量反馈写回 06。预合并/测试时该目标只允许指向个人，避免打扰正式群。

默认通知目标读取 `FEISHU_AUTOMATION_NOTIFY_TARGETS`；如果没有配置，就复用 `FEISHU_CARD_RECEIVE_TARGETS`。手动排障不想发通知时可加 `--no-notify`。

手动只跑 08:00 全源采集任务：

```bash
python3 scripts/run_daily_collection_job.py --defer-editorial --no-notify
```

手动只跑 10:00 发卡检查：

```bash
python3 scripts/run_topic_card_if_fresh.py --no-notify
```

在项目目录运行：

```bash
python3 scripts/source_control_cli.py plan

# Hosted command bridge: exact endpoints and both credentials come only from runtime env.
# SOURCE_BRIDGE_SIWC_BYPASS_BEARER crosses the owner-only Sites SIWC gate via
# OAI-Sites-Authorization. SOURCE_BRIDGE_BEARER remains the Worker app credential.
python3 scripts/source_command_bridge.py --check-only
python3 scripts/source_command_bridge.py

# Long-running local bridge process.
python3 scripts/source_command_bridge.py --watch --interval-seconds 5

python3 scripts/daily_pipeline.py \
  --resolve-url-intake \
  --fetch-wechat-public-fulltext \
  --wechat-article-limit 1 \
  --douyin-account-limit 0 \
  --douyin-video-limit 3 \
  --write-feishu \
  --defer-editorial
```

日常来源配置以 `output/state/source_control.sqlite3` 为准，不需要先 dry-run。
Feishu `01 来源与采样` 仅是迁移前历史，不同步、不回写、也不作为 fallback。
owner control-plane permission and source repository credentials do not authorize runtime
bridge requests. The bridge requires the official Sites SIWC bypass bearer plus the
independent Worker app bearer; neither value may be passed as a CLI argument or logged.
第一条命令只读 SQLite exact revision；第二条命令对 URL intake、
WeWe、全部抖音跟踪账号和 AIHOT 各执行一次当前 run 采集，只把成功来源的
当前 run 行放入统一 owner/candidate plan，再写入 `03 内容收件箱` 并生成 raw
`today_10_topics.csv`。来源失败贡献零行，其他安全来源继续；不会读取 cache、
历史 artifact、retry artifact 或 manual substitute。定时路径不会启动 Docker、
启动浏览器或执行登录。

## 3. 开发验证和排障

dry-run 只用于改代码、改采集规则或排查字段问题，不作为日常步骤：

```bash
python3 scripts/daily_pipeline.py --resolve-url-intake
```

隔离测试可显式跳过 AIHOT；该参数不在正式 wrapper 中使用：

```bash
python3 scripts/daily_pipeline.py --no-fetch-aihot
```

只测试 Skill 或标题判断时，不要重新采集。用当前 Codex 任务状态机复用只读 `content_items.csv`：

```bash
PYTHONPATH=scripts python3 scripts/topic_editorial_state_machine.py prepare-source-open \
  --out-dir /private/tmp/ar020d_current_task_replay \
  --persona-docx "/absolute/private/path/to/我的案例库.docx" \
  --content-csv output/runs/<run_id>/content_items.csv \
  --since <YYYY-MM-DD> \
  --batch-size 3
```

当前 Codex 任务随后按 `validate-source-open -> prepare-research -> validate-research -> prepare-stage1 -> validate-stage1 -> prepare-ranking -> validate-ranking -> prepare-stage2 -> validate-stage2 -> finalize` 协议执行。精确来源和研究失败均 fail closed；旧 one-shot/deterministic/nested CLI 会在读写业务输出前失败。

同日正式候选恢复不得重新从 `content_items.csv` 抽样。先计算批准文件的
SHA256，再用显式 exact-input 模式只读锁定全部行：

```bash
python3 scripts/topic_editorial_state_machine.py check-exact-input \\
  --run-id <run_id> \\
  --exact-input-csv output/runs/<run_id>/today_10_topics.csv \\
  --exact-input-sha256 <sha256>

python3 scripts/topic_editorial_state_machine.py prepare-source-open \\
  --out-dir /private/tmp/<fresh-run> \\
  --persona-docx "/absolute/private/path/to/我的案例库.docx" \\
  --run-id <run_id> \\
  --exact-input-csv output/runs/<run_id>/today_10_topics.csv \\
  --exact-input-sha256 <sha256> \\
  --batch-size 3
```

exact-input 会锁定规范 run 路径、文件 SHA256、行顺序、精确 URL、来源 owner
字段和候选指纹。缺失、增加、重复、重排或替换都必须在下游开始前失败，且失败
候选不得由其他内容补位。

写入边界：

- 只写入 `04 分析与选题` 的今日候选池。
- 正式输出只以 `output/runs/<run_id>/` 为权威路径；主编、04 和 Topic Card 必须使用同一 exact run。
- 不写入被淘汰的调试候选。
- 不新增业务表。
- 不自动发布。
- 定时采集/选题阶段不生成完整成稿；已确认选题的口播稿与执行包由本机轻量 watcher 按需生成。
- 不强抓抖音、小红书、视频号。

## 4. 所需环境变量

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BASE_APP_TOKEN`
- `CODEX_BIN` 可选；默认 `/Applications/Codex.app/Contents/Resources/codex`

不要把这些值写入仓库。建议放在本机 shell 配置、密码管理器或本机 `.env` 文件里；`.env` 已被 `.gitignore` 排除。

## 5. 失败后查看日志

daily pipeline 会写入：

```text
output/logs/daily_pipeline_YYYY-MM-DD.json
```

06 watcher 会写入：

```text
output/logs/script_package_watcher_YYYY-MM-DD.log
```

06 runner 会写入：

```text
output/logs/codex_script_package_runner_YYYY-MM-DD.log
```

常见处理：

- AIHOT 失败：流程仍会继续，可用 `--no-fetch-aihot` 或手动补充内容。
- 飞书环境变量缺失：补齐 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BASE_APP_TOKEN` 后重跑。
- 飞书权限不足：检查自建应用是否有多维表格读写权限。
- Codex 失败：确认 Codex 桌面端已登录，`/Applications/Codex.app/Contents/Resources/codex exec` 可在终端运行。
- watcher 不触发：先运行 `python3 scripts/install_script_package_watcher_launch_agent.py --status`，再查看 `~/Library/Logs/ai-account-radar/script_package_watcher_launch_agent.err.log` 和 `output/logs/script_package_watcher_YYYY-MM-DD.log`。

## 6. 手动重跑

正式写入：

```bash
FEISHU_APP_ID=你的AppID \
FEISHU_APP_SECRET=你的AppSecret \
FEISHU_BASE_APP_TOKEN=你的BaseAppToken \
python3 scripts/daily_pipeline.py --write-feishu
```

生成 06 脚本包：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --limit 2 --max-age-days 5
```
