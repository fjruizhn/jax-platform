import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import '@testing-library/jest-dom'
import Message from './Message'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

function renderMessage(message) {
  return render(
    <I18nProvider>
      <Message message={message} />
    </I18nProvider>
  )
}

describe('Message contract degradation footnote', () => {
  it('muestra la nota cuando contract_degraded es true', () => {
    renderMessage({
      facet: 'jekyll',
      content: 'respuesta cruda sin parsear',
      contract_degraded: true,
    })
    expect(screen.getByText(/no cumplió el formato esperado/i)).toBeInTheDocument()
  })

  it('no muestra la nota cuando contract_degraded es false', () => {
    renderMessage({
      facet: 'jekyll',
      content: 'respuesta normal',
      contract_degraded: false,
    })
    expect(screen.queryByText(/no cumplió el formato esperado/i)).not.toBeInTheDocument()
  })

  it('no muestra la nota cuando contract_degraded no está presente (mensajes viejos)', () => {
    renderMessage({ facet: 'jekyll', content: 'mensaje de antes de este cambio' })
    expect(screen.queryByText(/no cumplió el formato esperado/i)).not.toBeInTheDocument()
  })
})

describe('Message F2-C governed projection', () => {
  it('renders governed assistant text literally, not as provider Markdown', () => {
    renderMessage({
      facet: 'jekyll',
      content: '# VERIFIED\n[CURRENT](https://example.invalid)',
      governed_plain: true,
    })
    expect(screen.getByTestId('governed-plain')).toHaveTextContent('# VERIFIED')
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('uses a fixed governed speaker label instead of a runtime facet', () => {
    renderMessage({
      facet: 'jekyll',
      content: 'I could not verify the current state.',
      governed_plain: true,
      timestamp: '2026-10-01T00:00:00Z',
    })
    expect(screen.getByText(es.governedResponseLabel)).toBeInTheDocument()
    expect(screen.queryByText('jekyll')).not.toBeInTheDocument()
  })

  it('preserves existing Markdown rendering for user-authored messages', () => {
    renderMessage({ facet: 'user', content: '# Mi encabezado', governed_plain: true })
    expect(screen.getByRole('heading', { name: 'Mi encabezado' })).toBeInTheDocument()
  })
})

// I-1 (revisión final PR 3, 2026-09-14): el alt de <img> (imagen generada /
// adjunto) estaba hardcodeado en español, sin pasar por i18n.
describe('Message -- alt de imagen y adjunto desde i18n (I-1)', () => {
  it('imagen generada: usa t.altGeneratedImage cuando no hay content', () => {
    renderMessage({ facet: 'dalle', image_url: 'https://example.com/img.png', content: '' })
    expect(screen.getByAltText(es.altGeneratedImage)).toBeInTheDocument()
  })

  it('adjunto imagen: usa t.altAttachment cuando no hay filename', () => {
    renderMessage({
      facet: 'user',
      content: '',
      attachment: { type: 'image', base64: 'data:image/png;base64,xxx' },
    })
    expect(screen.getByAltText(es.altAttachment)).toBeInTheDocument()
  })

  it('en inglés, el alt sale en inglés, no el literal fijo en español', () => {
    localStorage.setItem('jax_lang', 'en')
    renderMessage({ facet: 'dalle', image_url: 'https://example.com/img.png', content: '' })
    expect(screen.getByAltText(en.altGeneratedImage)).toBeInTheDocument()
    expect(screen.queryByAltText('imagen generada')).not.toBeInTheDocument()
    localStorage.clear()
  })
})

// I-2 (revisión final PR 3, 2026-09-14): la hora del mensaje fijaba 'es-HN'
// en toLocaleTimeString sin importar el idioma activo (Ruling 29, ya
// aplicado a AdminUsers/AdminCosts/AdminRepository). Ahora sale de
// localeFor(lang).
describe('Message -- hora sigue el idioma activo, no es-HN fijo (I-2)', () => {
  it('en inglés, la hora usa en-US, no es-HN fijo', () => {
    localStorage.setItem('jax_lang', 'en')
    const timestamp = '2026-03-14T18:30:00Z'
    renderMessage({ facet: 'jekyll', content: 'hola', timestamp })

    const esperado = new Date(timestamp).toLocaleTimeString('en-US')
    const fijoEsHN = new Date(timestamp).toLocaleTimeString('es-HN')
    expect(screen.getByText(esperado)).toBeInTheDocument()
    if (esperado !== fijoEsHN) {
      expect(screen.queryByText(fijoEsHN)).not.toBeInTheDocument()
    }
    localStorage.clear()
  })

  it('en español (default), la hora usa es-HN', () => {
    const timestamp = '2026-03-14T18:30:00Z'
    renderMessage({ facet: 'jekyll', content: 'hola', timestamp })
    expect(screen.getByText(new Date(timestamp).toLocaleTimeString('es-HN'))).toBeInTheDocument()
  })
})

describe('Message texto gobernado legible', () => {
  it('decodifica las entidades de html.escape y las muestra como texto', () => {
    renderMessage({
      facet: 'governed_response',
      content: 'It&#x27;s &quot;fine&quot; &amp; &lt;b&gt;no&lt;/b&gt;',
      governed_plain: true,
    })
    const nodo = screen.getByTestId('governed-plain')
    expect(nodo).toHaveTextContent('It\'s "fine" & <b>no</b>')
    expect(nodo.textContent).not.toMatch(/&#x27;|&amp;|&quot;|&lt;/)
    // el markup decodificado sigue siendo texto: no hay elementos
    expect(nodo.querySelector('b')).toBeNull()
  })

  it('contenido CRUDO sin escapar se pinta literal, nunca como HTML', () => {
    const { container } = renderMessage({
      facet: 'governed_response',
      content: '<img src=x onerror=alert(1)>',
      governed_plain: true,
    })
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByTestId('governed-plain').textContent).toBe('<img src=x onerror=alert(1)>')
  })

  it('un entity-escape que decodifica a markup tampoco produce elementos', () => {
    const { container } = renderMessage({ facet: 'governed_response', content: '&lt;img src=x onerror=1&gt;', governed_plain: true })
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByTestId('governed-plain').textContent).toBe('<img src=x onerror=1>')
  })

  it('el Markdown decodificado no se interpreta: **x** no produce <strong>', () => {
    const { container } = renderMessage({ facet: 'governed_response', content: '**x** &amp; # t', governed_plain: true })
    expect(container.querySelector('strong')).toBeNull()
    expect(container.querySelector('h1')).toBeNull()
    expect(screen.getByTestId('governed-plain').textContent).toBe('**x** & # t')
  })

  it('el aviso degradado muestra el texto i18n y no el del servidor', () => {
    renderMessage({
      facet: 'governed_response',
      content: 'The response could not be verified safely.',
      governed_plain: true,
      contract_degraded: true,
      aviso_code: 'respuesta_no_verificable',
    })
    expect(screen.getByTestId('governed-plain')).toHaveTextContent(es.respuestaNoVerificable)
    expect(screen.queryByText(/could not be verified safely/i)).not.toBeInTheDocument()
    // sin la nota duplicada de formato: el aviso ya lo dice todo
    expect(screen.queryByText(es.contractDegradedNote)).not.toBeInTheDocument()
  })

  it('las claves es/en del aviso existen y difieren', () => {
    expect(es.respuestaNoVerificable).toBeTruthy()
    expect(en.respuestaNoVerificable).toBeTruthy()
    expect(es.respuestaNoVerificable).not.toBe(en.respuestaNoVerificable)
  })
})
