import { NavLink, Outlet } from 'react-router-dom'

const ico = {
  home: 'M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z',
  works: 'M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0zM7 6H4a3 3 0 0 0 3 4M17 6h3a3 3 0 0 1-3 4',
  tasks: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
  image: 'M4 5h16v14H4zM4 15l4-4 4 4 3-3 5 5',
  video: 'M3 6h13v12H3zM16 10l5-3v10l-5-3',
  music: 'M4 14a8 8 0 0 1 16 0v4a2 2 0 0 1-2 2h-1v-6h3M4 14v4a2 2 0 0 0 2 2h1v-6H4',
  flow: 'M10 3h4v4h-4zM3 17h4v4H3zM17 17h4v4h-4zM12 7v5M5 17v-3h14v3',
}
const I = ({ d }) => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
       strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
)

function Item({ to, icon, label, soon }) {
  return (
    <NavLink to={to} end className={({ isActive }) => `nav ${isActive ? 'on' : ''}`}>
      <I d={ico[icon]} /><span>{label}</span>{soon && <em>进阶</em>}
    </NavLink>
  )
}

export default function Layout() {
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <div className="logo">讲</div>
          <div><b>讲解视频工作室</b><small>Explainer Studio</small></div>
        </div>
        <div className="group">创作</div>
        <Item to="/" icon="home" label="工作台" />
        <div className="group">资产与管理</div>
        <Item to="/works" icon="works" label="作品集" />
        <Item to="/tasks" icon="tasks" label="任务中心" />
        <div className="group">进阶（规划中）</div>
        <Item to="/image" icon="image" label="图像" />
        <Item to="/advanced/video" icon="video" label="AI 视频" soon />
        <Item to="/advanced/music" icon="music" label="音乐" soon />
        <Item to="/advanced/workflow" icon="flow" label="工作流" soon />
        <div className="side-foot">单用户 · 本地运行</div>
      </aside>
      <main className="main"><Outlet /></main>
    </div>
  )
}
