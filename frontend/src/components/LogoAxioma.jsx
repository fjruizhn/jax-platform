import { useI18n } from '../i18n/index.jsx'
// La fuente de la marca va en el bundle (@fontsource, OFL), no desde Google
// Fonts: cada visita le avisaría a un tercero. Solo el subset latin y los dos
// pesos que usa el logotipo.
import '@fontsource/ibm-plex-serif/latin-600.css'
import '@fontsource/ibm-plex-serif/latin-300-italic.css'

// Logotipo de texto (2026-09-12, pedido de Fernando): reemplaza "JAX | Platform
// v0.1". Es la marca de la portada de Six Impossible Things: IBM Plex Serif y
// los tres dorados de ese documento (tailwind.config.js, colores `oro`).
//
// Una sola pieza fuerte: "Axioma" en serif 600 dorado. El lema acompaña en
// itálica fina y un dorado más apagado, sobre la misma línea de base. En
// pantallas angostas queda solo "Axioma". Los dorados son tokens (oro,
// oro-claro, oro-oscuro en src/tema/tokens.css): en claro ya vienen
// oscurecidos para leerse sobre blanco.
export default function LogoAxioma() {
  const { t } = useI18n()
  return (
    <span className="flex items-baseline gap-2 leading-none select-none">
      <span className="font-marca font-semibold text-lg tracking-wide text-oro">
        {t.brandName}
      </span>
      <span aria-hidden="true" className="hidden sm:inline font-marca text-sm text-oro-oscuro">
        ·
      </span>
      <span className="hidden sm:inline font-marca italic font-light text-sm tracking-[0.08em] text-oro-claro">
        {t.brandTagline}
      </span>
    </span>
  )
}
