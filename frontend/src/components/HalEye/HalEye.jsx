import './HalEye.css'
import { useJaxStore, getEyeState } from '../../store/useJaxStore'

export default function HalEye({ size = 220 }) {
  const { facets, activePipelines, lasManos, killSwitchActive, generatingImage } = useJaxStore()
  const eye = getEyeState(facets, activePipelines, lasManos, killSwitchActive, generatingImage)

  const r = size / 2
  const outerR = r * 0.92
  const irisR = r * 0.38
  const pupilR = r * 0.15
  const animClass = `hal-anim-${eye.animation.replace('-', '-')}`

  return (
    <div className="hal-eye-container" style={{ '--eye-color': eye.color }}>
      <div className={animClass}>
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          className="hal-eye-svg"
          aria-label={`Ojo HAL — ${eye.label}`}
        >
          {/* Outer housing */}
          <circle cx={r} cy={r} r={outerR} fill="#0f172a" stroke="#1e293b" strokeWidth="3" />

          {/* Glow rings */}
          <circle
            cx={r} cy={r} r={outerR * 0.88}
            fill="none"
            stroke={eye.color}
            strokeWidth="1.5"
            opacity="0.3"
            className="hal-glow-ring"
          />
          <circle
            cx={r} cy={r} r={outerR * 0.72}
            fill="none"
            stroke={eye.color}
            strokeWidth="1"
            opacity="0.2"
            className="hal-glow-ring"
          />

          {/* Iris */}
          <circle
            cx={r} cy={r} r={irisR}
            fill={eye.color}
            opacity="0.9"
            className="hal-iris"
          />

          {/* Inner iris detail */}
          <circle
            cx={r} cy={r} r={irisR * 0.75}
            fill="#0f172a"
            opacity="0.5"
          />

          {/* Pupil */}
          <circle
            cx={r} cy={r} r={pupilR}
            fill="#0a0f1a"
          />

          {/* Specular highlight */}
          <circle
            cx={r - irisR * 0.25}
            cy={r - irisR * 0.25}
            r={pupilR * 0.35}
            fill="white"
            opacity="0.6"
          />

          {/* Outer glow ring for gate/kill */}
          {eye.animation === 'blink' && (
            <circle
              cx={r} cy={r} r={outerR * 0.97}
              fill="none"
              stroke={eye.color}
              strokeWidth="3"
              opacity="0.8"
              className="hal-anim-blink"
            />
          )}
        </svg>
      </div>
      <div
        className="absolute bottom-0 text-xs font-mono opacity-40"
        style={{ color: eye.color }}
      >
        {eye.label}
      </div>
    </div>
  )
}
