// Narration in use for the 「配音」 panel: the job's scenes.json, overlaid with the takes the LIVE
// take was built on (its `base` chain, oldest first). Opening a version restores that version's
// take, so an applied take is not necessarily live; with no live record the original voice
// (take 0) is live. Records come from GET /api/jobs/{id}/revoice (each has `live` and `base`).
export function liveNarrations(scenes, records) {
  const out = {}
  for (const s of scenes || []) out[s.id] = s.narration || ''
  const byN = new Map((records || []).map((r) => [r.n, r]))
  const chain = []
  for (let r = (records || []).find((x) => x.live); r && !chain.includes(r); r = byN.get(r.base)) chain.unshift(r)
  for (const r of chain) {
    for (const s of r.scenes || []) if (s.id in out && s.new_text != null) out[s.id] = s.new_text
  }
  return out
}
