import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { leerVersion } from './leerVersion.js'

// Versión de Axioma: fuente única, el archivo VERSION de la raíz del repo
// (Fernando, 2026-10-02; antes salía de package.json y no coincidía con el
// rótulo de inicio ni con FastAPI). Inyectada en build time; el backend lee el
// mismo archivo. vitest.config.js hace mergeConfig sobre este archivo, así que
// __APP_VERSION__ también existe en los tests. Sin archivo o con formato inválido, el build y el dev fallan.
const version = leerVersion(fileURLToPath(new URL('../VERSION', import.meta.url)))

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
