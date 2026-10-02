// Config de Vite SOLO para la revisión visual de Proyectos E1 (loadtest/proyectos_e1.py visual).
// Reutiliza la config de la app y cambia el destino del proxy: el de la app apunta a
// localhost:8080, que es PRODUCCIÓN en esta máquina; aquí va al backend de prueba.
import { mergeConfig } from 'vite'
import base from './vite.config.js'

const destino = process.env.PROYECTOS_E1_BACKEND_URL
if (!destino) throw new Error('falta PROYECTOS_E1_BACKEND_URL: no se levanta Vite sin saber a qué backend apunta')

export default mergeConfig(base, {
  server: {
    proxy: {
      '/api': { target: destino, changeOrigin: true },
      '/ws': { target: destino.replace(/^http/, 'ws'), ws: true, changeOrigin: true },
    },
  },
})
