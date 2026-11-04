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
          <Route path="music" element={<MusicPage />} />
          <Route path="advanced/video" element={<Navigate to="/video" replace />} />
          <Route path="advanced/music" element={<Navigate to="/music" replace />} />
          <Route path="advanced/:kind" element={<Advanced />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
