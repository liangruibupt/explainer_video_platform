// Dev-only mock for the motion editor UI (docs/REMOTION_CONTRACT.md §7). Not part of the build.
// Serves web/dist and fakes the §7 endpoints plus a dummy /motion/ workbench page.
//   node mock-motion.mjs [port] [sampleJobDir]
// Reads the sample job read-only; never writes outside memory.
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const PORT = +(process.argv[2] || 8794)
const SAMPLE = process.argv[3] || '/home/ubuntu/workplace/explainer_video_platform/data/jobs/20261103-150536-b75769'
const DIST = path.join(here, 'dist')
const CARDS = ['data/counter-roll', 'text/kinetic-title', 'diagram/flow-arrows', 'fallback/title-card', 'chart/bar-race']

const base = JSON.parse(fs.readFileSync(path.join(SAMPLE, 'job.json'), 'utf8'))
const now = () => Date.now() / 1000
const jobs = {
  rm1: { ...base, id: 'rm1', status: 'done', renderer: 'remotion',
         scenes: (base.scenes || []).map((s, i) => ({ ...s, card: CARDS[i % CARDS.length] })) },
  html1: { ...base, id: 'html1', status: 'done', renderer: 'html' },
}
const versions = { rm1: [{ n: 1, file: 'final.mp4', status: 'done', created: now() - 3600, warnings: [], error: null }] }
const posts = { jobs: [], versions: [], editor: [] }
let motionHits = 0

const send = (res, code, body, type = 'application/json') => {
  res.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-store' })
  res.end(type === 'application/json' ? JSON.stringify(body) : body)
}
const readBody = (req) => new Promise((ok) => {
  let b = ''; req.on('data', (c) => { b += c }); req.on('end', () => { try { ok(JSON.parse(b || '{}')) } catch { ok({}) } })
})
const MIME = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.mp4': 'video/mp4',
               '.jpg': 'image/jpeg', '.png': 'image/png', '.svg': 'image/svg+xml', '.log': 'text/plain' }
function serveFile(req, res, file) {
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return send(res, 404, { detail: 'not found' })
  const size = fs.statSync(file).size
  const type = MIME[path.extname(file)] || 'application/octet-stream'
  const m = /bytes=(\d*)-(\d*)/.exec(req.headers.range || '')
  if (m) {
    const start = m[1] ? +m[1] : 0
    const end = m[2] ? +m[2] : size - 1
    res.writeHead(206, { 'Content-Type': type, 'Content-Range': `bytes ${start}-${end}/${size}`,
                         'Accept-Ranges': 'bytes', 'Content-Length': end - start + 1 })
    return fs.createReadStream(file, { start, end }).pipe(res)
  }
  res.writeHead(200, { 'Content-Type': type, 'Content-Length': size, 'Accept-Ranges': 'bytes' })
  fs.createReadStream(file).pipe(res)
}

// dummy workbench: writes a project into the key the studio page reads
const MOTION_PAGE = `<!doctype html><meta charset="utf-8"><title>mock workbench</title>
<body style="margin:0;font:14px sans-serif;background:#111;color:#eee;display:grid;place-items:center;height:100vh">
<div><h2>Mock Motion Workbench</h2><p id="s"></p></div>
<script>
const p = { version: 1, fps: 30, width: 1920, height: 1080, mock: true,
  tracks: [{ id: 'shots', items: [{ id: 's01', from: 0, duration: 120 }] }], edited: new Date().toISOString() };
localStorage.setItem('shotcraft-workbench-project-v1', JSON.stringify(p));
document.getElementById('s').textContent = 'project written: ' + location.search;
</script>`

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://x')
  const p = u.pathname
  let m
  if (p === '/api/_mock/posts') return send(res, 200, posts)
  if (p.startsWith('/motion')) {
    motionHits += 1
    if (motionHits <= 2) return send(res, 502, 'starting', 'text/plain') // editor-not-ready first
    return send(res, 200, MOTION_PAGE, 'text/html')
  }
  if (p === '/api/music') return send(res, 200, { synth: '合成垫乐', none: '无音乐' })
  if (p === '/api/jobs' && req.method === 'GET') return send(res, 200, Object.values(jobs))
  if (p === '/api/jobs' && req.method === 'POST') {
    const b = await readBody(req); posts.jobs.push(b)
    return send(res, 200, { ...jobs.rm1, ...b, id: 'rm1' })
  }
  if ((m = /^\/api\/jobs\/([^/]+)$/.exec(p))) return jobs[m[1]] ? send(res, 200, jobs[m[1]]) : send(res, 404, { detail: 'job not found' })
  if ((m = /^\/api\/jobs\/([^/]+)\/editor$/.exec(p)) && req.method === 'POST') {
    posts.editor.push(m[1]); motionHits = 0
    const j = jobs[m[1]]
    if (!j || j.renderer !== 'remotion' || j.status !== 'done') return send(res, 409, { detail: 'job is not a finished remotion job' })
    return send(res, 200, { job_id: m[1], url: '/motion/?import=project' })
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/versions$/.exec(p))) {
    const list = versions[m[1]] || (versions[m[1]] = [])
    if (req.method === 'POST') {
      const b = await readBody(req); posts.versions.push(b)
      const v = { n: list.length + 1, file: `versions/v${list.length + 1}.mp4`, status: 'queued', created: now(),
                  warnings: ['镜头 s02 的时长与配音偏离 0.8 秒（24 帧），配音已恢复原位置，画面可能和旁白对不齐'], error: null }
      list.push(v)
      setTimeout(() => { v.status = 'rendering' }, 2000)
      setTimeout(() => { v.status = 'done' }, 6000)
      return send(res, 202, v)
    }
    return send(res, 200, list)
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/versions\/(\d+)\/video$/.exec(p))) return serveFile(req, res, path.join(SAMPLE, 'final.mp4'))
  if ((m = /^\/api\/jobs\/([^/]+)\/files\/(.+)$/.exec(p))) return serveFile(req, res, path.join(SAMPLE, path.basename(m[2])))
  if (p.startsWith('/api/')) return send(res, 200, [])
  const f = path.join(DIST, path.normalize(p).replace(/^(\.\.[/\\])+/, ''))
  if (p !== '/' && fs.existsSync(f) && fs.statSync(f).isFile()) return serveFile(req, res, f)
  return serveFile(req, res, path.join(DIST, 'index.html'))
})
server.listen(PORT, '127.0.0.1', () => console.log(`mock on http://127.0.0.1:${PORT}`))
