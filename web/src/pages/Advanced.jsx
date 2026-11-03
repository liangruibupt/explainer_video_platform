import { useParams } from 'react-router-dom'

const INFO = {
  image: { title: '图像', model: 'Qwen-Image 2.1（FP8）', gpu: 'ml.g7.2xlarge',
    does: '文生图、图像编辑、透明背景 PNG，最多 10 张参考图。' },
  video: { title: 'AI 视频', model: 'MiniMax H3（GGUF Q8）', gpu: 'ml.g7.4xlarge',
    does: '文/图生视频，画面和同步音频一次生成。' },
  music: { title: '音乐', model: 'YuE2-3B（BF16）', gpu: 'ml.g7.2xlarge',
    does: '歌词 + 风格生成完整歌曲，48 kHz 立体声，附乐谱。' },
  workflow: { title: '工作流', model: 'Opus 5.5（生成工作流）', gpu: '—',
    does: '用节点图编排生成步骤，Agent 能从一句话生成工作流。' },
}

export default function Advanced() {
  const { kind } = useParams()
  const i = INFO[kind] || INFO.image
  return (
    <div className="page">
      <h1 className="h1-sm">{i.title} <span className="pill soon">进阶 · 规划中</span></h1>
      <p className="sub">{i.does}</p>
      <div className="panel narrow">
        <dl className="kv">
          <dt>模型</dt><dd>{i.model}</dd>
          <dt>GPU</dt><dd>{i.gpu}</dd>
          <dt>部署</dt><dd>SageMaker 异步端点，闲置缩容到 0，有任务时自动启动</dd>
          <dt>状态</dt><dd>未部署。设计见仓库 docs/DESIGN.md 第 3–6 节</dd>
        </dl>
      </div>
    </div>
  )
}
