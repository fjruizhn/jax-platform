import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Versión de Axioma: fuente única, el archivo VERSION de la raíz del repo
// (Fernando, 2026-10-02; antes salía de package.json y no coincidía con el
// rótulo de inicio ni con FastAPI). Inyectada en build time; el backend lee el
// mismo archivo. vitest.config.js hace mergeConfig sobre este archivo, así que
// __APP_VERSION__ también existe en los tests. Sin archivo, el build falla.
const version = readFileSync(new URL('../VERSION', import.meta.url), 'utf8').trim()

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(version),
  },
  server: {
    port: 5173,
    allowedHosts: ['axioma-ia.io', 'www.axioma-ia.io'],
    proxy: {
      '/api': { target: 'http://localhost:8080', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8080', ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
  },
})
