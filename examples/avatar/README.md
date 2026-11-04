# 数字人样例

同一张 AI 生成人像（虚构人物）+ Amazon Polly Zhiyu 中文口播，渲染机 g7e.4xlarge（2026-11-04）。说明和指标见 [docs/AVATAR.md](../../docs/AVATAR.md)。

| 文件 | 内容 |
|---|---|
| `portrait_16x9.jpg`, `portrait_9x16.jpg` | 输入人像（图像模块生成，已缩小） |
| `infinitetalk_15s_480_fast.mp4` | InfiniteTalk，480P，lightx2v 4 步，14.3 秒（压缩到 crf 28） |
| `infinitetalk_15s_480_fast_frames.jpg` | 起 / 中 / 末三帧 |

原始文件（未压缩）：`s3://evp-models-747411437379-us-east-1/jobs/avatar-ec2/examples/avatar/`。

![InfiniteTalk 起中末三帧](infinitetalk_15s_480_fast_frames.jpg)
