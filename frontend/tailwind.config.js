/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      // Marca Axioma (2026-09-12): la misma tipografía y los mismos dorados de
      // la portada de Six Impossible Things (--gold, --gold-light, --gold-dark).
      fontFamily: {
        marca: ['"IBM Plex Serif"', 'Georgia', 'serif'],
      },
      colors: {
        oro: {
          DEFAULT: '#c9a84c',
          claro: '#e8d5a3',
          oscuro: '#8a6d2f',
        },
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
