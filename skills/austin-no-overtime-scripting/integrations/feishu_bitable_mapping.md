# 飞书字段映射

## 04 -> Topic Card

| Topic Card字段 | 优先读取的04字段 |
|---|---|
| topic_id | 选题ID / record_id / 内容指纹 |
| topic_title | 选题命题 / 我的选题标题 / 选题标题 / 可发布标题 |
| content_pillar | 对应方向 / 对应栏目 / 业务场景 |
| core_thesis | 一句话Brief / 重点体现 / 选题命题 / 我的切入 |
| target_audience | 目标观众 / 影响对象 / 业务场景 |
| pain_point | 我的工作流痛点 / 旧流程痛点 / 我的场景拆解 |
| old_workflow | 旧流程痛点 / 我的场景拆解 |
| ai_intervention | AI介入点 / 我要做的实验 / 验证方式 |
| demo_materials | 可展示证据 / 可展示结果 / 演示素材 |
| missing_evidence | 需要补的证据 / 证据缺口 |
| unique_judgment | 我的思考点 / 主编判断 / 选题判断 / 我的切入 |
| takeaway_asset | 可沉淀资产 / 资料包承接方式 / 重点体现 |
| fact_check_points | 不能声称的部分 / 不能照搬/风险提示 / 风险点 |

## 05 回写字段

`05 Brief与制作` 只保存轻量确认字段：脚本状态、推荐模板、核心观点、视频大纲、给06的生成输入、一句话说明、本地文档、是否可进入06、版本。

完整内容不塞进飞书表格，默认写入本地 `full_script_execution_package.md`。口播全文、视频结构、录屏清单、后期交接、发布包和 QA 都在这一个 Markdown 主文档里。

v0.5 不自动写 `06 内容任务主表`。只有用户确认执行包可制作后，后续流程才按需拆任务。
