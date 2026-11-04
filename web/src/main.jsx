import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './styles.css'
import Layout from './Layout.jsx'
import Workbench from './pages/Workbench.jsx'
import Works from './pages/Works.jsx'
import JobPage from './pages/JobPage.jsx'
import Tasks from './pages/Tasks.jsx'
import Advanced from './pages/Advanced.jsx'
import ImagePage from './pages/ImagePage.jsx'
import VideoPage from './pages/VideoPage.jsx'
import MusicPage from './pages/MusicPage.jsx'
import WorkflowPage from './pages/WorkflowPage.jsx'
import AvatarPage from './pages/AvatarPage.jsx'

// Phase 4 pages (docs/PHASE4_CONTRACTS.md). Each part adds its own file; until it exists the
// route shows a placeholder, so the parts build and run independently.
const optional = import.meta.glob([
  './pages/ChatPage.jsx', './pages/GuidePage.jsx', './pages/WorkflowEditor.jsx',
  './pages/AssetsPage.jsx', './pages/ProjectsPage.jsx', './pages/SettingsPage.jsx',
])
function Soon() {
  return <div className="page"><h1 className="h1-sm">建设中</h1><p className="sub">这个页面还在开发。</p></div>
}
function page(name) {
  const load = optional[`./pages/${name}.jsx`]
  const C = load ? React.lazy(load) : Soon
  return <React.Suspense fallback={<div className="page muted">加载中…</div>}><C /></React.Suspense>
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Workbench />} />
          <Route path="works" element={<Works />} />
          <Route path="tasks" element={<Tasks />} />
          <Route path="jobs/:id" element={<JobPage />} />
          <Route path="image" element={<ImagePage />} />
          <Route path="video" element={<VideoPage />} />
          <Route path="avatar" element={<AvatarPage />} />
          <Route path="music" element={<MusicPage />} />
          <Route path="workflow" element={<WorkflowPage />} />
          <Route path="workflow/edit/:id" element={page('WorkflowEditor')} />
          <Route path="chat" element={page('ChatPage')} />
          <Route path="guide" element={page('GuidePage')} />
          <Route path="library" element={page('AssetsPage')} />
          <Route path="projects" element={page('ProjectsPage')} />
          <Route path="settings" element={page('SettingsPage')} />
          <Route path="advanced/workflow" element={<Navigate to="/workflow" replace />} />
          <Route path="advanced/video" element={<Navigate to="/video" replace />} />
          <Route path="advanced/music" element={<Navigate to="/music" replace />} />
          <Route path="advanced/:kind" element={<Advanced />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
