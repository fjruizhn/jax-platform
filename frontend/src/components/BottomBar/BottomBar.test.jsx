import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
}))

import BottomBar from './BottomBar'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import { waitFor } from '@testing-library/react'
import api from '../../api/client'
import es from '../../i18n/es.js'

function renderBar() {
  return render(
    <I18nProvider>
      <BottomBar />
    </I18nProvider>
  )
}

// Ruling 30 (decisión de Fernando, "superficie + borde"): ningún texto va
// sobre un fondo sólido del color de la faceta. En modo chat, Enviar lleva
// borde y texto de la faceta activa sobre superficie.
describe('BottomBar -- botón Enviar en modo chat', () => {
  beforeEach(() => {
    useJaxStore.setState({ activeFacet: 'hyde' })
  })

  it('usa el token de la faceta activa en borde y texto, sin fondo de faceta', () => {
    const { container } = renderBar()
    // U34: el campo del chat es el punto de entrada del foco tras el cambio
    // obligatorio de contraseña (RequireAuth busca [data-foco-inicial]).
    expect(container.querySelector('[data-foco-inicial]')).toBe(container.querySelector('textarea'))
    const enviar = screen.getByRole('button', { name: 'Enviar' })
    const estilo = enviar.getAttribute('style') || ''
    expect(estilo).toContain('border-color: rgb(var(--faceta-hyde) / 1)')
    expect(estilo).toMatch(/(^|;\s*)color: rgb\(var\(--faceta-hyde\) \/ 1\)/)
    expect(estilo).not.toContain('background')
    expect(enviar.className).toContain('bg-superficie')
    expect(enviar.className).not.toContain('text-fondo')
  })

  it('sigue a la faceta elegida', () => {
    renderBar()
    fireEvent.click(screen.getByRole('button', { name: /jekyll/i }))
    const estilo = screen.getByRole('button', { name: 'Enviar' }).getAttribute('style') || ''
    expect(estilo).toContain('rgb(var(--faceta-jekyll) / 1)')
    expect(estilo).not.toContain('background')
  })

  it('la faceta elegida pinta borde y texto con su token, sin fondo de color', () => {
    renderBar()
    const boton = screen.getByRole('button', { name: /hyde/i })
    const estilo = boton.getAttribute('style') || ''
    expect(estilo).toContain('border-color: rgb(var(--faceta-hyde) / 1)')
    expect(estilo).not.toContain('background')
    expect(boton.className).toContain('bg-superficie')
  })
})

const POLITICA = {
  max_bytes: 1048576, max_chars: 8000, max_por_mensaje: 1,
  mimes_de_imagen: ['image/png', 'image/jpeg', 'image/webp'],
  accept: ['image/png', 'image/jpeg', 'image/webp', 'application/pdf', '.md'],
  facetas_con_imagen: ['hipatia'],
}
// RD4 (2026-09-17): /api/chat/upload responde por referencia -- id, tipo,
// nombre, bytes -- nunca base64 ni el texto completo.
const SUBIDA_IMAGEN = { id: 'img-id-1', tipo: 'imagen', nombre: 'f.png', mime: 'image/png', bytes: 3 }
const SUBIDA_IMAGEN_2 = { id: 'img-id-2', tipo: 'imagen', nombre: 'g.png', mime: 'image/png', bytes: 3 }

function adjuntar(container, archivo) {
  const input = container.querySelector('input[type="file"]')
  fireEvent.change(input, { target: { files: [archivo] } })
}

describe('BottomBar -- adjuntos cableados (frente D)', () => {
  let toast
  beforeEach(() => {
    toast = vi.fn()
    useJaxStore.setState({ activeFacet: 'hipatia', messages: [], addToast: toast })
    api.get.mockImplementation((url) => Promise.resolve({ data: url === '/chat/adjuntos' ? POLITICA : {} }))
    api.post.mockReset()
    // RD4: previewUrl/vistaDeAdjunto usan URL.createObjectURL/revokeObjectURL
    // (jsdom no las implementa). Cada llamada devuelve un blob distinto y
    // numerado para poder distinguir el del compositor del del mensaje.
    let n = 0
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => `blob:u${++n}`), revokeObjectURL: vi.fn() })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('A-21: sube con FormData y sin Content-Type a mano', async () => {
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toContain('application/pdf'))
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const [url, cuerpo, opciones] = api.post.mock.calls[0]
    expect(url).toBe('/chat/upload')
    expect(cuerpo).toBeInstanceOf(FormData)
    expect(opciones).toBeUndefined()
  })

  it('un 415 del upload se muestra traducido', async () => {
    api.post.mockRejectedValueOnce({ response: { data: { detail: { code: 'adjunto_tipo_no_permitido' } } } })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['x'], 'x.exe'))
    await waitFor(() => expect(toast).toHaveBeenCalledWith({ message: es.erroresMesa.adjunto_tipo_no_permitido(), type: 'error' }))
  })

  it('manda adjuntos: [{id}] en el cuerpo del chat (RD4, nunca bytes ni base64)', async () => {
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN })
      .mockResolvedValueOnce({ data: { facet: 'hipatia', response: 'ok', timestamp: 't' } })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    await screen.findByAltText('f.png')
    fireEvent.change(container.querySelector('textarea'), { target: { value: 'describí' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
    expect(api.post.mock.calls[1]).toEqual(['/chat', {
      message: 'describí', facet: 'hipatia', origin: 'web',
      adjuntos: [{ id: 'img-id-1' }],
    }])
  })

  it('la vista previa de la imagen subida usa un object URL local del compositor', async () => {
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    const img = await screen.findByAltText('f.png')
    expect(img).toHaveAttribute('src', 'blob:u1')
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
  })

  it('reemplazar el adjunto revoca el object URL viejo y crea uno nuevo', async () => {
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN }).mockResolvedValueOnce({ data: SUBIDA_IMAGEN_2 })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    await screen.findByAltText('f.png')
    adjuntar(container, new File(['def'], 'g.png', { type: 'image/png' }))
    const img = await screen.findByAltText('g.png')
    expect(img).toHaveAttribute('src', 'blob:u2')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:u1')
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith('blob:u2')
  })

  it('quitar el adjunto revoca su object URL', async () => {
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    await screen.findByAltText('f.png')
    fireEvent.click(screen.getByTitle(es.attachRemove))
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:u1')
    expect(screen.queryByAltText('f.png')).not.toBeInTheDocument()
  })

  it('salir del modo chat revoca el object URL del adjunto sin enviar', async () => {
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    await screen.findByAltText('f.png')
    fireEvent.click(screen.getByRole('button', { name: es.modeComando }))
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:u1')
    expect(screen.queryByAltText('f.png')).not.toBeInTheDocument()
  })

  it('desmontar con un adjunto sin enviar revoca su object URL', async () => {
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN })
    const { container, unmount } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    await screen.findByAltText('f.png')
    unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:u1')
  })

  it('una subida que termina después de salir del modo chat no adjunta, y revoca su object URL (minor RD2)', async () => {
    let resolverSubida
    api.post.mockImplementationOnce(() => new Promise((r) => { resolverSubida = r }))
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    // Sale del chat MIENTRAS la subida sigue en vuelo.
    fireEvent.click(screen.getByRole('button', { name: es.modeComando }))
    resolverSubida({ data: SUBIDA_IMAGEN })
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:u1'))
    expect(screen.queryByAltText('f.png')).not.toBeInTheDocument()
  })

  it('enviar: el compositor revoca su object URL; el mensaje se queda con uno propio', async () => {
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN })
      .mockResolvedValueOnce({ data: { facet: 'hipatia', response: 'ok', timestamp: 't' } })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    await screen.findByAltText('f.png')
    fireEvent.change(container.querySelector('textarea'), { target: { value: 'describí' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
    const mensajeUsuario = useJaxStore.getState().messages[0]
    expect(mensajeUsuario.attachment.base64).toBe('blob:u2')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:u1')
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith('blob:u2')
  })

  it('un 404 adjunto_no_encontrado en el chat limpia el adjunto para poder re-adjuntar', async () => {
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN })
      .mockRejectedValueOnce({ response: { data: { detail: { code: 'adjunto_no_encontrado' } } } })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    await screen.findByAltText('f.png')
    fireEvent.change(container.querySelector('textarea'), { target: { value: 'describí' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }))
    await waitFor(() => {
      const ultimo = useJaxStore.getState().messages.at(-1)
      expect(ultimo.content).toContain(es.erroresMesa.adjunto_no_encontrado())
    })
    expect(screen.queryByAltText('f.png')).not.toBeInTheDocument()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:u1')
  })

  it('imagen con una faceta que no ve imágenes: aviso y Enviar deshabilitado', async () => {
    useJaxStore.setState({ activeFacet: 'jekyll' })
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    const aviso = await screen.findByRole('status')
    expect(aviso.textContent).toMatch(/no acepta imágenes/)
    fireEvent.change(container.querySelector('textarea'), { target: { value: 'describí' } })
    expect(screen.getByRole('button', { name: 'Enviar' })).toBeDisabled()

    // Fix round 1: Enter en el textarea llama a handleSend() directo, sin
    // pasar por el disabled del botón -- tiene que frenar igual.
    fireEvent.keyDown(container.querySelector('textarea'), { key: 'Enter' })
    expect(api.post).toHaveBeenCalledTimes(1) // solo la subida, nunca /chat
    expect(useJaxStore.getState().messages).toHaveLength(0)
  })

  it('sin política no se puede adjuntar', async () => {
    api.get.mockImplementation((url) => (url === '/chat/adjuntos' ? Promise.reject(new Error('caído')) : Promise.resolve({ data: {} })))
    const { container } = renderBar()
    await waitFor(() => expect(screen.getByTitle(es.adjuntoPoliticaNoDisponible)).toBeDisabled())
    expect(container.querySelector('input[type="file"]')).toBeDisabled()
  })

  it('un 422 imagen_no_soportada del chat se muestra traducido', async () => {
    useJaxStore.setState({ activeFacet: 'hipatia' })
    api.post.mockResolvedValueOnce({ data: SUBIDA_IMAGEN })
      .mockRejectedValueOnce({ response: { data: { detail: { code: 'imagen_no_soportada', facet: 'hipatia' } } } })
    const { container } = renderBar()
    await waitFor(() => expect(container.querySelector('input[type="file"]').getAttribute('accept')).toBeTruthy())
    adjuntar(container, new File(['abc'], 'f.png', { type: 'image/png' }))
    await screen.findByAltText('f.png')
    fireEvent.change(container.querySelector('textarea'), { target: { value: 'describí' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }))
    await waitFor(() => {
      const ultimo = useJaxStore.getState().messages.at(-1)
      expect(ultimo.content).toContain(es.erroresMesa.imagen_no_soportada())
    })
  })
})
