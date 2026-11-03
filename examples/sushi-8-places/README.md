# 示例：苏轼一生停留的 8 个地方（横屏，北宋地图）

| | |
|---|---|
| 画幅 | 横屏 16:9，1920×1080，30 fps |
| 成片 | [output/final.mp4](output/final.mp4)，135.6 秒，10 段 |
| 提示词 | [prompt.json](prompt.json)：主题 + 分镜要求，8 个地方的事实写在 brief 里 |
| 地点 | [places.json](places.json)：宋代地名、今地名、经纬度、年份、成就 |
| 地图 | [song_map.png](song_map.png)：由 `song_map.py` 生成的北宋（约 1100 年）地图；[places_px.json](places_px.json) 是各地点在画面上的像素坐标 |
| 分镜 | [output/scenes.json](output/scenes.json) |
| 绘制代码 | [output/index.html](output/index.html)：地图作为背景，Opus 5.5 只画标记、路线和文字面板 |
| 字幕、运行记录 | [output/captions.ass](output/captions.ass)、[output/job.json](output/job.json)、[output/pipeline.log](output/pipeline.log) |

## 8 个地方

| | 宋代地名 | 今地名 | 年份 | 主要成就 |
|---|---|---|---|---|
| ① | 眉州 | 四川眉山 | 1037 出生 | 出生、读书；1057 年与苏辙同榜进士 |
| ② | 汴京 | 河南开封 | 1057 起多次在朝 | 入朝为官；1079 年乌台诗案下狱 |
| ③ | 杭州 | 浙江杭州 | 1071–1074 通判，1089–1091 知州 | 疏浚西湖、修筑苏堤；《饮湖上初晴后雨》 |
| ④ | 密州 | 山东诸城 | 1074–1076 | 《江城子·密州出猎》《水调歌头·明月几时有》 |
| ⑤ | 徐州 | 江苏徐州 | 1077–1079 | 率军民抗洪、修黄楼；上书论利国监冶铁业 |
| ⑥ | 黄州 | 湖北黄冈 | 1080–1084 贬谪 | 开垦东坡；前后《赤壁赋》《念奴娇·赤壁怀古》《寒食帖》 |
| ⑦ | 惠州 | 广东惠州 | 1094–1097 贬谪 | 助修东新桥、西新桥；“日啖荔枝三百颗” |
| ⑧ | 儋州 | 海南儋州 | 1097–1100 贬谪 | 办学讲学，东坡书院 |

结尾一段讲 1101 年遇赦北归、在常州病逝，并把 8 个地方连成路线。常州是终点，不计入 8 个地方。

年份和成就参考 [Wikipedia: Su Shi](https://en.wikipedia.org/wiki/Su_Shi) 等公开资料。为了防止模型编造，事实全部写进了 brief，模型只负责组织旁白和画面。

## 地图数据

| 内容 | 来源 | 许可 |
|---|---|---|
| 海岸线、河流、湖泊 | [Natural Earth](https://www.naturalearthdata.com/) 1:50m | 公有领域 |
| 北宋及周边政权疆域（约 1100 年） | [aourednik/historical-basemaps](https://github.com/aourednik/historical-basemaps) `world_1100.geojson` | GPL-3.0 |

historical-basemaps 的边界是近似值，所以地图上用虚线画疆域。`song_map.py` 只在运行时读取这些数据，仓库里不包含原始数据文件，只包含渲染出来的 `song_map.png`。

## 背景音乐

古琴曲《秋风词》独奏，来自 [Internet Archive: Qiu feng ci](https://archive.org/details/QiuFengCi)（2006 年上传者以公有领域发布）。原曲 108 秒，按视频长度循环，接缝处交叉淡化 4 秒，人声出现时自动压低。

## 各步骤耗时

| 写分镜 | 旁白 | 写绘制代码 | 字幕 | 逐帧渲染 | 合成 |
|---|---|---|---|---|---|
| 32 秒 | 3 秒 | 106 秒 | 179 秒 | 约 17 分钟 | 约 40 秒 |

逐帧渲染比竖屏示例慢很多：4067 帧 1080p，每帧约 0.28 秒，主要花在截图编码上。

## 重新生成

```bash
scripts/studio.sh start
~/.cache/explainer-video/venv/bin/python scripts/run_example.py examples/sushi-8-places
```

脚本会先用 `places.json` 生成地图，再把地图作为背景、把像素坐标作为上下文一起提交给工作室。
