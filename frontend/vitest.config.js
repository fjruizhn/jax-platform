import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config.js'

export default mergeConfig(viteConfig, defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    // Sondeo del PDF escaneado del chat (BottomBar.jsx): en las pruebas los tiempos son cortos y
    // FIJOS -- los mismos en local y en CI --, para que `npm test` pase sin variables de entorno
    // y las pruebas de backoff/tope calculen sus esperados con estos valores.
    // Secuencia de esperas: 10, 20, 40, 40, ... ms; tope total 400 ms.
    env: {
      VITE_CHAT_PDF_POLL_INITIAL_MS: '10',
      VITE_CHAT_PDF_POLL_MAX_MS: '40',
      VITE_CHAT_PDF_POLL_TIMEOUT_MS: '400',
    },
  },
}))
