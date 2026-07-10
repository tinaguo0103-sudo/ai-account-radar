# AR-020C Adversarial Structure Review

日期：2026-07-10

状态：Docs-only Adversarial Review Done / No Production Action

本轮只做结构审查：未改业务代码，未写 Feishu，未发 Topic Card，未触发采集，未触发 06/Codex 脚本生成，未同步 global Skill，未部署 SCF/runtime。本报告不是继续给 QA 通过/失败结论，而是反查“为什么标题仍像原始标题扩写或结构化模板”的结构根因。

## 结论

当前问题不能再定义成单纯“标题表达返修”。它至少分成三类：

1. **真实生成链路已加载 global private Skill，但 self-validation 报告话术仍写成 repo mirror/persona/context，证据口径不准。** `editorial_skill_runner.py` 默认读取 `/Users/congcong/.codex/skills/ai-account-editorial-director`，fresh rows 也记录 `Skill参考文件=/Users/congcong/.codex/skills/.../persona-and-cases.md`。但 replay `execution_note` 说“repo mirror/persona/context”，这会误导 PM 以为 repo 脱敏镜像参与了主生成。
2. **原始标题钩子确实进入模型输入，不只是报告装饰；但案例库人格驱动只被弱证明。** prompt 会嵌入 `SKILL.md` 和 `persona-brief.md`，也会把 `原始标题钩子` 写入 candidate payload；完整 `persona-and-cases.md` 只以“参考路径”形式出现，没有全文进入上下文。产物里出现案例/母场景，不足以证明模型真的调用了完整案例库判断。
3. **模板感的主因是“结构化字段一次性生成 + workflow gate 语言锚点”，不是 card mapping 或报告展示单独造成。** `SKILL_FIELDS` 要求模型一次输出几十个字段；prompt 同时要求 `editorial_thinking_json`、`field_mapping_json`、命题、实验、验证、资产、标题。模型很容易把“我要做的实验/验证方式”的内部工作语言泄漏到用户可见标题面。
4. **当前开发自修方向仍偏 validator/禁句补丁，不能证明自然仿写和人格判断发生。** 它能拦更多内部工单壳，也修正了一个 validator 误伤面；但如果开发方案只停在禁词、只改报告、只增字段、只改 title guard，应直接判结构返修失败。

PM 的架构选择建议：继续走 `Editorial Thinking -> Field Mapping`，但要把它做成可审计的两阶段证据，而不是在一个 prompt 里同时“自由判断 + 填表”。第一阶段必须产出可读的 Austin 主编判断和标题仿写依据；第二阶段只能映射字段。验收时要证明第一阶段已经自然、具体、案例驱动，而不是靠第二阶段 guard 把坏标题拦掉。

## 根因分类

### Confirmed root cause

1. **运行时 contract owner 不一致。** `editorial_skill_runner.py` 默认加载 global private Skill，而不是 repo mirror。repo mirror 已经加入 AR-020C 两段式 contract，但 default runtime 仍嵌入 global private Skill 文本；global Skill 的主流程仍是 `Gate -> Workflow Experiment Card -> Title Packaging`。因此“repo mirror 看起来正确”不能证明真实 replay 的 contract 已更新。
2. **自由主编判断与字段映射在同一 prompt/schema 内被同时要求。** runner 要求 `editorial_thinking_json`、`field_mapping_json` 和全部 `SKILL_FIELDS` 一次性产出，同时保留命题、实验、验证、资产、标题包装等字段。模型会自然把 public proposition 先压成 workflow experiment card。
3. **Pre-Skill deterministic shaping 过重。** `topic_flow_rework.py`、`content_sampler.py` 和 runner 的 mother-scene/hint 机制虽然被标注为 `non_authoritative_hints`，但它们在 prompt 中仍是高密度、近距离的主题/措辞/场景材料，容易把 Skill 引向 `验收 / 交付 / 能不能 / 先看` 的旧表达。
4. **质量 guard 是后置拦截，不是自然表达生成器。** `topic_field_contract.py` 能 downgrade/fail，但不能把已经生成的任务壳重新写成自然公开判断。
5. **persona/case-library grounding 是弱证明。** runner 嵌入 `persona-brief.md`，但完整 `persona-and-cases.md` 主要以路径形式出现，没有 candidate-level retrieval artifact。用户案例库还没有成为标题/命题生成的第一层证据。

### Likely contributor

1. **严格 schema 与批量输出放大同构。** `additionalProperties=false` 和全字段 required 有利于机器处理，但在一批候选中会放大相同字段顺序和句法骨架。
2. **观察/补证据候选的 title-quality scan 曾混入工作字段。** Agent 例子显示，标题本身自然时，`我要做的实验 / 验证方式 / 标题思路` 的工作语言也可能污染 title-quality 结论。这是 validator 误伤来源，不是模板生成根因。
3. **PM sample package 可以改善展示，但不能修复主字段。** report 能拆开原始标题、摘录、hook、rewrite reason；它能减少误读，但如果 `field_mapping_json` 已经产出任务壳，report 不能证明内容质量。

### Not a root cause

1. **04 / Topic Card consumer 不是任务壳首发点。** 04 和 Topic Card 主要展示上游字段；它们会放大用户可见影响，但没有发明 Storyboard、Claude Cowork、MIRA 的任务壳。
2. **deterministic fallback 不是本轮失败的接受路径。** fresh QA rows 为 real Skill output，`fallback_row_count=0`。问题不是 fallback 被误当质量通过，而是 real Skill 被结构化 prompt/schema 拉回旧表达。
3. **原始标题钩子不是纯报告装饰。** hook 已进入 candidate payload 和真实 Skill 输出；问题是它被放在字段映射压力之前，最后变成 `市场钩子 + workflow gate`，而不是 `市场钩子 + Austin 人格判断`。

## 真实运行时加载链路

### 运行入口

- `scripts/topic_skill_replay_evaluation.py:21-25` 导入 `editorial_skill_runner` 并声明 replay 不写 Feishu，只由 Skill 拥有用户可见字段。
- `scripts/editorial_skill_runner.py:31-35` 定义 `GLOBAL_SKILL_DIR = Path.home() / ".codex" / "skills" / "ai-account-editorial-director"`，`SKILL_DIR` 默认取 global private Skill。
- `scripts/editorial_skill_runner.py:38-51` 的 `skill_reference_dirs()` 只返回 `SKILL_DIR`，不会隐式 fallback 到 repo mirror。
- `scripts/editorial_skill_runner.py:1547-1550` 在 prompt 中读取 `SKILL_MD` 和 `SKILL_PERSONA_BRIEF`。
- `scripts/editorial_skill_runner.py:1673-1683` 把 `editorial_rule_text`、`persona-brief.md` 和 `案例/人设参考路径` 写进 prompt，但明确“本次不把完整长文全部塞入上下文”。
- `scripts/editorial_skill_runner.py:1707-1729` 使用 `codex exec --ephemeral --sandbox read-only -C <repo>` 执行，输入是 `build_codex_prompt(rows)`，不是直接调用 Skill 工具。
- `scripts/editorial_skill_runner.py:1753-1757` 对 Codex 成功输出标记 `Skill编辑层=ai-account-editorial-director`、`editorial_engine=codex`、`fallback_only=false`。
- `scripts/editorial_skill_runner.py:1818-1842` 只有显式 `--engine deterministic` 或允许 fallback 时才走 deterministic；否则 Codex 失败会抛错。

### Global private 与 repo mirror 的证据边界

证据显示实际生成使用 global private Skill：

- fresh QA rows 的 `Skill参考文件` 均为 `/Users/congcong/.codex/skills/ai-account-editorial-director/references/persona-and-cases.md`。
- global private reference 文件体量明显大于 repo mirror：`persona-brief.md` 19602 bytes vs repo 6789 bytes；`persona-and-cases.md` 32842 bytes vs repo 5396 bytes。
- global private Skill 明确要求“先读 persona-brief，persona-and-cases 用来追溯细节”：`/Users/congcong/.codex/skills/ai-account-editorial-director/SKILL.md:34-44`。

但 replay 元数据的 `execution_note` 不准确：

- `scripts/topic_skill_replay_evaluation.py:398-411` 把每批 meta 写成“repo mirror/persona/context 执行主编合约；未在本进程内另行调用全局私有 Skill 工具”。
- 这句话只适合表达“没有另行调用外部 Skill 工具”，不适合表达真实加载路径。真实路径是 runner 把 global private Skill 文本和 persona brief 嵌入 prompt，再让 `codex exec` 生成。

验收要求：下一版必须把 runtime provenance 分开写清：

- `skill_text_path`: 实际 `SKILL.md` 路径。
- `persona_brief_embedded`: true/false + path/hash。
- `case_library_embedded`: true/false + path/hash。
- `case_library_reference_only`: true/false。
- `execution_mode`: embedded prompt via `codex exec`，不是 global Skill tool call。

## 反向追踪：五个样例的结构来源

### 1. Codex + Obsidian

fresh QA 产物：

- 原始标题钩子：`Codex+Obsidian工具组合 / 自生长知识库 / 定时抓热点 / 自动整理复盘 / 输出文档、PPT、视频 / 手把手教程入口`
- publish title：`我做选题台后才发现，知识库最该存的不是资料，是为什么选它`
- topic：`Codex+Obsidian的知识库入口，真正适合拿来补我的选题台长期记忆`
- 结论：这是目前最接近“原始标题入口 + Austin 判断”的样例。它保留市场入口，但最终标题不照抄，也没有教程化。

结构追踪：

- `compact_candidate()` 会把 `原始标题钩子` 放进 payload：`scripts/editorial_skill_runner.py:1463-1471`。
- 同时它也把 `source_facts`、`source_governance_evidence`、`关联母场景候选` 一并传入：`scripts/editorial_skill_runner.py:1468-1503`。
- 该样例能落到“选题台长期记忆”，说明 persona brief / 母场景候选有参与；但因为完整案例库只给路径，不能证明“案例库细节”参与了生成。

### 2. Storyboard

fresh QA 产物：

- topic：`多宫格故事板的“一键成片”，要放进我的分镜返修流程里才算数`
- title/contract：`title_quality_status=fail`，但失败原因显示为“测试/验证/能不能类骨架占比过高”。
- 主编判断摘要写“放进 Austin 的商业短片交付现场，做一次10秒Brief到分镜返修实验”。

当前 dev self-validation r3 产物：

- publish title：`多宫格故事板再简单，商业短片最后还是要过分镜返修`
- topic：`多宫格故事板的“一键成片”承诺，真正要过的是我的分镜返修验收`
- meta 称 title quality 全 pass，但 `DEV_SELF_ACCEPTANCE.md` 自己仍判“internal process gate”。

结构判断：

- 这不是 card mapping 改写，也不是 fallback。fresh row 为 `fallback_only=false`，`editorial_engine=codex`。
- 原始标题钩子已经进模型输入，并在输出里保留“一键成片/自动分镜”。
- 问题发生在 Skill 生成的 `field_mapping_json -> 选题命题/可发布标题`：模型把“实验/验收流程”写成标题面。
- 当前 r3 把禁句绕开后，仍是“商业短片最后还是要过分镜返修”，语义上还是流程门禁标题。validator pass 不等于自然 Austin 标题。

最小反例：

```text
原始标题：多宫格故事板2.0，出视频比你想的还简单
坏输出：多宫格故事板的“一键成片”承诺，真正要过的是我的分镜返修验收
为什么坏：它看似保留市场入口，但句子中心是验收门槛，不是用户会自然点开的业务矛盾。
更接近验收方向：多宫格故事板让分镜变快了，但商业短片最贵的还是返修责任
```

### 3. Claude Cowork

fresh QA 产物：

- topic：`Claude Cowork 的入口很热，但我更想把它改成内容团队的协作验收链路`
- `title_quality_status=fail`
- 这是典型“我更想把 X 改成 Y”的内部任务壳。

r3 self-validation 产物：

- publish title：`我现在缺的不是 AI 同事，而是它做完事后留下验收记录`
- topic：`Claude Cowork 这种 AI 同事，真正要接住的是内容团队的任务状态和验收记录`
- `title_quality_status=pass`

结构判断：

- r3 的 Claude Cowork 比 fresh QA 明显自然，但它仍需证明不是禁句替换后的单点好转。
- 该样例的“AI 同事 -> 验收记录”很容易由 prompt 中的 `Agent/飞书执行台/验收记录` 母场景生成；还不能证明完整案例库驱动了选择。
- 如果开发方案只展示 Claude Cowork 这一条，不能说明 Storyboard/MIRA/Agent/Codex+Obsidian 全部自然化。

### 4. MIRA

fresh QA 产物：

- topic：`MIRA 的实时世界模型很抓人，卡点是它还没过我的商业短片验收`
- `title_quality_status=pass`

r3 self-validation 产物：

- topic：`MIRA 的实时世界模型很抓人，但商业视频交付还缺分镜和成片验收证据`
- `title_quality_status=pass`

结构判断：

- 这是“观察/补证据候选”较好的方向：保留 MIRA、实时世界模型、20 FPS 等市场入口，同时明确证据缺口。
- 但它仍依赖“商业短片验收”这一常见母场景。如果同批大量 AI 视频都落到“过验收/返修/交付”，会形成新的 Austin 模板。

### 5. Agent / 飞书执行台

fresh QA 产物：

- topic：`Agent真正有用的能力，是做完事以后留下可验收记录`
- `title_pattern_family=freeform`，但 `title_quality_status=fail`，失败文案仍是“测试/验证/能不能类骨架占比过高”。

结构判断：

- 这是 validator 错误阻断，不是标题本身明显失败。它没有 `能不能/会不会/我更想把` 这类标题壳。
- 当前未提交 dev 改动把 `title_for_quality()` 调整为真实展示回退顺序，并把 `我要做的实验/验证方式/标题思路` 拆到 non-blocking `work_expression_audit`：`scripts/topic_field_contract.py:168-183`、`scripts/topic_field_contract.py:237-244`、`scripts/topic_field_contract.py:409-502`、`scripts/topic_field_contract.py:505-534`。
- 这个修复方向合理，但它只解决 validator 扫描面，不能解决生成策略本身。

### 6. Codex PPT

fresh QA 产物：

- publish title：`Codex 做 PPT 真正有用的地方，是把 Word 方案变成可交付资产`
- topic：`Codex 可编辑 PPT 的重点不是五步教程，是能不能把 Word 方案变成可交付资产`
- `title_quality_status=pass`

结构判断：

- publish title 是合格方向：借 `Codex + Word + 可编辑 PPT`，落到“方案交付资产”。
- topic 仍带 `能不能`，但因为 publish title 优先展示，用户可见标题面不受影响。
- 这支持 current validator 改动：质量门应按 `可发布标题 -> 选题命题 -> 我的选题标题 -> 选题标题` 的真实展示顺序检查，而不是扫描所有工作字段。

## 标题表达问题 vs 内容策略问题 vs 报告展示问题

### A. 标题表达问题

特征：原始钩子进来了，但最终句子仍像内部测试/验收门禁。

样例：

- Storyboard：`要放进我的分镜返修流程里才算数`
- Claude Cowork：`我更想把它改成内容团队的协作验收链路`
- r3 Storyboard：`真正要过的是我的分镜返修验收`

根因：模型知道“不能做工具教程”，但没有学会“自然地说出 Austin 的业务矛盾”。于是它把工作流实验的验收规则提前写到标题面。

### B. 内容策略问题

特征：选题切入仍由母场景和字段契约强牵引，而不是由案例库里的真实经历牵引。

证据：

- `MOTHER_SCENES` 是代码内置的泛化场景：`AI账号信息雷达 / 飞书执行台`、`商业视频 / AI导演工作流` 等，见 `scripts/editorial_skill_runner.py:266-280`。
- `real_tension()` 根据关键词返回固定冲突，例如视频类固定到“分镜、返修和成片验收”，Agent 类固定到“任务、状态、异常和验收记录”：`scripts/editorial_skill_runner.py:1210-1233`。
- deterministic fallback 的 `enrich()` 会大量补齐主字段并标记 `fallback_only=true`：`scripts/editorial_skill_runner.py:1374-1431`。本轮 fresh rows 不是 fallback，但这些固定场景仍作为 prompt context 和候选 hints 存在。

判断：不是这些母场景错，而是它们只能当输入线索。验收必须要求模型说明“这条为什么不是又一次套到商业视频验收/飞书记录”，而不是只写“落到我的现场”。

### C. Validator 错误阻断

特征：标题可读，但质量门因扫描了非标题工作字段而 fail。

样例：

- Agent：`Agent真正有用的能力，是做完事以后留下可验收记录` 被 fail。

当前 dev 未提交改动已往正确方向修：

- 标题质量只看真实展示字段：`scripts/topic_field_contract.py:168-183`、`scripts/topic_field_contract.py:237-244`。
- 工作字段另设 non-blocking audit：`scripts/topic_field_contract.py:505-534`。

验收注意：这个修复只能消除误伤，不能被当作标题质量已通过的证据。

### D. 报告展示问题

`ar020c_user_sample_summary.md` 目前能清楚拆开：

- 内部分类；
- 原始标题；
- 原始来源摘录；
- 原始标题钩子；
- Austin rewrite reason；
- publish title；
- topic；
- experiment。

对应代码在 `scripts/topic_skill_replay_evaluation.py:656-703` 和 `scripts/topic_skill_replay_evaluation.py:706-756`。这部分不是主要根因。

但 report/provenance 仍有两处风险：

- execution note 写 repo mirror，和真实 global private 加载路径不一致：`scripts/topic_skill_replay_evaluation.py:398-411`。
- r3 self-validation 自身不自洽：`DEV_SELF_ACCEPTANCE.md` 写“Completed before stop decision: 1 batch / 3 candidates”，但同目录 `skill_replay_progress.csv` 和 batch meta 显示 batch_000 到 batch_005 已 success，batch_006 已 start 后无完成聚合。该 self-validation 不能作为验收证据，只能作为开发自查材料。

## 原始标题钩子是否只是报告装饰

不是。它已经进入 fresh Skill 输入：

- `scripts/editorial_skill_runner.py:562-574` 会从原始标题抽取 fallback hook。
- `scripts/editorial_skill_runner.py:1463-1471` 把 `原始标题钩子` 和 `source_title_hook` 写进 candidate payload。
- `scripts/editorial_skill_runner.py:1587-1590` prompt 明确要求先提取钩子，再说明借了什么、改了什么。
- fresh rows 的 `原始标题钩子` 和 `Austin改写理由` 在 Storyboard、Codex PPT、Claude Cowork、MIRA、Codex+Obsidian 均存在，且内容与来源标题对应。

但它还没有被证明是“选择/标题自然化”的主驱动：

- Storyboard 有正确钩子，却仍输出任务壳。
- Claude Cowork 有正确钩子，却仍输出“我更想把它改成...”。
- Codex+Obsidian 和 Codex PPT 成功，但更像局部成功，不足以证明结构已经稳定。

验收要求：开发必须证明 hook 影响的是 `chosen_angle` 和最终 title，而不是只填入 `原始标题钩子/Austin改写理由` 两个解释字段。

## 对开发结构方案的验收口径

### 必须证明的事情

1. **运行时 provenance 可审计。** 每次 replay summary 和 batch meta 必须记录真实 `SKILL.md` 路径、persona brief 路径/hash、case library 是 embedded 还是 reference-only、是否使用 deterministic fallback。
2. **案例库/人设驱动可见。** 每条强候选必须输出 `case_anchor_used` 或等价字段：引用的是哪类真实/相邻案例、为什么相关、哪些不能声称。不能只写“飞书/导演/验收”泛词。
3. **原始标题钩子影响 chosen angle。** `editorial_thinking_json` 里必须能看到 `source_title_hook -> rejected_common_take -> chosen_austin_angle -> final_title` 的链路。
4. **第一阶段自然判断先成立。** 在字段映射前，单独产出 60-120 字 Austin 主编判断和 2-3 个标题方向；它们不应包含字段名、内部测试指令或“我要做的实验”句式。
5. **第二阶段只映射字段。** `field_mapping_json` 不能发明新角度，不能把 `non_authoritative_hints` 或旧字段复制到主字段。
6. **最小反例必须过。** Storyboard、Claude Cowork、MIRA、Agent、Codex+Obsidian、Codex PPT 都要能在 fresh real replay 中展示自然标题/命题，不靠手工挑样。
7. **guard 只能防错，不能制造成功。** `quality_gate_ok=true` 之外，还必须有 PM 可读样例证明标题不是禁词绕过。

### 必须直接判失败的伪修复

- 只新增禁词或正则，使旧标题被降级，但不改善 fresh generation。
- 只改 `ar020c_user_sample_summary.md` 展示，把坏标题藏进 topic 或 excerpt。
- 只新增 `原始标题钩子/Austin改写理由` 字段，但 `editorial_thinking_json` 和最终 title 不使用它们。
- 只调整 `title_quality_issues`，让 Storyboard/Claude Cowork pass，但样例仍是“过验收/接进流程/留下记录”的模板壳。
- 只把 `可发布标题` 写好，`选题命题` 继续像内部任务；因为无 publish title 的观察/补证据行仍会暴露 topic。
- 只证明 1-2 条好样例，不做 2026-07-01+ fresh full replay。
- 用 deterministic fallback、aggregate-only、旧 QA rows 或 report 后处理当作内容质量证据。

## 必须通过的最小测试样例

这些样例应加入 focused tests 或 replay fixture。测试不是要求固定输出，而是要求“失败模式不可再出现”。

### Storyboard

输入锚点：

```text
多宫格故事板2.0，出视频比你想的还简单；一张参考图 + 人话想法；自动生成完整分镜故事板；一键直出成片。
```

必须通过：

- `原始标题钩子` 包含 `故事板/一键成片/参考图/人话想法/自动分镜`。
- 最终展示标题不能含 `要放进/才算数/我更想把/能不能/会不会/正好拿来/验收流程里看`。
- 可接受方向：市场承诺 vs 商业短片返修责任；不能只写“过我的分镜返修验收”。

### Claude Cowork

输入锚点：

```text
人们如何使用 Claude Cowork；AI 同事；官方使用入口；协作场景。
```

必须通过：

- 不写 `我更想把它改成...`。
- 不写工具教程或功能科普。
- 可接受方向：AI 同事的角色感 vs 内容团队任务边界、状态回填、验收记录。

### MIRA

输入锚点：

```text
MIRA：可玩多人世界模型，20 FPS 实时生成“火箭联盟的梦”。
```

必须通过：

- 保留 `MIRA/实时世界模型/20 FPS/可玩世界` 入口。
- 因证据不足时只观察或补证据，不进入生成脚本包。
- 观察命题必须是公开证据缺口，不是“我会先把它放进...看”。

### Agent / 飞书执行台

输入锚点：

```text
我们到底在用 agent 的什么能力？
```

必须通过：

- `Agent真正有用的能力，是做完事以后留下可验收记录` 这类自然判断不应被 validator 误伤。
- 如果工作字段有“验证/检查/验收”，只能进入 non-blocking audit，不得改变 title quality。

### Codex + Obsidian

输入锚点：

```text
Codex联动Obsidian，搭建超强知识库，手把手教程；自生长知识库；自动整理复盘。
```

必须通过：

- 借 `Codex+Obsidian/知识库/复盘`，不做搭库教程。
- 标题应落到选题判断、长期记忆、资料复用，不复制“手把手教程/next level”。

### Codex PPT

输入锚点：

```text
Codex生成可编辑PPT，按这5步就够了；Word 文档生成高级可编辑 PPT；不是整页图片。
```

必须通过：

- publish title 优先展示时，validator 必须按展示回退顺序检查。
- 标题可借 `Word -> 可编辑 PPT`，落到方案交付、可返修、可复用。

## 建议的结构改造方向

### 方案 A：persona/case-library retrieval-first + free editorial judgment + field mapping

推荐作为产品方向。

流程：

1. 先从 private case library 里按 source hook、账号方向、市场表达、候选类型检索 3-5 条相关案例片段。
2. 第一阶段只让 Skill 输出 public editorial judgment：`source_read`、`source_title_hook`、`case_anchor_used`、`why_i_would_choose`、`why_i_would_not_choose`、`chosen_austin_angle`、`public_title_direction`、`near_miss_reason`。
3. 第二阶段再把第一阶段结果映射到 04 / Topic Card / 06 字段。
4. replay artifact 必须保留 retrieved case snippets / case ids，让 PM 能看到人格案例如何影响判断。

用户可见效果：

- 标题更像 Austin 根据真实案例和账号人格作判断，而不是把来源塞进一个流程验收卡。
- 原始标题钩子仍可借用，但会先和 Austin 案例发生关系，再进入字段映射。

迁移风险：

- 需要更新 global private Skill 与 repo mirror 的同步策略。
- token/runtime 成本上升；需要继续依赖 batch/resume replay。
- case retrieval 质量会成为新风险，需要 artifact 证明。

测试证据：

- 每条强候选有 `case_anchor_used` 和 hook-to-angle trace。
- Storyboard、Claude Cowork、MIRA、Agent、Codex+Obsidian、Codex PPT 六类反例必须 fresh replay 通过。

### 方案 B：两次 Skill 运行，强隔离 judgment 与 mapper

第一阶段：`editorial_thinking_only`

- 输入：source facts、原始标题钩子、persona brief、明确的案例锚点摘要。
- 输出：`source_read`、`source_title_hook`、`case_anchor_used`、`rejected_common_take`、`chosen_austin_angle`、`public_title_direction`、`decision`、`evidence_gap`。
- 禁止输出 04 字段，避免模型为了填表先进入工单语言。

第二阶段：`field_mapping_only`

- 输入：第一阶段结果 + 原始 source facts。
- 输出：04 / Topic Card / 06 主字段。
- 强约束：不得发明第一阶段没有的角度；不得复制旧 deterministic fields；不得把实验句写到标题面。

### 可接受的轻量方案

### 方案 C：单次调用但硬分区 prompt/schema

如果不拆两次 Codex exec，也至少在一个 prompt 中用严格 JSON 分区：

```json
{
  "editorial_thinking": {
    "source_title_hook": "...",
    "case_anchor_used": "...",
    "chosen_austin_angle": "...",
    "public_title_direction": "...",
    "rejected_common_take": "..."
  },
  "field_mapping": {
    "选题命题": "...",
    "我要做的实验": "...",
    "可发布标题": "..."
  },
  "self_audit": {
    "title_surface": "...",
    "why_not_task_shell": "...",
    "hook_used_in_title": true
  }
}
```

验收时必须检查 `field_mapping` 是否与 `editorial_thinking.public_title_direction` 一致。

## PM 架构选择建议

建议 PM 不再派“标题返修”任务，而是派“AR-020C 结构根因修复”：

- 范围：runner prompt / Skill contract / replay provenance / validator scan surface / tests。
- 不做：生产发布、Feishu 写入、Topic Card UX、大范围采集逻辑、06 生成。
- 验收：fresh 2026-07-01+ full replay，必须有完整 runtime provenance、案例锚点证据、六类样例、最小反例专项测试。
- 成功标准：不是 0 个禁词命中，而是 PM/user 读样例时能看出 Austin 先完成了“为什么我会选/不选、从哪个真实现场切、标题为什么这样借原始入口”的判断。

推荐状态：`Needs Architecture Rework`。当前 r3 自验证和未提交补丁可以作为下一轮输入，但不能作为 QA 派发或 PM 接受依据。
