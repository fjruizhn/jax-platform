# Contribuir a Axioma Platform

Mismas reglas de fondo que el repo hermano `jax` -- éstas son las que ya
se aplican al propio trabajo del mantenedor, no un estándar nuevo para
terceros.

## Reglas de la casa

**No suponer, verificar contra el sistema real.** Un PR que dice "esto
debería andar" sin evidencia de que corrió contra el backend/frontend
real no está terminado. Esto incluye UI: si tocás un componente, probalo
en el navegador, no solo en el linter.

**i18n, cero excepciones.** Ningún string visible al usuario va
hardcodeado en un componente -- todo vive en `frontend/src/i18n/{es,en}.js`.
Un PR con un string nuevo sin su entrada de traducción no se acepta.

**Dark/light mode, siempre.** Todo componente nuevo respeta el tema
activo -- colores vía variables CSS/tokens, nunca un valor hardcodeado.
Probado en ambos modos antes de pedir review.

**P10 -- ningún camino de error termina en éxito reportado.** El repo
`jax` corre un scanner de CI que busca `except: pass` sin marcar; el mismo
criterio aplica acá aunque el scanner viva en el otro repo. Un `except`
que silencia un error real (sin loguear, sin propagar) no se acepta salvo
que esté marcado `# fail-soft: <razón concreta>`.

**Tests obligatorios.** Backend: contra una base de datos de prueba real
cuando el comportamiento depende de la integración, no todo mockeado.
Frontend: al menos verificación manual documentada en el PR (qué se probó,
en qué navegador/modo) si no hay test automatizado para ese componente.

## Qué esperamos de un PR

- Rama dedicada, commits que expliquen el *por qué*.
- CI verde.
- Si tocás el flujo de aprobación de cambio de modelo (`facet_binding`) o
  cualquier ruta que hoy exige superadmin, decilo explícito en la
  descripción -- es una de las pocas rutas del sistema con esa exigencia
  a propósito, y un PR que la relaje necesita esa conversación primero.

## Qué NO esperamos

No hace falta cobertura perfecta ni que el PR resuelva toda la deuda
relacionada que encuentres en el camino -- señalala en la descripción,
no la escondas ni la arregles a medias dentro de un PR que no era sobre
eso.
