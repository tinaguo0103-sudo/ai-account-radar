# AR-020B Skill Contract Implementation

日期：2026-07-07

状态：Ready for QA / AR-020B Skill Contract Review

## 目标

AR-020B 不继续修 PM report，也不让 deterministic replay 假装主编。目标是把 Austin-fit 判断、来源转译、推荐动作和 04 / Topic Card / 06 会消费的主字段重新交给 `ai-account-editorial-director`，同时用确定性代码做来源治理、候选池、fallback 标记和字段契约校验。

## 实现边界

本轮不修改全局私有 Skill，只更新仓库 mirror 和 runner contract。生产发布前必须另行决定全局私有 Skill 同步和回滚策略。

未写生产、未写飞书、未发 Topic Card、未触发采集、未触发 06。

## 关键变更

### Skill contract

`skills/ai-account-editorial-director/SKILL.md` 增加 AR-020B 字段契约：

- Skill 是 `选题命题`、`一句话Brief`、`我要做的实验`、`我的工作流痛点`、`旧流程痛点`、`AI介入点`、`验证方式`、`可沉淀资产`、`我的思考点`、`重点体现`、`对应方向`、`推荐动作`、`今日建议级别`、`title_permission`、`可发布标题` 等主字段的 owner。
- `来源权重类型`、`来源构成`、`原始来源标题`、`原始来源账号`、`AIHOT重大性说明`、`市场验证依据` 是事实证据。
- `Austin映射方向`、`Austin转译角度`、`主题簇`、`Austin转译质量` 只是 pre-Skill hint，不能盖过来源证据和用户账号现场。

### Runner context

`scripts/editorial_skill_runner.py` 现在会把 source governance evidence、字段契约 guardrail、source weight、AI Hot 重大性、对标账号、市场验证、主题 hint 等传给 Skill。

输出会标记：

- `editorial_engine=codex`
- `fallback_only=false`
- `not_editorial_quality=false`

显式 deterministic fallback 会标记：

- `editorial_engine=deterministic`
- `fallback_only=true`
- `not_editorial_quality=true`

### Field contract validator

新增 `scripts/topic_field_contract.py`，检查：

- 知识库 / Obsidian / RAG 来源不得在主字段残留 AI视频 / 分镜 / 成片验收，除非来源本身有视频证据。
- AI 视频 / 分镜来源不得错落成纯知识库 / 办公表格。
- AI Hot 可行动候选必须有重大性说明和 Austin 角度。
- `推荐动作=生成脚本包` 必须有可执行实验、验证方式和非 `不生成标题` 的 title permission。
- 字段契约失败会降级为 `暂存观察`，清空可发布标题，并写入 `field_contract_issues`。

### 04 write guard

`scripts/push_today10_to_feishu.py` 不再为缺失的 Skill 主字段创造实验、验证或痛点。只有 `editorial_engine=codex`、非 fallback、字段契约通过、且实验动作可执行的行才会进入 04 今日候选。

### Real Skill replay

新增 `scripts/topic_skill_replay_evaluation.py`：

1. 只读加载 2026-07-01+ `content_items.csv`。
2. 使用 deterministic code 只组装 broad review pool。
3. 调用 `editorial_skill_runner.py` 的真实 Skill 路径。
4. 输出 actionable / observe / rejected / contract failures / fallback rows / sample table。

推荐命令：

```bash
PYTHONPATH=scripts PYTHONPYCACHEPREFIX=/private/tmp/ai_account_radar_pycache \
python3 scripts/topic_skill_replay_evaluation.py \
  --since 2026-07-01 \
  --content-csv /path/to/output/runs/run_YYYYMMDD_HHMMSS/content_items.csv \
  --out-dir /private/tmp/ar020b_skill_replay_<date> \
  --engine codex
```

如果 replay 输出 `fallback_row_count > 0` 或 `contract_failure_count > 0`，不能标记 AR-020B 内容质量通过。

本轮开发验证输出：

- 输出目录：`/private/tmp/ar020b_skill_replay_20260707_dev_v3`
- `content_items=273`
- `candidate_count=34`
- `pre_skill_pool_count=16`
- `skill_rows=16`
- `actionable_count=4`
- `observe_count=12`
- `contract_failure_count=0`
- `fallback_row_count=0`
- `reverse_flags=0`
- `writes_feishu=false`

关键样例：

- `Codex联动Obsidian...知识库`：纠偏为 `真实工作流改造`，实验落到资料进入选题台和脚本包，不再残留 AI 视频 / 分镜 / 成片验收。
- `多宫格故事板2.0`：保留 `AI导演工作流`，实验落到分镜返修验收。
- `Mx-Shell Skill`：保留为可选候选，方向为 `AI导演工作流`，但不冒充今日最强。
- `CI/CD + 自动化 Shell`：降级为 `暂存观察 / 观察`，不作为可行动候选。
- 泛增长 / 企业 AI 来源：保留为 `补证据 / 可选候选`，需要明确项目和交付证据后才能推进。

## QA 关注点

- 看 `skill_replay_rows.csv` 主字段，不只看 report。
- 抽查 `skill_sample_table.csv` 中知识库、AI导演、Mx-Shell/Skill、CI/CD Shell、泛企业/AI Hot 样例。
- 如果任何 actionable row 来自 fallback，必须打回。
- 如果 contract failure 仍进入 04/Topic Card-facing 输出，必须打回。

## L3 Topic Card UX Rework

2026-07-07 L3 staging/test 返修后，Topic Card 承载层新增三条边界：

- 04 写入字段补齐 `推荐动作`、`title_permission`、`可发布标题`，避免卡片层只能靠 `今日建议级别` 猜测能否生成脚本包。
- Topic Card 将 `生成脚本包` 候选和 `补证据 / 可选候选 / 缺发布标题` 候选分开；只有可生成候选进入多选框，补证据候选只展示判断和缺口，不进入 06。
- `build/send` 支持 `--strict-run-id` 和 `--record-id`，用于 L3 run-specific 测试隔离，避免 AR-013 补偿池混入旧测试记录。

只读 staging preview 验证：

```bash
AI_ACCOUNT_RADAR_ENV_FILE=.env.staging.local \
PYTHONPYCACHEPREFIX=/private/tmp/ai_account_radar_pycache \
python3 scripts/feishu_topic_decision_card.py build \
  --run-id ar020b_l3_20260707_134926 \
  --limit 10 \
  --strict-run-id \
  --include-decided
```

结果：

- `record_count=7`
- `strict_run_id=true`
- `coverage_dates=["2026-07-07"]`
- `candidate_ids=3`
- `supplement_candidate_ids=4`
- 预览中不再包含旧 `[AR-018 TARGET TEST]` 记录。
- 预览文案包含 `可生成候选：3 条｜补证据/观察候选：4 条` 和 `不会进入下方“生成脚本包”勾选列表`。
