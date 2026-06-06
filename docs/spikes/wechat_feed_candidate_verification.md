# 微信公众号 Feed 候选验证

验证对象：`数字生命卡兹克` 公众号自动发现文章列表的候选 feed / 订阅服务。

## 一页结论
- 找到 `数字生命卡兹克` 可直接读取的 feed：`https://wechat2rss.xlab.app/feed/7b1c10c25bdfe69d0a08a5349cf3b032e55f4f05.xml`。
- 本轮检测到的 feed 若来自工具项目 releases，只能说明工具项目可被订阅，不代表卡兹克公众号已可自动发现。
- 当前已把卡兹克 Wechat2RSS feed 做成 P1 显式接入能力：默认 `daily_pipeline.py` 不拉取，只有传 `--fetch-wechat-feed` 时才进入候选池。
- 当前最稳回退路径仍是 `02 URL投喂入口` 单篇 URL；如果 feed 失效或全文解析受限，继续用单篇 URL 投喂。
- 本轮不建议把 RSS之家页面或 GitHub issue 直接接入默认 `daily_pipeline.py`。

## P1 显式接入命令

```bash
python3 scripts/wechat_feed_intake.py --config config/wechat_feed_candidates.yaml --limit 5 --dry-run
python3 scripts/daily_pipeline.py --fetch-wechat-feed --wechat-feed-limit 5
python3 scripts/daily_pipeline.py --fetch-wechat-feed --wechat-feed-limit 5 --write-feishu
```

feed 内容会先进入标准 ContentItem，本地输出 `output/wechat_feed_content_items.jsonl`；显式写入时会进入 `03 内容收件箱`，再参与 `04 分析与选题` 候选。默认流程仍不拉该 feed。

## 能力矩阵

| 候选 | 类型 | HTTP | Content-Type | feed_detected | feed_url | item_count | recommendation |
| --- | --- | --- | --- | --- | --- | ---: | --- |
| RSS之家数字生命卡兹克页面 | rssabc_page | - | - | 否 | - | 0 | needs_user_dependency：未检测到公开 feed；可能需要 RSS之家账号/商业订阅或用户提供实际 feed_url。 |
| Wechat2RSS issue 166 | github_issue | 200 | text/html; charset=utf-8 | 是 | https://wechat2rss.xlab.app/feed/7b1c10c25bdfe69d0a08a5349cf3b032e55f4f05.xml | 10 | auto_ready：检测到可读 feed，可进入后续 source_watch_probe。 |
| Wechat2RSS issue 310 | github_issue | 200 | text/html; charset=utf-8 | 否 | - | 0 | blocked_not_recommended：issue 只能作为线索页，未发现可直接接入 feed。 |
| Wechat2RSS issue 390 | github_issue | 200 | text/html; charset=utf-8 | 否 | - | 0 | blocked_not_recommended：issue 只能作为线索页，未发现可直接接入 feed。 |
| Wechat2RSS issue 419 | github_issue | 200 | text/html; charset=utf-8 | 否 | - | 0 | blocked_not_recommended：issue 只能作为线索页，未发现可直接接入 feed。 |
| we-mp-rss GitHub | tool_route | 200 | text/html; charset=utf-8 | 否 | - | 0 | needs_user_dependency：适合作为自建中间层候选，需要部署服务并配置公众号订阅。 |
| we-mp-rss README | tool_route | 200 | text/html; charset=utf-8 | 否 | - | 0 | needs_user_dependency：适合作为自建中间层候选，需要部署服务并配置公众号订阅。 |
| wewe-rss GitHub | tool_route | - | - | 否 | - | 0 | needs_user_dependency：适合作为私有化候选，但通常需要微信读书登录态/服务维护。 |
| wewe-rss releases | tool_route | 200 | text/html; charset=utf-8 | 是 | https://github.com/cooderl/wewe-rss/releases.atom | 10 | needs_user_dependency：检测到的是工具项目自身 feed，不是目标公众号文章 feed；可用于跟踪工具更新，但不能直接作为卡兹克来源。 |
| RSSHub GitHub | tool_route | 200 | text/html; charset=utf-8 | 否 | - | 0 | unstable_spike_only：可作补充路由，公众号指定源通常依赖第三方路线或 cookie，不建议默认接入。 |

## RSS之家页面验证
- URL：https://www.rssabc.com/obj/PDxlbw893y
- HTTP： / ``
- 是否检测到公开 feed：否
- 失败原因：[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'www.rssabc.com'. (_ssl.c:1129); fallback failed: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'rssabc.com'. (_ssl.c:1129)
- 判断：needs_user_dependency：未检测到公开 feed；可能需要 RSS之家账号/商业订阅或用户提供实际 feed_url。

## Wechat2RSS issue 线索验证
### Wechat2RSS issue 166
- URL：https://github.com/ttttmr/Wechat2RSS/issues/166
- HTTP：200 / `text/html; charset=utf-8`
- 是否检测到可读 feed：是
- 判断：auto_ready：检测到可读 feed，可进入后续 source_watch_probe。
- 线索：
  - `https://mp.weixin.qq.com/s/d7pzpal6u1c8NfmMLia2mw`
  - `https://mp.weixin.qq.com/s/d7pzpal6u1c8NfmMLia2mw\`
  - `https://mp.weixin.qq.com/s/d7pzpal6u1c8NfmMLia2mw\u003c/a\u003e\u003c/p\u003e`
  - `https://github.com/_view_fragments/issues/show/ttttmr/Wechat2RSS/166/issue_layout`
  - `https://opengraph.githubassets.com/162737593ca4928712a28200da98c8384caff705025e207671ad82785f70e75f/ttttmr/Wechat2RSS/issues/166`
  - `https://github.com/ttttmr/Wechat2RSS/issues/166`
  - `https://github.com/166/Wechat2RSS/issues/166`
  - `https://github.com/ttttmr/Wechat2RSS.git`
  - `https://github.com/ttttmr/Wechat2RSS/issues/166&quot;,&quot;user_id&quot;:null}}`
  - `https://github.com/ttttmr/Wechat2RSS/labels/%E6%96%B0%E5%85%AC%E4%BC%97%E5%8F%B7`
  - `https://github.com/ttttmr/Wechat2RSS/issues/166#issuecomment-2745021692`
  - `https://wechat2rss.xlab.app/feed/7b1c10c25bdfe69d0a08a5349cf3b032e55f4f05.xml`
### Wechat2RSS issue 310
- URL：https://github.com/ttttmr/Wechat2RSS/issues/310
- HTTP：200 / `text/html; charset=utf-8`
- 是否检测到可读 feed：否
- 判断：blocked_not_recommended：issue 只能作为线索页，未发现可直接接入 feed。
- 线索：
  - `https://mp.weixin.qq.com/s/oAVTfP6OnUc2I11-px48lw`
  - `https://mp.weixin.qq.com/s/oAVTfP6OnUc2I11-px48lw\`
  - `https://mp.weixin.qq.com/s/oAVTfP6OnUc2I11-px48lw\u003c/a\u003e\u003c/p\u003e`
  - `https://github.com/_view_fragments/issues/show/ttttmr/Wechat2RSS/310/issue_layout`
  - `https://opengraph.githubassets.com/cb91d225323a0deb7790b1182455b0b9c7ef9759671e93632da9022404e7dc48/ttttmr/Wechat2RSS/issues/310`
  - `https://github.com/ttttmr/Wechat2RSS/issues/310`
  - `https://github.com/310/Wechat2RSS/issues/310`
  - `https://github.com/ttttmr/Wechat2RSS.git`
  - `https://github.com/ttttmr/Wechat2RSS/issues/310&quot;,&quot;user_id&quot;:null}}`
  - `https://github.com/ttttmr/Wechat2RSS/issues/166`
  - `https://github.com/ttttmr/Wechat2RSS/labels/%E6%96%B0%E5%85%AC%E4%BC%97%E5%8F%B7`
  - `https://github.com/ttttmr/Wechat2RSS/issues?q=state%3Aopen%20label%3A%22%E6%96%B0%E5%85%AC%E4%BC%97%E5%8F%B7%22`
### Wechat2RSS issue 390
- URL：https://github.com/ttttmr/Wechat2RSS/issues/390
- HTTP：200 / `text/html; charset=utf-8`
- 是否检测到可读 feed：否
- 判断：blocked_not_recommended：issue 只能作为线索页，未发现可直接接入 feed。
- 线索：
  - `https://mp.weixin.qq.com/s/Vj6BvmaM5XZzgRlBCKDwhw`
  - `https://mp.weixin.qq.com/s/Vj6BvmaM5XZzgRlBCKDwhw\`
  - `https://mp.weixin.qq.com/s/Vj6BvmaM5XZzgRlBCKDwhw\u003c/a\u003e\u003c/p\u003e`
  - `https://github.com/_view_fragments/issues/show/ttttmr/Wechat2RSS/390/issue_layout`
  - `https://opengraph.githubassets.com/4ba50987ad7c17fbdae80f816b8d8b96c10b12abd225fead05b36cd98c226f5f/ttttmr/Wechat2RSS/issues/390`
  - `https://github.com/ttttmr/Wechat2RSS/issues/390`
  - `https://github.com/390/Wechat2RSS/issues/390`
  - `https://github.com/ttttmr/Wechat2RSS.git`
  - `https://github.com/ttttmr/Wechat2RSS/issues/390&quot;,&quot;user_id&quot;:null}}`
  - `https://github.com/ttttmr/Wechat2RSS/issues/166`
  - `https://github.com/ttttmr/Wechat2RSS/labels/%E6%96%B0%E5%85%AC%E4%BC%97%E5%8F%B7`
  - `https://github.com/ttttmr/Wechat2RSS/issues?q=state%3Aopen%20label%3A%22%E6%96%B0%E5%85%AC%E4%BC%97%E5%8F%B7%22`
### Wechat2RSS issue 419
- URL：https://github.com/ttttmr/Wechat2RSS/issues/419
- HTTP：200 / `text/html; charset=utf-8`
- 是否检测到可读 feed：否
- 判断：blocked_not_recommended：issue 只能作为线索页，未发现可直接接入 feed。
- 线索：
  - `https://github.com/_view_fragments/issues/show/ttttmr/Wechat2RSS/419/issue_layout`
  - `https://opengraph.githubassets.com/3905905bb30eabf1c196e99caee50d0b704a3ca1077d954b75766978ec6e6dbb/ttttmr/Wechat2RSS/issues/419`
  - `https://github.com/ttttmr/Wechat2RSS/issues/419`
  - `https://github.com/419/Wechat2RSS/issues/419`
  - `https://github.com/ttttmr/Wechat2RSS.git`
  - `https://github.com/ttttmr/Wechat2RSS/issues/419&quot;,&quot;user_id&quot;:null}}`
  - `https://github.com/ttttmr/Wechat2RSS/issues/419#issuecomment-4468566147`
  - `https://github.com/ttttmr/Wechat2RSS/issues/419#issuecomment-4590590363`
  - `https://github.com/ttttmr/Wechat2RSS/issues/419#issue-4459328354`
  - `数字生命卡兹克`
  - `卡兹克`
  - `GitHub API status=200 content_type=application/json; charset=utf-8`

## 工具路线判断
### we-mp-rss
- we-mp-rss GitHub：needs_user_dependency：适合作为自建中间层候选，需要部署服务并配置公众号订阅。
  - 页面线索：https://github.com/rachelos/we-mp-rss; https://opengraph.githubassets.com/c538faad9a8f11efedc980d74dc4a189525429d58c1c8a3b85ed67f2482e67c1/rachelos/we-mp-rss; https://github.com/rachelos/we-mp-rss.git; https://github.com/rachelos/we-mp-rss&quot;,&quot;user_id&quot;:null}}; https://github.com/RSSNext/Folo/stargazers\
- we-mp-rss README：needs_user_dependency：适合作为自建中间层候选，需要部署服务并配置公众号订阅。
  - 页面线索：https://github.com/rachelos/we-mp-rss/blob/main/ReadMe.md; https://opengraph.githubassets.com/c538faad9a8f11efedc980d74dc4a189525429d58c1c8a3b85ed67f2482e67c1/rachelos/we-mp-rss; https://github.com/rachelos/we-mp-rss.git; https://github.com/rachelos/we-mp-rss/blob/main/ReadMe.md&quot;,&quot;user_id&quot;:null}}; https://github.com/RSSNext/Folo/stargazers\
### wewe-rss
- wewe-rss GitHub：needs_user_dependency：适合作为私有化候选，但通常需要微信读书登录态/服务维护。
- wewe-rss releases：needs_user_dependency：检测到的是工具项目自身 feed，不是目标公众号文章 feed；可用于跟踪工具更新，但不能直接作为卡兹克来源。
  - 页面线索：https://github.com/cooderl/wewe-rss/releases; https://opengraph.githubassets.com/7e242d924678620a95fad80bb3c30a1f774764b67719bb17371c02f9d283a1df/cooderl/wewe-rss; https://github.com/cooderl/wewe-rss/releases.atom; https://github.com/cooderl/wewe-rss/tags.atom; https://github.com/cooderl/wewe-rss.git
### RSSHub
- RSSHub GitHub：unstable_spike_only：可作补充路由，公众号指定源通常依赖第三方路线或 cookie，不建议默认接入。
  - 页面线索：https://github.com/DIYgod/RSSHub; https://opengraph.githubassets.com/1582fb28f73b2c86947f466ac298a4bdd3c3fb75c9a218edc3eb3951f1e2b500/DIYgod/RSSHub; https://github.com/DIYgod/RSSHub.git; https://github.com/DIYgod/RSSHub&quot;,&quot;user_id&quot;:null}}; https://docs.rsshub.app/img/logo.png\

## 下一步最小接入方案

1. 继续保留单篇公众号 URL 作为 P0：`02 URL投喂入口 -> url_content_resolver.py -> 03 内容收件箱`。
2. 卡兹克 Wechat2RSS feed 已进入 P1 显式接入验证；默认流程不拉取。
3. 显式 feed 输出只在用户传 `--fetch-wechat-feed` 时参与候选；连续稳定后再考虑是否默认接入。
4. 正式默认化前必须持续验证：feed_url、标题、文章链接、发布时间、去重指纹、失败原因、全文解析可用性。

## 原始结果摘要

### RSS之家数字生命卡兹克页面
- candidate_url：https://www.rssabc.com/obj/PDxlbw893y
- candidate_type：rssabc_page
- http_status：-
- content_type：`-`
- feed_detected：否
- feed_url：-
- item_count：0
- failure_reason：[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'www.rssabc.com'. (_ssl.c:1129); fallback failed: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'rssabc.com'. (_ssl.c:1129)
- recommendation：needs_user_dependency：未检测到公开 feed；可能需要 RSS之家账号/商业订阅或用户提供实际 feed_url。

### Wechat2RSS issue 166
- candidate_url：https://github.com/ttttmr/Wechat2RSS/issues/166
- candidate_type：github_issue
- http_status：200
- content_type：`text/html; charset=utf-8`
- feed_detected：是
- feed_url：https://wechat2rss.xlab.app/feed/7b1c10c25bdfe69d0a08a5349cf3b032e55f4f05.xml
- item_count：10
- latest_titles：
  - Anthropic万字长文：当AI开始构建自己，人类该何去何从？ | Fri, 05 Jun 2026 09:35:00 +0800 | https://mp.weixin.qq.com/s?__biz=MzIyMzA5NjEyMA==&mid=2647682995&idx=1&sn=dfb17dcc572d5d2f4e980859973f3992
  - 分享Claude Code团队内部的5条工作原则，我觉得每一条都值得学习。 | Wed, 03 Jun 2026 10:02:00 +0800 | https://mp.weixin.qq.com/s?__biz=MzIyMzA5NjEyMA==&mid=2647682883&idx=1&sn=6c9cbd20d8f6e7dbabe4a36b53bad5fe
  - 为了不花那120刀，我把电脑清理软件做成了开源skill。 | Tue, 02 Jun 2026 09:58:00 +0800 | https://mp.weixin.qq.com/s?__biz=MzIyMzA5NjEyMA==&mid=2647682858&idx=1&sn=0bdfd10a71c2746e17dab2157aab6369
  - 英伟达发布全新RTX Spark - 个人PC的新时代。 | Mon, 01 Jun 2026 15:04:00 +0800 | https://mp.weixin.qq.com/s?__biz=MzIyMzA5NjEyMA==&mid=2647682855&idx=1&sn=b7d0d623f3c26f5debdfba1c089b0e06
  - 实测Claude Opus 4.8，这可能是第一个不会偷懒的模型。 | Fri, 29 May 2026 06:06:00 +0800 | https://mp.weixin.qq.com/s?__biz=MzIyMzA5NjEyMA==&mid=2647682817&idx=1&sn=d1f463490433d00ab331659a19b7cc1f
- recommendation：auto_ready：检测到可读 feed，可进入后续 source_watch_probe。

### Wechat2RSS issue 310
- candidate_url：https://github.com/ttttmr/Wechat2RSS/issues/310
- candidate_type：github_issue
- http_status：200
- content_type：`text/html; charset=utf-8`
- feed_detected：否
- feed_url：-
- item_count：0
- recommendation：blocked_not_recommended：issue 只能作为线索页，未发现可直接接入 feed。

### Wechat2RSS issue 390
- candidate_url：https://github.com/ttttmr/Wechat2RSS/issues/390
- candidate_type：github_issue
- http_status：200
- content_type：`text/html; charset=utf-8`
- feed_detected：否
- feed_url：-
- item_count：0
- recommendation：blocked_not_recommended：issue 只能作为线索页，未发现可直接接入 feed。

### Wechat2RSS issue 419
- candidate_url：https://github.com/ttttmr/Wechat2RSS/issues/419
- candidate_type：github_issue
- http_status：200
- content_type：`text/html; charset=utf-8`
- feed_detected：否
- feed_url：-
- item_count：0
- recommendation：blocked_not_recommended：issue 只能作为线索页，未发现可直接接入 feed。

### we-mp-rss GitHub
- candidate_url：https://github.com/rachelos/we-mp-rss
- candidate_type：tool_route
- http_status：200
- content_type：`text/html; charset=utf-8`
- feed_detected：否
- feed_url：-
- item_count：0
- recommendation：needs_user_dependency：适合作为自建中间层候选，需要部署服务并配置公众号订阅。

### we-mp-rss README
- candidate_url：https://github.com/rachelos/we-mp-rss/blob/main/ReadMe.md
- candidate_type：tool_route
- http_status：200
- content_type：`text/html; charset=utf-8`
- feed_detected：否
- feed_url：-
- item_count：0
- recommendation：needs_user_dependency：适合作为自建中间层候选，需要部署服务并配置公众号订阅。

### wewe-rss GitHub
- candidate_url：https://github.com/cooderl/wewe-rss
- candidate_type：tool_route
- http_status：-
- content_type：`-`
- feed_detected：否
- feed_url：-
- item_count：0
- failure_reason：_ssl.c:1112: The handshake operation timed out
- recommendation：needs_user_dependency：适合作为私有化候选，但通常需要微信读书登录态/服务维护。

### wewe-rss releases
- candidate_url：https://github.com/cooderl/wewe-rss/releases
- candidate_type：tool_route
- http_status：200
- content_type：`text/html; charset=utf-8`
- feed_detected：是
- feed_url：https://github.com/cooderl/wewe-rss/releases.atom
- item_count：10
- latest_titles：
  - v2.6.1 | 2024-12-15T12:54:01Z | https://github.com/cooderl/wewe-rss/releases/tag/v2.6.1
  - v2.6.0 | 2024-12-15T08:38:37Z | https://github.com/cooderl/wewe-rss/releases/tag/v2.6.0
  - v2.6.1 | 2024-12-15T13:02:32Z | https://github.com/cooderl/wewe-rss/releases/tag/release-20241215130232
  - v2.6.0 | 2024-12-15T09:14:54Z | https://github.com/cooderl/wewe-rss/releases/tag/release-20241215084708
  - v2.5.0 | 2024-12-05T14:28:21Z | https://github.com/cooderl/wewe-rss/releases/tag/v2.5.0
- recommendation：needs_user_dependency：检测到的是工具项目自身 feed，不是目标公众号文章 feed；可用于跟踪工具更新，但不能直接作为卡兹克来源。

### RSSHub GitHub
- candidate_url：https://github.com/DIYgod/RSSHub
- candidate_type：tool_route
- http_status：200
- content_type：`text/html; charset=utf-8`
- feed_detected：否
- feed_url：-
- item_count：0
- recommendation：unstable_spike_only：可作补充路由，公众号指定源通常依赖第三方路线或 cookie，不建议默认接入。
