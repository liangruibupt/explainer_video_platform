// Dev-only mock for the Opus chat panel (docs/MOTION_ASSIST_CONTRACT.md §3–§5). Not part of the build.
// Serves web/dist, fakes /api/jobs/{id}, /editor, /versions, /assist, /scene-edits and a dummy /motion/
// workbench that keeps its project in localStorage (and flushes it on beforeunload, like the real one).
//   node mock-assist.mjs [port] [sampleJobDir]
// Reads the sample job read-only; state lives in memory.
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const PORT = +(process.argv[2] || 8803)
const SAMPLE = process.argv[3] || '/home/ubuntu/workplace/explainer_video_platform/data/jobs/20261111-075413-d48e69'
const DIST = path.join(here, 'dist')

const base = JSON.parse(fs.readFileSync(path.join(SAMPLE, 'job.json'), 'utf8'))
const TL = JSON.parse(fs.readFileSync(path.join(SAMPLE, 'remotion/src/data/timeline.json'), 'utf8'))
const now = () => Date.now() / 1000
const jobs = {
  rm1: { ...base, id: 'rm1', status: 'done', renderer: 'remotion' },
  html1: { ...base, id: 'html1', status: 'done', renderer: 'html' },
}
const titleOf = (sid) => (base.scenes || []).find((s) => s.id === sid)?.title || sid
const OLD_DEFAULTS = (sid) => ({ title: titleOf(sid), accent: '#4c9aff', show: true })
const NEW_DEFAULTS = { title: '折线图版本', accent: '#ff8800', show: false, lineWidth: 6 }

// the project the dummy workbench starts from (same shape as the workbench ProjectData)
const INITIAL = {
  name: TL.scenes[0].label, fps: TL.fps, width: TL.width, height: TL.height, background: TL.background,
  source: `mock@${TL.revision}`,
  tracks: [
    { id: 'tr-shots', name: '镜头', clips: TL.scenes.map((s) => ({
      id: `clip-${s.id}`, cardId: `proj:shot:scene-${s.id}`, start: s.from, duration: s.duration, inOffset: 0,
      speed: 1, opacity: 1, scale: 1, x: 0, y: 0, props: OLD_DEFAULTS(s.id), label: s.label })) },
    { id: 'tr-cap', name: '字幕', clips: [{ id: 'clip-cap1', cardId: 'proj:caption:caption', start: 0, duration: 90,
      inOffset: 0, speed: 1, opacity: 1, scale: 1, x: 0, y: 0, props: { text: '三年三倍' }, label: '三年三倍' }] },
    { id: 'tr-music', name: '音乐', clips: [{ id: 'clip-voice', cardId: 'audio', start: 0, duration: 2400, inOffset: 0,
      speed: 1, opacity: 1, scale: 1, x: 0, y: 0, props: { src: 'audio/voice_bgm.wav' }, label: '配音（已锁定）' }] },
  ],
}

const versions = { rm1: [{ n: 1, file: 'final.mp4', status: 'done', created: now() - 3600, warnings: [], error: null }] }
const edits = { rm1: [] }
const posts = { versions: [], editor: [], assist: [], edits: [], motion: 0 }

const send = (res, code, body, type = 'application/json') => {
  res.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-store' })
  res.end(type === 'application/json' ? JSON.stringify(body) : body)
}
const readBody = (req) => new Promise((ok) => {
  let b = ''; req.on('data', (c) => { b += c }); req.on('end', () => { try { ok(JSON.parse(b || '{}')) } catch { ok({}) } })
})
const MIME = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.mp4': 'video/mp4',
               '.jpg': 'image/jpeg', '.png': 'image/png', '.svg': 'image/svg+xml' }
function serveFile(res, file) {
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return send(res, 404, { detail: 'not found' })
  res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' })
  fs.createReadStream(file).pipe(res)
}

// dummy workbench: keeps the stored project (or writes INITIAL), shows it, flushes on beforeunload
const MOTION_PAGE = `<!doctype html><meta charset="utf-8"><title>mock workbench</title>
<body style="margin:0;font:13px sans-serif;background:#111;color:#eee;padding:16px">
<h3 style="margin:0 0 6px">Mock Motion Workbench <small id="q"></small></h3>
<pre id="proj" style="white-space:pre-wrap;font:12px monospace"></pre>
<script>
const KEY = 'shotcraft-workbench-project-v1';
let p = null;
try { p = JSON.parse(localStorage.getItem(KEY)); } catch (e) {}
if (!p || !Array.isArray(p.tracks)) { p = ${JSON.stringify(INITIAL)}; localStorage.setItem(KEY, JSON.stringify(p)); }
window.__project = p;
document.getElementById('q').textContent = 'loaded ' + new Date().toISOString().slice(11, 23) + ' ' + location.search;
document.getElementById('proj').textContent = p.tracks[0].clips.map(function (c) {
  return c.id + ' start=' + c.start + ' dur=' + c.duration + ' ' + JSON.stringify(c.props); }).join('\\n');
window.addEventListener('beforeunload', function () { localStorage.setItem(KEY, JSON.stringify(window.__project)); });
</script>`

const still = (n, k) => `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
<rect width="1920" height="1080" fill="#0b1020"/><polyline points="200,900 700,640 1200,520 1700,200" fill="none"
stroke="#ff8800" stroke-width="${20 + k * 8}"/><text x="960" y="160" fill="#fff" font-size="90" text-anchor="middle"
font-family="sans-serif">edit ${n} · still ${k + 1}</text></svg>`

function assistReply(body) {
  const msg = String(body.message || '')
  const sid = body.scene_id || 'growth_math'
  if (msg.includes('500')) return [500, { detail: 'Opus call failed' }]
  if (msg.includes('409')) return [409, { detail: '只有已完成的 Remotion 作品才能用 Opus 修改' }]
  if (msg.includes('旁白')) {
    return [200, { reply: '旁白和配音在这里改不了。想换旁白的话，请用新的旁白重新制作这个作品。',
      changes: [], project: null, ops: [], rejected: [], scene_edit: null }]
  }
  if (msg.includes('折线') || msg.includes('画面')) {
    return [200, { reply: `「${titleOf(sid)}」要换成折线图，需要改写这段画面的代码。`, changes: [], project: null,
      ops: [], rejected: [], scene_edit: { scene_id: sid, instruction: '把柱状图换成折线图，保留年份标签' } }]
  }
  const project = JSON.parse(JSON.stringify(body.project || {}))
  const clip = (project.tracks || []).flatMap((t) => t.clips || []).find((c) => c.cardId === `proj:shot:scene-${sid}`)
  if (!clip) return [200, { reply: '没找到要改的片段。', changes: [], project: null, ops: [], rejected: [], scene_edit: null }]
  const old = clip.props.title
  clip.props.title = '三年三倍的含义'
  return [200, { reply: '标题已经改好，点「应用修改」放进编辑器。', changes: [`${clip.label} 标题：'${old}' → '三年三倍的含义'`],
    project, ops: [{ op: 'set_props', clip_id: clip.id, props: { title: '三年三倍的含义' } }],
    rejected: ['clip-nope 不存在，已忽略'], scene_edit: null }]
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://x')
  const p = u.pathname
  let m
  if (p === '/api/_mock/posts') return send(res, 200, posts)
  if (p.startsWith('/motion')) { posts.motion += 1; return send(res, 200, MOTION_PAGE, 'text/html') }
  if ((m = /^\/api\/jobs\/([^/]+)$/.exec(p))) return jobs[m[1]] ? send(res, 200, jobs[m[1]]) : send(res, 404, { detail: 'job not found' })
  if ((m = /^\/api\/jobs\/([^/]+)\/editor$/.exec(p)) && req.method === 'POST') {
    posts.editor.push(m[1])
    const j = jobs[m[1]]
    if (!j || j.renderer !== 'remotion') return send(res, 409, { detail: 'job is not a finished remotion job' })
    return send(res, 200, { job_id: m[1], url: '/motion/?import=project' })
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/versions$/.exec(p))) {
    const list = versions[m[1]] || (versions[m[1]] = [])
    if (req.method === 'POST') {
      const b = await readBody(req); posts.versions.push(b)
      const v = { n: list.length + 1, status: 'queued', created: now(), warnings: [], error: null }
      list.push(v)
      setTimeout(() => { v.status = 'done' }, 3000)
      return send(res, 202, v)
    }
    return send(res, 200, list)
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/assist$/.exec(p)) && req.method === 'POST') {
    const b = await readBody(req); posts.assist.push(b)
    const [code, body] = assistReply(b)
    return send(res, code, body)
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/scene-edits$/.exec(p))) {
    const list = edits[m[1]] || (edits[m[1]] = [])
    if (req.method === 'POST') {
      const b = await readBody(req); posts.edits.push(b)
      if (list.some((e) => e.status === 'queued' || e.status === 'running')) {
        return send(res, 409, { detail: '这个作品还有一个画面修改在进行，等它完成后再试' })
      }
      const n = list.length + 1
      const e = { n, scene_id: b.scene_id, instruction: b.instruction, status: 'queued', created: now(), ended: null,
        stills: [], frames: [], layout: null, defaults: null, error: null, log_tail: 'queued' }
      list.push(e)
      setTimeout(() => { e.status = 'running'; e.log_tail = 'opus: rewriting growth_math.tsx\ntsc --noEmit' }, 1500)
      setTimeout(() => {
        Object.assign(e, { status: 'ready', ended: now(), frames: [31, 117, 247],
          stills: [0, 1, 2].map((k) => `/api/jobs/${m[1]}/scene-edits/${n}/stills/${k}.png`),
          layout: { status: 'issues', issues: ['too_small: f31 y 轴刻度文字约 11 px，偏小'] },
          defaults: NEW_DEFAULTS, log_tail: 'stills ok' })
      }, 5000)
      return send(res, 202, e)
    }
    return send(res, 200, [...list].reverse())
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/scene-edits\/(\d+)\/stills\/(\d+)\.png$/.exec(p))) {
    return send(res, 200, still(+m[2], +m[3]), 'image/svg+xml')
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/scene-edits\/(\d+)(?:\/(apply|discard|undo))?$/.exec(p))) {
    const list = edits[m[1]] || []
    const e = list.find((x) => x.n === +m[2])
    if (!e) return send(res, 404, { detail: '没有这次画面修改' })
    const act = m[3]
    if (!act) return send(res, 200, e)
    if (req.method !== 'POST') return send(res, 405, { detail: 'POST only' })
    if (act === 'apply') {
      if (e.status !== 'ready') return send(res, 409, { detail: '只有已完成预览的修改才能应用' })
      e.status = 'applied'
      return send(res, 200, { status: 'applied', scene_id: e.scene_id, defaults: e.defaults })
    }
    if (act === 'discard') {
      if (e.status !== 'ready') return send(res, 409, { detail: '只有已完成预览的修改才能放弃' })
      e.status = 'discarded'
      return send(res, 200, { status: 'discarded' })
    }
    const last = [...list].reverse().find((x) => x.scene_id === e.scene_id && x.status === 'applied')
    if (!last) return send(res, 409, { detail: '这个镜头没有可以撤销的修改' })
    last.status = 'undone'
    return send(res, 200, { status: 'undone', scene_id: e.scene_id, defaults: OLD_DEFAULTS(e.scene_id) })
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/files\/(.+)$/.exec(p))) return serveFile(res, path.join(SAMPLE, path.basename(m[2])))
  if (p.startsWith('/api/')) return send(res, 200, [])
  const f = path.join(DIST, path.normalize(p).replace(/^(\.\.[/\\])+/, ''))
  if (p !== '/' && fs.existsSync(f) && fs.statSync(f).isFile()) return serveFile(res, f)
  return serveFile(res, path.join(DIST, 'index.html'))
})
server.listen(PORT, '127.0.0.1', () => console.log(`mock on http://127.0.0.1:${PORT}`))
