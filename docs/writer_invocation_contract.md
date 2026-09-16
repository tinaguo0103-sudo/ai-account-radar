# Writer invocation contract

本地候选，不代表已发布或已通过内容验收。入口为 scripts/run_daily_workflow.py，
写作 owner 为同一 Automation Codex；代码只负责材料、顺序、checkpoint 和结果搬运。
不再调用 clean_writer_context 实验 adapter，不启动第二模型 runtime。

## Phase inputs and outputs

| 公共 action | owner 实际读取 | 提交 envelope | 参数 |
| --- | --- | --- | --- |
| article_required | owner_invocation.skill_entrypoint 指向的 active R4；按 Skill 现有顺序完整读取当前 references、按需案例/原始口播，以及当前题 same-run 原始材料 | packet_id + article {topic_id,title,body} | --article-item-file |
| spoken_adaptation_required | topic_input.article_artifact.path 的完整冻结文章与 spoken-adaptation.md | packet_id + article_sha256 + script {topic_id,title,hook,structure,body} | --script-item-file |

材料不足使用同一 packet 声明的 failure envelope，不补齐或替换历史内容。
scripts_required 仅为内部/兼容阶段标识；再次调用公共入口取得实际 phase packet，
不可据此提交裸 topic_id/title/hook/structure/body。Skill 最终字段不变，由上述 envelope 包装。

外层不另写创作方法。owner 必须打开正文，而不是把 hash、文件清单或历史读过当作
本次 Skill 应用；不要求输出内部推理。文件读取和一手研究工具不被调用合同禁用。
研究能力是否在实际 scheduler/Luna 环境可用，仍需该环境正向验证。

## Recovery and evidence boundary

文章冻结后丢失响应，可用原 envelope 重提；内容冲突仍拒绝，冻结文章不重写。
无参数续接得到当前 phase；口播不得先于文章。已经进入下一题不自动重放旧题。
公共入口、同一 SQLite checkpoint 和 artifact 是恢复权威，不使用历史 latest。
本地 deterministic fixtures 只证明组件调用/阶段与恢复，不是 259 候选完整重放，
也不证明模型确实阅读全文、自检、写作质量或定时稳定性。

## Release boundary

候选绑定 ai-git，保留 PAUSED、每日 08:00、原 cwd、gpt-5.6-luna/max。
config/web010_single_daily_workflow_release.json 只保存待审合同；不得直接更新实际任务。
后续需 PM 授权同一 release unit 的代码、已认可 R4 parity 和 automation prompt 对齐，
再独立验证 scheduler 工具、完整文章/口播、真实 runtime 与 Website 读回。
本阶段不生成新文章、不同步 active Skill、不 push、不改生产或实际 automation。
