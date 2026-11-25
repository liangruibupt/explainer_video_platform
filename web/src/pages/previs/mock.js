// Previs API client with a dev-only mock fallback (docs/PREVIS_CONTRACT.md §11).
// The backend parts (A, B, C, E) land in parallel; until they do, a 404 (or the current
// asset list's 422 for kind=model) in `vite dev` answers from ./mocks instead. In a
// production build the fallback is compiled out and the error is shown as usual.

export class ApiError extends Error {
  constructor(status, message, body) { super(message); this.status = status; this.body = body }
}

export async function papi(path, opts = {}) {
  const isForm = opts.body instanceof FormData
  const r = await fetch(`/api${path}`, { ...opts, headers: isForm ? opts.headers : { 'Content-Type': 'application/json', ...opts.headers } })
  let body = null
  try { body = await r.json() } catch { /* not json */ }
  // The studio serves the SPA for any unknown GET, /api/* included: HTML = route not there.
  if (r.ok && body == null && (r.headers.get('content-type') || '').includes('text/html')) throw new ApiError(404, `接口 /api${path} 还没有`)
  if (!r.ok) {
    const d = body?.detail
    const msg = typeof d === 'string' ? d : d?.message || (d ? JSON.stringify(d) : `${r.status}`)
    throw new ApiError(r.status, msg, body)
  }
  return body
}

const MISSING = new Set([404, 405])

/** Real call; in dev, when the route is not there yet, the named mock handler answers. */
export async function withMock(call, mockName, ...args) {
  try {
    return await call()
  } catch (e) {
    if (!import.meta.env.DEV || !(e instanceof ApiError) || !MISSING.has(e.status)) throw e
    const m = await import('./mocks/index.js')
    return m[mockName](...args)
  }
}

export const isMockId = (id) => typeof id === 'string' && id.includes('_mock_')

/** Model assets: GET /api/assets?kind=model, mock models when the backend does not know `model` yet. */
export async function listModels() {
  try {
    return await papi('/assets?kind=model')
  } catch (e) {
    if (!import.meta.env.DEV || !(e instanceof ApiError) || ![404, 422].includes(e.status)) throw e
    return (await import('./mocks/index.js')).models()
  }
}

export const turntableUrl = (a) => a.turntable_url || `/api/assets/${a.id}/turntable`

// Contract gap: the library record already uses `size` for the file size in bytes, and
// ModelMeta (§4.4) adds `size` = [w, h, d] metres to the same meta.json. Accept both until
// part A settles it: an array is the model size, a number is the file size.
export const modelSize = (a) => (Array.isArray(a?.size) ? a.size : Array.isArray(a?.model_size) ? a.model_size : null)
export const fileBytes = (a) => (typeof a?.size === 'number' ? a.size : a?.file_size ?? a?.bytes ?? 0)
/** The record as the viewer wants it: ModelMeta with `size` in metres. */
export const viewerMeta = (a) => (a && modelSize(a) ? { ...a, size: modelSize(a) } : a)
