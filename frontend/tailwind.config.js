import { TOKENS } from './src/tema/tokens.js'

// Tokens del tema (spec 2026-09-14-tema-tokens §4): un color por token, con
// opacidad. Los valores están en src/tema/tokens.css; acá sólo los nombres.
const token = (nombre) => `rgb(var(--${nombre}) / <alpha-value>)`

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      // Marca Axioma (2026-09-12): la misma tipografía y los mismos dorados,
      // ahora en tokens.css.
      fontFamily: {
        marca: ['"IBM Plex Serif"', 'Georgia', 'serif'],
      },
      colors: {
        ...Object.fromEntries(TOKENS.map((n) => [n, token(n)])),
        // Colores viejos del panel: se van en el PR 4 del rollout, cuando
        // Dashboard migra (hoy los usa con bg-hal-bg y text-hal-text).
        hal: {
          bg: '#0f172a',
          panel: '#1e293b',
          border: '#334155',
          text: '#e2e8f0',
          muted: '#64748b',
        },
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'pulse-fast': 'pulse 0.8s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'blink': 'blink 1s step-end infinite',
      },
      keyframes: {
        blink: {
          '0%, 100%': { opacity: 1 },
          '50%': { opacity: 0 },
        },
      },
    },
  },
  plugins: [],
}
