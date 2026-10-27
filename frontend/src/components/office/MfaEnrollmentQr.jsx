import { QRCodeSVG } from 'qrcode.react'

const BASE32_SECRET = /^[A-Z2-7]{16,128}$/

export function buildTotpUri({ secret, account, issuer }) {
  if (typeof secret !== 'string' || !BASE32_SECRET.test(secret)
    || typeof account !== 'string' || !account.trim()
    || typeof issuer !== 'string' || !issuer.trim()) return null

  const name = account.trim()
  const service = issuer.trim()
  return `otpauth://totp/${encodeURIComponent(service)}:${encodeURIComponent(name)}`
    + `?secret=${encodeURIComponent(secret)}&issuer=${encodeURIComponent(service)}`
    + '&algorithm=SHA1&digits=6&period=30'
}

export default function MfaEnrollmentQr({ secret, account, issuer, label }) {
  const uri = buildTotpUri({ secret, account, issuer })
  if (!uri) return null

  // The QR is generated in this browser. The TOTP secret is never sent to a
  // remote image service and leaves the DOM when enrollment is closed.
  return <div role="img" aria-label={label} className="mx-auto w-fit max-w-full rounded-lg border border-borde">
    <QRCodeSVG value={uri} size={208} level="M" marginSize={4}
      bgColor="rgb(var(--qr-paper))" fgColor="rgb(var(--qr-ink))" aria-hidden="true" />
  </div>
}
