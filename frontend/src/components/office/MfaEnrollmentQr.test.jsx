import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'
import MfaEnrollmentQr, { buildTotpUri } from './MfaEnrollmentQr'

const secret = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP'

describe('MFA enrollment QR', () => {
  it('encodes the same secret as a Google Authenticator TOTP URI', () => {
    const uri = buildTotpUri({ secret, account: 'persona+uno@ejemplo.com', issuer: 'Oficina virtual' })
    const parsed = new URL(uri)
    expect(parsed.protocol).toBe('otpauth:')
    expect(parsed.host).toBe('totp')
    expect(decodeURIComponent(parsed.pathname)).toBe('/Oficina virtual:persona+uno@ejemplo.com')
    expect(parsed.searchParams.get('secret')).toBe(secret)
    expect(parsed.searchParams.get('issuer')).toBe('Oficina virtual')
    expect(parsed.searchParams.get('algorithm')).toBe('SHA1')
    expect(parsed.searchParams.get('digits')).toBe('6')
    expect(parsed.searchParams.get('period')).toBe('30')
  })

  it('renders a local SVG with a scan margin and hides malformed enrollment data', () => {
    const { rerender } = render(<MfaEnrollmentQr secret={secret} account="persona@ejemplo.com" issuer="Oficina virtual" label="Escanear código QR" />)
    const qr = screen.getByRole('img', { name: 'Escanear código QR' })
    expect(qr.querySelectorAll('svg path')).toHaveLength(2)
    expect(qr.querySelector('img')).toBeNull()
    rerender(<MfaEnrollmentQr secret="not a key" account="persona@ejemplo.com" issuer="Oficina virtual" label="Escanear código QR" />)
    expect(screen.queryByRole('img', { name: 'Escanear código QR' })).not.toBeInTheDocument()
  })
})
