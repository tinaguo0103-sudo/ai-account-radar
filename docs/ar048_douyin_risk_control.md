# AR-048 抖音风控暂停与人工恢复合同

## 用户结果

抖音出现滑块、验证码、短信验证、challenge、登出或无法判断登录态时，来源立即暂停。
系统保留本轮已完成账号，不访问后续账号，也不把尚未尝试的账号记为失败。用户只在既有
fixed 9333 profile 手工完成验证；系统 fresh preflight 通过后，仅按原顺序续跑 exact
remaining accounts。

系统不自动识别、填写或绕过验证码，不导出 Cookie/token/profile，不切换浏览器身份。

## 前置门禁

任何账号导航前，必须核对：

- CDP 端口为 9333，profile identity 为 `fixed_douyin_profile_9333`；
- `login_state=logged_in` 且 `status=session_verified`；
- 页面 URL、标题、iframe、dialog 和可见文本没有 slider、验证码、短信或 challenge；
- readiness 结果不是 blank、indeterminate 或 unknown。

门禁失败时账号导航数必须为 0，并写入 typed durable risk state。

## 固定节奏

- 每批 5 个账号；
- 账号之间固定等待 10 秒；
- 批次之间固定等待 120 秒；
- 第一轮全部结束且没有风控信号后，仅对 transient timeout 进行一次 tail retry；
- tail retry 前固定等待 600 秒。

这些值写入 run evidence。系统不使用随机延时、指纹伪装或隐蔽 pacing。

## Exact Checkpoint

`output/state/source_control.sqlite3` 是唯一 authority。`douyin_risk_state` 保存
source-global 状态；`douyin_run_checkpoints` 以 `run_id + source_id` 保存：

```text
completed
updated_no_new_items
failed_account_local
pending
not_attempted_waiting_manual_verification
```

`completed` 和 `updated_no_new_items` 的 artifact identity/hash 不可改写。同一 exact
run 恢复时跳过它们，只处理 `pending` 与
`not_attempted_waiting_manual_verification`，顺序仍以原始 source plan 为准。

## 运行中风控

每次导航准备前后及 tail retry 前重复执行 source-global detector。任一账号出现
slider/verification iframe/dialog/text、SMS/challenge URL/title、登出菜单消失，或多个账号
连续出现相同 XHR schema/body failure，立即停止后续导航。未尝试账号统一标记
`not_attempted_waiting_manual_verification`，不得记为账号失败。

本地通知只是把同一暂停事实送到用户可见入口。通知失败时来源仍保持暂停，不得继续采集。

## 人工恢复

`/sources` 显示 typed 风控状态、完成数与剩余数，并提供两个动作：

1. 打开既有 fixed 9333 profile 到前台；
2. 用户点击“我已完成验证”后执行 fresh exact preflight。

只有 fresh preflight 为 `session_verified/logged_in` 且 profile identity exact 时，才启动
remaining-only resume。重复确认或重复恢复不得改写已完成 artifact，也不得增加 checkpoint。

## 边界

下游只消费当前 run 的 exact artifact。等待、失败和未尝试账号贡献 0 行，不读取旧 run、
cache 或其他来源替代。首次真实生产证明由发布后的下一次正常 08:00 完成，Dev/QA 不执行
正式采集或验证码交互。
