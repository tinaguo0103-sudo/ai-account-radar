# AR-044 09:15 Prompt Release Candidate

The live automation is not edited in Development. Production must update the
existing `ai-04-rebuild` Prompt in place and preserve its ID, fixed task,
schedule, model, reasoning, cwd, exact-run checks, Feishu target checks and
Topic Card/06 prohibition.

Replace only the editorial gate semantics with:

```text
所有可信的同日 exact-run 候选直接进入 Stage 1。原始素材出现数字、日期、法律、医疗、金融或安全词不构成研究前置资格门。Stage 1 只按业务价值选择、观察或不做。

只有最终选中的可见标题或角度实际保留精确数字、直接引语、官方声明、法律、医疗、金融或安全 hard claim 时，才要求对应研究证据。证据不足时先删除或软化 hard claim；仍不成立时只淘汰该候选，其他 survivor 继续。

不得设置最低推荐数、Top-N、来源 quota、保底候选或统一观察覆盖。推荐数为 0 时以 completed_no_recommendation 正常结束，Feishu 04、Topic Card 和 06 调用均为 0。只有 0 safe survivor 才停止主编链路。
```

Official release read-back must prove the exact Prompt text/hash and unchanged
automation identity/schedule/status. No catch-up or same-day recovery is part of
the Prompt update.
