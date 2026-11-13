// node --test web/src/components/voiceLive.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { liveNarrations } from './voiceLive.js'

const scenes = [{ id: 'a', narration: 'A0' }, { id: 'b', narration: 'B0' }]
const t1 = { n: 1, base: 0, status: 'applied', scenes: [{ id: 'a', new_text: 'A1' }] }
const t2 = { n: 2, base: 1, status: 'applied', scenes: [{ id: 'b', new_text: 'B2' }] }

test('applied take that is not live (another version opened) does not change the texts', () => {
  assert.deepEqual(liveNarrations(scenes, [{ ...t1, live: false }]), { a: 'A0', b: 'B0' })
})

test('live take shows its narration', () => {
  assert.deepEqual(liveNarrations(scenes, [{ ...t1, live: true }]), { a: 'A1', b: 'B0' })
})

test('live take built on an earlier take includes the whole base chain', () => {
  assert.deepEqual(liveNarrations(scenes, [{ ...t2, live: true }, { ...t1, live: false }]), { a: 'A1', b: 'B2' })
})

test('ready (not applied) take that is not live is ignored', () => {
  const ready = { n: 3, base: 0, status: 'ready', live: false, scenes: [{ id: 'a', new_text: 'X' }] }
  assert.deepEqual(liveNarrations(scenes, [ready]), { a: 'A0', b: 'B0' })
})
