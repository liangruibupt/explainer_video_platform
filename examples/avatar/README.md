# 数字人样例

同一张 AI 生成人像（虚构人物）+ Amazon Polly Zhiyu 中文口播，渲染机 g7e.4xlarge（2026-11-04）。说明和指标见 [docs/AVATAR.md](../../docs/AVATAR.md)。

| 文件 | 内容 |
|---|---|
| `portrait_16x9.jpg`, `portrait_9x16.jpg` | 输入人像（图像模块生成，已缩小） |
| `infinitetalk_15s_480_fast.mp4` | InfiniteTalk，480P，lightx2v 4 步，14.3 秒（压缩到 crf 28） |
| `infinitetalk_15s_480_fast_frames.jpg` | 起 / 中 / 末三帧 |
| `longcat_15s_480_fast.mp4` | LongCat-Video-Avatar-1.5，480P，distill 8 步，同一输入 |
| `contact_15s_480_fast.jpg` | 两个模型同一时间点对比（上 InfiniteTalk，下 LongCat；0/4/7/11/14 秒） |
| `metrics.json` | 口型相关、身份余弦、色差（`infra/avatar/bench/analyze.py`） |
| `infinitetalk_promo45_480_fast.mp4`、`_sheet.jpg` | 集成验证 A：数字人页 API，45 秒超市促销口播（49.3 秒），640 宽 crf 31；六帧 0/20/40/60/80/99 % |
| `infinitetalk_live90_480_fast.mp4`、`_sheet.jpg` | 集成验证 B：90 秒直播带货（89.5 秒），长片段身份 / 偏色检查 |
| `traces/promo45.json`、`traces/live90.json` | 逐秒身份余弦和 ΔE（`infra/avatar/bench/trace.py`） |
| `wf_product_pitch_milk.mp4`、`_sheet.jpg` | 集成验证 C：模板「商品口播（数字人）」端到端，有机纯牛奶，42.4 秒 |
| `infinitetalk_15s_720_fast_sheet.jpg`、`traces/pitch15_720.json` | 集成验证 D：720P 快速 15 秒的六帧和逐秒指标 |
| `portrait_9x16_live.jpg` | 竖屏直播间主播人像（图像模块 9:16 生成，虚构人物，已缩小） |
| `infinitetalk_live30_916_480_fast.mp4`、`_sheet.jpg`、`traces/live30_916.json` | E：9:16 直播带货（面霜），25.8 秒，448×832 |
| `infinitetalk_live90_seg55_480_fast.mp4`、`_sheet.jpg`、`traces/live90_seg55.json` | F：90 秒直播带货自动分段（2 段，接缝 43.2 秒），和不分段的 `infinitetalk_live90_480_fast` 同稿同人像，640 宽 crf 31 |
| `infinitetalk_live90_seg55_seam.jpg` | F 的接缝：42.2 / 43.0 / 43.36（叠化中）/ 43.72 / 44.2 秒（`infra/avatar/bench/seams.py`） |
| `infinitetalk_pitch15_916_720_fast.mp4`、`_sheet.jpg`、`traces/pitch15_916_720.json` | G：9:16 720P 快速 15 秒（输出 704×1280，样例缩到 540 宽） |

原始文件（未压缩）：`s3://evp-models-747411437379-us-east-1/jobs/avatar-ec2/examples/avatar/`。

![两模型对比](contact_15s_480_fast.jpg)

![InfiniteTalk 起中末三帧](infinitetalk_15s_480_fast_frames.jpg)

![自动分段接缝](infinitetalk_live90_seg55_seam.jpg)

![9:16 直播带货](infinitetalk_live30_916_480_fast_sheet.jpg)
