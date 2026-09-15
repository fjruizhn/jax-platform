import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// I-1 (revisión final PR 2, 2026-09-14): AdminSidebar.jsx tenía "Axioma v0.3"
// fijo (Principio IV). Fuente única: package.json, inyectada en build time.
// vitest.config.js hace mergeConfig sobre este archivo, así que __APP_VERSION__
// también existe en los tests.
const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
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
