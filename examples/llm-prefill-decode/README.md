# 示例：LLM 推理的 Prefill 与 Decode（竖屏）

| | |
|---|---|
| 画幅 | 竖屏 9:16，1080×1920，30 fps |
| 成片 | [output/final.mp4](output/final.mp4)，100.5 秒，7 段 |
| 提示词 | [prompt.json](prompt.json)：主题 + 分镜要求（brief） |
| 分镜 | [output/scenes.json](output/scenes.json)：每段的标题、旁白、画面描述、音效 |
| 时长 | [output/durations.json](output/durations.json)：每段帧数，由 Polly 旁白的实际长度决定 |
| 绘制代码 | [output/index.html](output/index.html)：Opus 5.5 为每段写的 Canvas 2D `draw()` 函数 |
| 字幕 | [output/captions.ass](output/captions.ass)：Whisper 字级时间，逐字高亮 |
| 运行记录 | [output/job.json](output/job.json)、[output/pipeline.log](output/pipeline.log) |

## 7 段内容

1. 标题：LLM 推理的两个阶段
2. 一次请求的全过程：Prefill 一次处理全部输入、生成 KV Cache；Decode 每步生成 1 个 token
3. Prefill：计算密集，GPU 算力打满，决定首 token 延迟 TTFT
4. Decode：访存密集，每步都要读全部权重和 KV Cache，决定 token 间隔 TPOT
5. 挤在同一组 GPU 上：长 Prefill 阻塞 Decode，两阶段需求相反
6. PD 分离：Prefill 集群算完把 KV Cache 传给 Decode 集群，代价是要高速传输 KV Cache
7. 小结

## 各步骤耗时

| 写分镜 | 旁白 | 写绘制代码 | 字幕 | 逐帧渲染 | 合成 |
|---|---|---|---|---|---|
| 24 秒 | 3 秒 | 82 秒 | 34 秒 | 215 秒 | 29 秒 |

## 重新生成

```bash
scripts/studio.sh start
~/.cache/explainer-video/venv/bin/python scripts/run_example.py examples/llm-prefill-decode
```

每次生成的分镜和画面都会不同，因为都是 Opus 5.5 现场写的。
