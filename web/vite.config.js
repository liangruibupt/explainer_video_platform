import { fileURLToPath } from 'node:url'
import { defineConfig, searchForWorkspaceRoot } from 'vite'
import react from '@vitejs/plugin-react'

// docs/PREVIS_CONTRACT.md §12: the viewer poses parts with the same core/rig.ts as the
// builder and the renderer, imported straight from the server tree.
const previsCore = fileURLToPath(new URL('../server/app/previs/js/core', import.meta.url))

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@previs-core': previsCore },
    // core/rig.ts imports 'three' from outside web/: make it resolve to web's copy.
    dedupe: ['three'],
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), previsCore] },
    proxy: { '/api': process.env.EVP_API || 'http://127.0.0.1:8765' },
  },
})
