# Sub-tarea A — Reporte: Zustand selectores + React.memo

## 1. Reconocimiento (alcance real, 2026-07-18)

Confirmado con grep sobre `frontend/src/components` y `frontend/src/pages`:

- **10 call sites** de `useJaxStore()` — **el 100% de los call sites** destructuraban el store completo (`const { a, b } = useJaxStore()`), cero usaban un selector (`useJaxStore(state => ...)`).
- **0 componentes** usaban `React.memo` en todo `frontend/src/components` y `frontend/src/pages`.
- No existe tooling de conteo de re-renders (no react-scan, no profiler harness, no test de renders) — confirmado, coincide con lo que decía el brief. Único test frontend existente: `frontend/src/store/useJaxStore.test.js` (3 tests, sobre la lógica del store, no sobre componentes).
- `zustand` es v5.0.14 (ya en package.json). `useShallow` (`zustand/react/shallow`) ya viene incluido en el paquete `zustand` — **no es una dependencia nueva**, no hizo falta instalar nada ni preguntar. Terminé sin necesitarlo: en todos los casos alcanzó con selectores primitivos individuales (una llamada a `useJaxStore` por campo), que es la forma más segura de evitar el "selector-de-objeto-nuevo" (ver sección de riesgos).

Los 10 call sites (todos corregidos):
`Toast.jsx`, `StepCard.jsx`, `KillSwitch.jsx`, `RightPanel.jsx`, `Login.jsx`, `LeftPanel.jsx`, `BottomBar.jsx`, `HalEye.jsx`, `Dashboard.jsx`, `CenterPanel.jsx`.

Conclusión: el hallazgo original (0 selectores, 0 memo) seguía siendo 100% exacto hoy, pese a los cambios de facet_models/WS isolation posteriores al audit del 2026-07-08.

## 2. Cambios

### 2a. Selectores Zustand (13 archivos)

En cada uno de los 10 call sites reemplacé la destructuración del store completo por **una llamada `useJaxStore(state => state.campo)` por campo leído** (no un solo selector que arma un objeto nuevo — ese patrón crea una referencia nueva en cada render y anula el propósito, ver sección de riesgos más abajo). Las funciones de acción (`addMessage`, `activateKillSwitch`, etc.) también se seleccionan individualmente: son referencias estables (definidas una sola vez en `create()`, nunca reasignadas por `set()`), así que seleccionarlas nunca dispara un re-render — pero antes del fix, estar dentro de un `useJaxStore()` sin selector las hacía re-renderizar el componente en cada `set()` de cualquier campo del store igual.

Casos más significativos:
- **`BottomBar.jsx`**: 8 campos en un solo `useJaxStore()` → 8 selectores individuales. Antes, tipear en el textarea (estado local `input`) ya causaba que el componente completo re-renderizara en cada `set()` de `messages`, `facets`, `activePipelines`, `toasts`, etc., aunque BottomBar sólo usa `wsStatus`/`activeFacet` como state y el resto son acciones.
- **`HalEye.jsx`**: 5 campos (`facets`, `activePipelines`, `lasManos`, `killSwitchActive`, `generatingImage`) — este componente sí necesita casi todo el store para calcular `getEyeState()`, pero antes también se re-renderizaba en cada cambio de `messages`/`toasts`/`wsStatus`/`activeFacet` (que no usa).
- **`CenterPanel.jsx`**: sólo necesita `messages`; antes re-renderizaba también con cada cambio de `facets` (status "thinking" cambia constantemente durante una respuesta) y `activePipelines`.

### 2b. `React.memo` (11 componentes)

Apliqué `React.memo` con criterio, en dos categorías, evitando sobre-aplicarlo a componentes baratos o que raramente re-renderizan:

**Ítems de lista (referencias de props estables entre updates no relacionados):**
- `FacetCard.jsx` — el store reemplaza sólo el objeto de la faceta que cambió (`facets: {...s.facets, [payload.facet]: {...}}`), las demás facetas mantienen la misma referencia. Memo evita re-renderizar 6 de 7 `FacetCard` en cada `facet_status_changed`.
- `Message.jsx` — `updateMessage`/`addMessage` preservan la referencia de los mensajes no tocados (`.map` con `: m` en la rama sin cambios). Memo evita re-renderizar todo el historial de chat cuando llega un mensaje nuevo o uno existente cambia de estado.
- `StepCard.jsx` — defensa adicional: `RightPanel` tiene estado local (`cancelling`) que, al cambiar, re-renderiza todo el árbol; si las props de un `StepCard` no cambiaron en ese ciclo, memo evita el re-render.

**Hijos sin props de `Dashboard`/paneles con overhead de renderizado evitable:**
- `Toast.jsx`, `LeftPanel.jsx`, `CenterPanel.jsx`, `RightPanel.jsx`, `BottomBar.jsx`, `HalEye.jsx`, `KillSwitch.jsx`, `AuditLog.jsx`.
  Motivo verificado leyendo `Dashboard.jsx` y `useTheme.js`: el toggle de tema (`toggleTheme`) es estado local de `Dashboard`, no contexto ni store. Sin memo, cada toggle de tema o de idioma re-renderiza `Dashboard` y, en cascada, **todos sus hijos sin props** (`LeftPanel`, `CenterPanel`, `RightPanel`, `BottomBar`, `Toast`) aunque ninguno de ellos lea `theme`. Confirmé que esto no rompe el cambio de idioma: `useI18n()` usa React Context (`I18nContext.Provider` en `i18n/index.jsx`), y el consumo de contexto dispara re-render del componente igual, sin importar `memo` (memo sólo bloquea re-renders forzados por el padre con las mismas props; no bloquea los disparados por el propio hook de contexto o por el propio store). `KillSwitch` recibe el mismo tratamiento porque `BottomBar` re-renderiza en cada tecleo (`setInput`) y `KillSwitch` no depende de ese estado. `AuditLog` no toca el store en absoluto — se beneficia porque `RightPanel` re-renderiza seguido por cambios de `activePipelines`/`cancelling` y `AuditLog` no tiene props propias.

**No memoicé** (por juicio, para no sobre-aplicar): `Login.jsx`, `Dashboard.jsx` (páginas de ruta, instancia única, no son hijas de nada que re-renderice con props estables), y el `ProgressBar` inline de `RightPanel.jsx` (función local, barata, no es un ítem de lista).

## 3. Riesgo evitado: selector-de-objeto-nuevo

El brief advertía sobre `useJaxStore(state => ({ a: state.a, b: state.b }))` — crea un objeto nuevo en cada llamada, y con comparación por referencia (default de Zustand) eso dispara MÁS re-renders que no usar selector. Evité el patrón completo: en **todos** los 10 call sites usé una llamada de hook por campo (`const a = useJaxStore(s => s.a); const b = useJaxStore(s => s.b)`), que selecciona un primitivo/referencia por llamada — cero objetos nuevos, cero necesidad de `useShallow`.

## 4. Verificación de la mejora (método + evidencia)

No hay tooling de conteo de re-renders en el repo (confirmado en la sección 1), así que seguí la alternativa que planteaba el brief: un contador de renders temporal, con test de vitest + React Testing Library (ambos ya son devDependencies — no instalé nada nuevo).

Archivo temporal `frontend/src/store/_tmp-rendercount.test.jsx` (creado, corrido, **borrado antes de commitear** — no forma parte del diff final):

1. **Prueba conceptual (componentes de juguete)**: un componente que hace `useJaxStore()` completo vs uno que hace `useJaxStore(s => s.wsStatus)`. Al mutar un campo no relacionado (`toasts`, vía `addToast()`), el componente sin selector re-renderizó (contador 1→2) y el componente con selector no (se mantuvo en 1). Al mutar el campo que ambos sí leen (`wsStatus`, vía `setWsStatus()`), ambos re-renderizaron.
2. **Prueba sobre el componente real ya corregido** (`KillSwitch.jsx`, memo + selectores): montado dentro de `I18nProvider` y envuelto en `<Profiler onRender={...}>` para contar commits reales. Al disparar `addMessage()` (mutación de `messages`, que `KillSwitch` nunca lee), el Profiler registró **0 commits adicionales** — antes del fix, con `useJaxStore()` sin selector, este mismo evento habría re-renderizado `KillSwitch` (mismo mecanismo que la prueba 1). Al disparar `activateKillSwitch()` (el campo que sí lee), el Profiler sí registró un commit adicional — confirmando que el componente sigue reaccionando correctamente a lo que le importa.

Resultado: 3/3 tests temporales pasaron. Evidencia reproducible: cualquiera puede recrear el mismo archivo (contenido documentado arriba, mecánica simple: contador de renders + `Profiler.onRender` + `act()` disparando acciones del store) para volver a verificar.

## 5. Tests

- `npm run test -- --run` (vitest, suite existente): **3/3 passed**, antes y después del fix — sin regresiones funcionales.
- `npm run build` (vite build): compila sin errores, 291 módulos transformados.
- No se agregó, modificó ni removió ningún test permanente — la suite existente (`useJaxStore.test.js`) no toca componentes, así que no había nada que pudiera romperse por el cambio de selectores en sí; el build de producción es la verificación de que no quedó JSX/import roto.

## 6. Auto-revisión

- ¿Confirmé el alcance real (no confié ciegamente en el audit)? Sí — grep + lectura de los 13 archivos antes de tocar nada; el hallazgo de 0 selectores/0 memo se sostuvo.
- ¿Evité el patrón de selector-objeto-nuevo? Sí — un hook call por campo en todos los casos, cero `useShallow` (no hizo falta).
- ¿Toqué sólo lo necesario? Sí — 13 archivos, todos parte del alcance (los 10 call sites + los 2 componentes de lista que no eran call sites pero sí candidatos claros a memo: `FacetCard.jsx`, `Message.jsx` — más `AuditLog.jsx` por el mismo criterio de "hijo sin props que re-renderiza de más"). No refactoricé nada más (dejé `wsStatus` sin usar en `BottomBar.jsx` tal cual estaba — ya era una destructuración muerta antes de mi cambio, no es un drive-by fix).
- ¿Tests pasando? Sí, 3/3, antes y después, más `npm run build` limpio.
- ¿Verificación de re-render documentada y reproducible? Sí, sección 4 — método + resultado numérico + cómo se puede recrear.

## 7. Preocupaciones

- No pude medir el "% de re-renders innecesarios" en producción real (requeriría React DevTools Profiler contra la app corriendo en el navegador, fuera de alcance de este entorno de agente sin sesión de browser activa). La evidencia que junté es sobre el mecanismo (selector vs no-selector, memo vs no-memo) aplicado directamente al código real, no sobre una sesión de usuario en vivo — considero que es evidencia suficiente del efecto, pero no es un "80% → X%" medido end-to-end.
- No toqué `frontend/node_modules/.package-lock.json` (aparecía modificado en el git status inicial, previo a mi trabajo) — lo dejé como estaba, no es parte de este sub-tarea.
