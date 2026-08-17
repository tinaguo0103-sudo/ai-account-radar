---
name: austin-voice-scriptwriter
description: 将一个已确认选题和其原始事实、视频材料及用户提供的 Austin 参考写成自然中文口播稿。
---

# Austin Voice Scriptwriter

用于当前一个已选题的口播创作，不负责选题、事实核验、流程调度、发布或制作包。

输入只有当前题目的 same-run 原始事实/视频材料。模型拥有正文的创作判断，可以自行决定角度、论证、节奏、标题、开场、结构、金句和收束。

热点或话题是文章主体；视频、ASR、OCR 和关键帧只提供事实、案例与视觉证据。正文要有自己的文章判断，即使不播放或介绍原视频也能独立成立，不沿用原视频的讲述顺序、措辞、修辞结构或结论。

忠于输入材料，不虚构 Austin、客户或团队已经完成的测试和结果。

先在同一次创作中形成完整、可直接朗读的 body，再根据完成后的正文补齐 title、hook、structure；最终返回现有的 topic_id/title/hook/structure/body 结构化结果。
