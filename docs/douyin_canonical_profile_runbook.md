# 抖音固定 Chrome Profile 运维手册

生产抖音采集只允许使用端口 `9333` 和以下持久化 profile：

```text
~/.codex/ai-account-radar-runtime/browser_profiles/douyin-chrome-profile
```

`DOUYIN_CHROME_PROFILE_DIR` 仅用于显式环境覆盖。默认路径与仓库、分支和 worktree 无关。launcher 会原子记录 canonical profile/hash、`9333`、真实 `lsof` listener PID 和 browser WebSocket identity；复用时必须全部匹配。marker 缺失、被改写、PID 复用或 Chrome 重启都会失败关闭，不切换到其他 Chrome/profile/端口。`ps` 只用于人工诊断，不是自动化门禁依赖。

## 07:45 只读检查

```bash
python3 scripts/check_douyin_session.py --port 9333
```

命令只输出 JSON，不读取或导出 cookie、token 或 localStorage。只有 `ok=true`、`login_state=logged_in` 才可进入 08:00 抖音探针。

## 首次迁移或重新登录

该步骤只能由生产执行线程操作，开发线程不得复制真实 profile。

1. 暂停 `ai` / `ai-04` / `ai-2`，确认没有采集任务运行。
2. 用 `lsof -nP -iTCP:9333 -sTCP:LISTEN` 和 `ps -p <pid> -o command=` 记录错误 dedicated Chrome 的 PID、端口和实际 profile。
3. 正常退出该 dedicated Chrome，确认 `9333` 已无监听。不要由启动脚本自动 kill。
4. 若迁移旧 profile：仅在 Chrome 完全退出且 lock 文件不活动时，先完整备份到带时间戳的只读目录，再复制到 canonical 目录。复制前后记录目录清单和校验；失败时删除不完整的新目录并从备份恢复。禁止复制活动/锁定中的 profile。
5. 更安全的默认方案是直接以 canonical profile 启动前台 Chrome，再人工登录抖音：

   ```bash
   python3 scripts/start_douyin_cdp_chrome.py --port 9333 --foreground
   ```

6. 登录后运行 07:45 只读检查并保存 JSON read-back。不得保存二维码、cookie、token 或 localStorage。
7. 检查失败时保持自动化暂停；不要改端口、换 profile 或改用 headless 绕过。

回滚只针对 profile 目录：先退出 canonical Chrome，保留失败目录证据，再恢复此前完整备份。代码回滚按 Git 发布流程执行，二者不得混为一项操作。
