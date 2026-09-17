import { memo } from 'react'
import './HalEye.css'
import { useJaxStore, getEyeState } from '../../store/useJaxStore'
import { EYE_ESTADO_REPOSO } from '../../store/eyeRestState'
import { useI18n } from '../../i18n/index.jsx'
import { colorToken } from '../../tema/tokens'

// `reposo` (Ruling 21, fix-vivo-brief.md §C, decisión de Fernando
// 2026-09-14): fuerza el estado de reposo del panel -- el token de jax_local,
// pulse-slow, sin etiqueta visible -- SIN llamar a getEyeState ni leer la store. Lo usa
// Login, que no tiene sesión: mostrar ahí "LAS MANOS DOWN" (lo que
// getEyeState calcularía con la store vacía) se vería roto. LeftPanel no pasa
// la prop y sigue exactamente igual, dirigido por la store.
function HalEye({ size = 220, reposo = false }) {
  const facets = useJaxStore((s) => s.facets)
  const activePipelines = useJaxStore((s) => s.activePipelines)
  const lasManos = useJaxStore((s) => s.lasManos)
  const killSwitchActive = useJaxStore((s) => s.killSwitchActive)
  const generatingImage = useJaxStore((s) => s.generatingImage)
  const { t } = useI18n()
  const eye = reposo
    ? { ...EYE_ESTADO_REPOSO, label: t.eyeIdle }
    : getEyeState(facets, activePipelines, lasManos, killSwitchActive, generatingImage, {
        reposo: t.eyeIdle,
        killSwitch: t.eyeKillSwitch,
        dalle: t.eyeDallE3,
        lasManosDown: t.eyeLasManosDown,
        gate: t.eyeGate,
        jacobs: t.eyeJacobs,
      })

  // El estado trae el NOMBRE de un token (Task 20). Los atributos de
  // presentación SVG no aceptan var() de forma fiable (spec §7.3): el color
  // va por `style`.
  const color = colorToken(eye.token)

  const r = size / 2
  const outerR = r * 0.92
  const irisR = r * 0.38
  const pupilR = r * 0.15
  const animClass = `hal-anim-${eye.animation.replace('-', '-')}`

  return (
    <div className="hal-eye-container" style={{ '--eye-color': color }}>
      <div className={animClass}>
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          className="hal-eye-svg"
          aria-label={t.halEyeAriaLabel(eye.label)}
        >
          {/* Outer housing */}
          <circle cx={r} cy={r} r={outerR} className="fill-fondo stroke-borde" strokeWidth="3" />

          {/* Glow rings */}
          <circle
            cx={r} cy={r} r={outerR * 0.88}
            fill="none"
            style={{ stroke: color }}
            strokeWidth="1.5"
            opacity="0.3"
            className="hal-glow-ring"
          />
          <circle
            cx={r} cy={r} r={outerR * 0.72}
            fill="none"
            style={{ stroke: color }}
            strokeWidth="1"
            opacity="0.2"
            className="hal-glow-ring"
          />

          {/* Iris */}
          <circle
            cx={r} cy={r} r={irisR}
            style={{ fill: color }}
            opacity="0.9"
            className="hal-iris"
          />

          {/* Inner iris detail */}
          <circle
            cx={r} cy={r} r={irisR * 0.75}
            className="fill-fondo"
            opacity="0.5"
          />

          {/* Pupil */}
          <circle
            cx={r} cy={r} r={pupilR}
            className="fill-fondo"
          />

          {/* Specular highlight */}
          <circle
            cx={r - irisR * 0.25}
            cy={r - irisR * 0.25}
            r={pupilR * 0.35}
            className="fill-sobre-color"
            opacity="0.6"
          />

          {/* Outer glow ring for gate/kill */}
          {eye.animation === 'blink' && (
            <circle
              cx={r} cy={r} r={outerR * 0.97}
              fill="none"
              style={{ stroke: color }}
              strokeWidth="3"
              opacity="0.8"
              className="hal-anim-blink"
            />
          )}
        </svg>
      </div>
      {!reposo && (
        <div
          className="absolute bottom-0 text-xs font-mono"
          style={{ color }}
        >
          {eye.label}
        </div>
      )}
    </div>
  )
}

export default memo(HalEye)
