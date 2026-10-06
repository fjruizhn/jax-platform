# E3 · respaldo de PDF escaneado en el chat

**Fecha:** 2026-10-06
**Estado:** ronda 3 implementada en la rama `feat/e3-respaldo-chat`; **sin auditoría aprobada**: la auditoría de escalón 3 sobre `b209f7de` dio **APROBADO CON CAMBIOS** (2 MAJOR, 5 MINOR; ningún BLOCK) y esta ronda los cierra, pero el SHA final exige una auditoría nueva, en otra invocación, antes de integrar. **CI del SHA final: [PENDIENTE: run de policy del SHA final; lo anota quien integre -- no se midió aquí].** La medición real de LAS MANOS queda pendiente para la ventana indicada abajo. El reporte de carga más antiguo se conserva como historia, pero su prueba fue declarada inválida para medir E3 por la auditoría: medía solo latencia de chat con un PDF pequeño ya en proceso, sin cronometrar subidas ni un estado terminal. No acredita el comportamiento de subida, despacho y sondeo hasta finalización.

## Corrección del registro (2026-10-06)

La medición histórica de 721.1 ms del POST de chat no corresponde al criterio E3. La prueba vigente (ronda 3, `test_carga_e3_20_usuarios_chatean_mientras_se_procesan_sus_pdf`) mide **por separado**, con latencia **por petición** y no con el tiempo de pared del lote, el p95 de cada subida (`POST /api/chat/upload`, 20 PDF escaneados al máximo de páginas y bytes) y el p95 de cada turno de chat (5 por usuario), mientras el hilo principal corre el despacho (`despachador.ciclo`) y el sondeo reales y consulta el estado de cada documento; las llegadas son escalonadas y el OCR simulado tarda 3 s, de modo que hay chats mientras se procesa (la prueba lo exige: >= 90 % de los turnos con documentos abiertos y >= 3 vueltas de despacho durante los chats). Un segundo paso del workflow inyecta `time.sleep(0.5)` bloqueante en `encolar_pdf_desde_chat` y solo se considera una demostración válida si el fallo es la aserción del tope de p95 (`E3 subida p95` o `E3 chat p95`).

El primer resultado normal de CI del SHA `18c4364` (policy run 37484684852) fue `p95=max=1891.9 ms`. Su corrida mutante no fue válida: el `sleep(3)` al inicio de `encolar_pdf_desde_chat` llenó el pool OCR de un worker y devolvió `503 adjuntos_reintentar` antes de medir latencia. Por ello, el paso mutante fija solo para esa corrida 8 workers (máximo permitido) y 60 s de timeout (máximo permitido), para separar bloqueo del event loop de saturación OCR.

**Histórico (SHA `65b9449`, prueba ANTERIOR; no es evidencia del SHA final):** policy run [37491432941](https://github.com/fjruizhn/jax-platform/actions/runs/37491432941) midió `p95=1308.5 ms`, `max=1309.7 ms`, que era el tiempo de pared del lote completo (p95 y máximo difieren en menos de 1 ms), con tope 25000 ms; el auditor de `b209f7de` demostró que ese tope dejaba pasar un `time.sleep(0.5)` en la ruta (p95 1132 -> 11152 ms). El run **37492767675** corrió sobre `88e4a52` y el **37494514448** sobre `b209f7de`; ninguno es del SHA final de esta ronda.

## Cierre de la auditoría sobre `b209f7de` (ronda 3, 2026-10-06)

**Cómo se midió (local, hall9000, no CI):** MariaDB `mariadb:12.3.3` efímera propia (nombre único, `--rm`; nunca el `3308` ni `jax_memory`), con las pruebas dentro del espacio de red de su contenedor (así LAS MANOS real, `7777`, y Ollama, `11434`, son inalcanzables por construcción), venv de pruebas con los extractores y el clon de jax en el pin de CI `0604754`. El OCR real no se mide aquí.

**Línea base de la prueba de carga (6 corridas sin mutante, 20 usuarios x 5 turnos, 20 PDF de 20 páginas y 10 MiB):**

| Medida | Rango | Peor corrida | Tope | Margen declarado |
|---|---:|---:|---:|---|
| p95 de subida | 35-59 ms | 59,4 ms | 1500 ms | x25: la base es de decenas de ms y manda el ruido absoluto del runner; sigue 6x por debajo del mutante |
| p95 de turno de chat | 1021-1255 ms | 1255 ms | 4000 ms | x3: la base ya es de cola (100 turnos en ~2 s sobre un loop) |

Mutante `time.sleep(0.5)` en `encolar_pdf_desde_chat`, con el entorno del paso de CI (8 workers, 60 s): **subida p95 9828 / 9918 / 10130 ms** (tope 1500) y chat p95 3908 / 3713 / 2132 ms en tres corridas; la prueba queda en rojo siempre por `E3 subida p95`. El tope de chat solo lo ve a veces (el bloqueo no está en su ruta): protege las regresiones de la ruta del chat. **La prueba ANTERIOR (`b209f7de`) con el mismo mutante a 0,5 s pasó** (p95 11062 ms contra tope 25000): ese era el defecto. Sin los 8 workers el mutante da `503 adjuntos_reintentar` antes de medir, por eso el paso de CI los fija y el grep exige el mensaje del tope.

| Hallazgo | Cierre | Evidencia local |
|---|---|---|
| MAJOR 1 · la carga no medía «chatear mientras se procesa» ni detectaba 0,5 s | Prueba reescrita (arriba): despacho y sondeo intercalados con los chats, p95 por petición de subida y de chat por separado, topes = base medida x margen declarado; el paso mutante de `policy.yml` pasa a `sleep(0.5)` y a ambos topes | Base y mutante en la tabla de arriba |
| MAJOR 2 · la prueba del reintento fallaba `GET /proyectos`, no el sondeo | Las pruebas de sondeo usan reloj falso y mocks **por URL** (`/proyectos/7/documentos/42` falla; la lista del selector responde aparte): esperas exactas 10 y 20 ms, tope de espera en 40 ms, tope total, y consulta que nunca vuelve | Mutantes F3 (catch -> agotar), F4 (backoff constante), F4b (sin tope de espera) y F5 (sin `topeTimer`): las pruebas nuevas fallan en cada uno; la prueba vieja los dejaba pasar a los tres (26/26 verdes) |
| MINOR 3 · `npm test` fallaba en local | `VITE_CHAT_PDF_POLL_*` pasan a `frontend/vitest.config.js` (`test.env`: 10, 40, 400 ms) y se quitan del paso de CI: iguales en local y en CI | `npx vitest run` sin variables: 1410/1410; la prueba vieja sin esa configuración fallaba (5003 ms de espera) |
| MINOR 4 · F7, F10, F9 sin prueba y falta `estados.desconocido` | Pruebas de `FileAttachment` (sin «listo» en `en_cola`/`pendiente`/`procesando`, estado traducido, «desconocido» traducido) y de `BottomBar` (`project_id` en el formData); `estados.desconocido` en es y en | F7, F10 y la clave faltante: rojo contra el mutante; F9: rojo sin el `append` |
| MINOR 5 · M9, M11, M7 sin prueba | Un VIEWER no lee un documento oculto (404 aun para el dueño); duplicado desde el chat devuelve el documento canónico con su estado actual y el oculto da 409 sin revelar el id; `DOC_MAX_BYTES_ARCHIVO` menor que el tope del chat da 413 con el tope de proyectos | M9 (sin `AND oculto_at IS NULL`), M11 (sin camino de reuso, estado fijo, sin id del duplicado) y M7 (sin chequeo de bytes en `encolar`, sin el `min()` en `upload.py`): rojo en cada uno |
| MINOR 6 · `encolar_pdf_desde_chat` no tomaba `cupo_de_subidas` | Toma el cupo de subidas simultáneas antes de tocar el disco y lo suelta en `finally` (429 `subidas_simultaneas`, ya traducido); pruebas con N=1 por usuario y global, con dos subidas realmente simultáneas | Contra `b209f7de` (sin cupo) las dos pruebas fallan (200 en vez de 429); sin el `soltar` fallan por cupo no liberado |
| MINOR 7 · este informe | Se quitó «Auditoría escalón 3: APROBADO»; «Contexto fijado» corregido; la evidencia de CI del SHA final queda marcada como pendiente | Este documento |

**Efecto de producción a notar:** con los valores sembrados (`subidas_por_usuario=2`, `subidas_globales=4`), una ráfaga de PDF escaneados por el chat ahora puede recibir 429 `subidas_simultaneas` (el área de proyectos ya se comportaba así). La prueba de carga sube el cupo al máximo con `ajustes_en_db` porque mide la latencia del loop, no el rechazo del cupo.

**Pisos re-medidos (local; no medidos en el runner de CI):** frontend **1410/0/1410** (113 archivos; antes 1397); backend sin DB **2364 passed / 1491 skipped** (antes 2364 / 1485: las +6 nuevas piden `client`); backend con DB **3854 passed / 1 skipped** (antes, mismo método sobre `b209f7de`: 3848 / 1; CI midió 3847 / 2, la diferencia de 1 es una prueba que el runner omite por entorno), así que el piso con DB se fija en 3853. Los topes de `policy.yml` son 1410, 2364 y 3853; lo re-mide quien integre (regla 5).

**Pendiente de esta ronda:** (1) auditoría de escalón 3 del SHA final, en otra invocación; (2) policy run del SHA final: **[PENDIENTE: URL del run y resultado de los pasos `Medir E3...` y `Comprobar que E3 rechaza el mutante sleep(0.5)`]**; (3) integración por Fernando, porque el PR toca `.github/workflows/policy.yml`.

## Cierre del informe de auditoría de la ronda 2 (histórico, SHA `65b9449`)

| Hallazgo | Cierre | Evidencia en el SHA `65b9449` |
|---|---|---|
| Major · E3 no medía upload→terminal ni detectaba `sleep(3)` | 20 subidas válidas al máximo de páginas/bytes, chat, despacho durable y sondeo terminal; p95 con tope de CI y mutante ejecutado en configuración OCR acotada válida | Policy run 37491432941: `p95=1308.5 ms`, `max=1309.7 ms`; mutante falló por p95 a `61448.8 ms` sobre `25000 ms`; wrapper del mutante pasó al verificar la causa exacta |
| Minor 1 · códigos internos OCR visibles | Causa y estado se resuelven por catálogos i18n; código desconocido usa causa genérica | Tests `FileAttachment`; frontend `1397/1397` |
| Minor 2 · sondeo sin reintentos ni límite | Reintento ante error transitorio, backoff creciente y tope total configurable con estado honesto al agotar | Tests `BottomBar`; frontend `1397/1397` |
| Minor 3 · selector bloqueado tras estado terminal | Se desbloquea en estados finales y muestra instrucción localizada | Tests `BottomBar` y `FileAttachment`; frontend `1397/1397` |
| Minor 4 · el freno no se revalidaba en la entrada nueva | Se revisa antes de autorización, carpeta, escritura e INSERT. Si para un lote después de aceptar archivos, el 423 incluye el resultado parcial; al fallar pre-INSERT elimina solo el archivo no registrado | Job DB `3847 passed`; dos escenarios parametrizados verifican una sola fila/archivo y el resultado parcial |
| Minor 5 · faltaban denegaciones de autorización del nuevo punto de entrada | Pruebas de otro tenant, sin membresía, VIEWER, archivado e inexistente; espían autorización de escritura/estado activo y confirman que no se abre carpeta ni se transmite archivo | Cinco casos parametrizados pasaron en job DB; `3847 passed, 2 skipped, 0 failed` |

Los floors medidos quedan en frontend 1397, backend no-DB 2364 y backend con DB 3847; no se redujeron. La medición E3 representa el runner efímero de CI con el borde HTTP de LAS MANOS simulado; no representa OCR real ni una medición de producción.

(Ronda 2, histórico; en la ronda 3 sí se ejecutaron localmente con una MariaDB efímera propia en su netns.) La integración E3 y los cinco casos DB de autorización todavía no se ejecutaban localmente: en Hall9000 pytest cerró antes de conectarse porque `3308` está protegido como puerto de producción, Docker no permite usar el daemon y no hay `mariadbd` local. El job `backend-tests-con-db` usa MariaDB efímera y quedó como ejecutor de esos casos. No se habilitó `JAX_TEST_DB_PERMITIR_INSTANCIA_DE_PRODUCCION` y no se consultó ni modificó `jax_memory`.

Verificaciones de esta rama en Hall9000: frontend `1397 passed` (113 archivos); unidad backend de estado/freno `3 passed`; el PDF de muestra se parseó a 20 páginas y 10 MiB y produjo `PdfSinTexto`. La suite backend completa sin DB terminó `2362 passed, 1482 skipped, 2 failed`; los dos fallos son `test_processing_status_is_resolved_only_by_paired_jax_singleton` y `test_processing_status_without_authoritative_jsonl_is_unavailable`, porque el checkout local de JAX no contiene `procesamiento_routes`. CI clona el pin JAX exacto de su workflow. Esos fallos no son evidencia de resultado para el job CI.

El OCR real de LAS MANOS no puede medirse en el job CI, que simula solo el borde HTTP remoto. **Pendiente para 2026-10-07; responsable: Fernando asigna operador y ventana de carga en el host LAS MANOS antes del GO de producción.** Registrar allí configuración efectiva, concurrencia, peticiones/s, p95 y máximo upload→terminal, latencia de chat y uso de CPU/memoria. Hasta que se mida, no se declara medido ni listo para GO de producción.

## Intento y resultado (histórico, no acredita E3)

La carga solicitada se ejecutó con 20 usuarios enviando turnos de chat mientras un PDF escaneado estaba admitido en la cola durable y el sondeo del dispatcher recibía `running`. La base MariaDB, las membresías, la admisión, la transición durable, el dispatcher y `/api/chat` fueron reales dentro del job de CI; LAS MANOS respondió con un cliente HTTP simulado que mantuvo el estado en proceso. El proveedor de chat simulado esperó 30 ms por petición. No se usó producción ni se simula que OCR terminó.

El intento local detectó `JAX_DB_PORT=3308`, puerto de la instancia de producción, y rechazó crear una base de pruebas (`base_de_test.py::exigir_conexion_permitida`). No se habilitó `JAX_TEST_DB_PERMITIR_INSTANCIA_DE_PRODUCCION`. Docker está instalado, pero su daemon devuelve `permission denied` en `/var/run/docker.sock`; tampoco hay `mariadbd`/`mysqld` instalado para iniciar una instancia desechable. La prueba se trasladó al job `backend-tests-con-db`, que levanta MariaDB efímera `mariadb:12.3.3`.

| Medida | Resultado |
|---|---:|
| Usuarios concurrentes de chat | 20 |
| Solicitudes de chat completadas | 20/20 HTTP 200 |
| PDF escaneado | Admitido y marcado `pendiente`; dispatcher activo y consulta retenida en `running` |
| p95 del POST `/api/chat` | 721.1 ms |
| Máximo del POST `/api/chat` | 769.4 ms |
| Base de datos de producción `jax_memory` usada | No |

Medición: GitHub Actions run [37454479696](https://github.com/fjruizhn/jax-platform/actions/runs/37454479696), SHA `30989bf5184bbb1573170c49d317ee16f2cd1281`; el paso focal `Medir E3 con 20 usuarios y escaneo en proceso` imprime el resultado. El job completo reportó 3838 pasadas y 2 omitidas. Esta es una prueba concurrente de extremo a extremo del backend bajo un proveedor de chat con demora fija, no una carga sostenida ni una medición de OCR real.

## Lecturas verificadas

- La ruta de subida encola el original en `project_documents` y avisa al despachador sin esperar al trabajo de LAS MANOS.
- La cola y su sondeo HTTP del procesador corren en el despachador existente; este cambio no llama OCR dentro del turno de chat.
- Las pruebas sin DB no demuestran latencia ni integración real del despachador.
- Verificación local completa sin DB: backend `2363 passed, 1479 skipped`; frontend `1393 passed, 0 failed`. Los tests que abren MariaDB se omitieron por `JAX_CI_NO_DB=1`.
- Delta de piso medido contra `origin/master d283d90`: backend `+5` pasadas (dos casos de upload sin DB, dos lecturas unitarias de estado y una comprobación del sufijo PDF); frontend `1389→1393` (`+4`: estado OCR, turno sin contenido OCR, traducción de autorización y selector bloqueado para conservar el proyecto). El test `FileAttachment` preexistente ya estaba versionado en `origin/master`.

## Pendientes vigentes

1. Medir OCR real de LAS MANOS según la ventana y responsable indicados arriba. La evidencia de CI no reemplaza esta medición.

## Registro de trabajo y traspaso

**Objetivo:** registrar el PDF escaneado del chat en el proyecto autenticado y entregar respuesta sin esperar el OCR; mostrar después el estado verdadero del procesador.

**Hecho (2026-10-06):** `PdfSinTexto` ahora lleva el total de páginas. `/api/chat/upload` exige `project_id` para el escaneado, vuelve a validar membresía/papel de escritura y proyecto activo, limita bytes/páginas con la configuración existente, y reutiliza el guardado durable, deduplicación y dispatcher de `project_documents`. El nuevo GET de estado comprueba visibilidad del proyecto y oculta documentos ocultos/ajenos con 404. BottomBar sondea el estado y la tarjeta usa i18n es/en y `aria-live`. No se envía el PDF al modelo ni se presenta como leído: E2b no está implementado.

**Decisión técnica:** se reutiliza el dispatcher existente, que ya llama el API vigente de LAS MANOS; no se crea una tarea efímera en FastAPI ni se espera OCR dentro de la petición. La suba de chat no crea un adjunto de texto que el modelo pudiera confundir con extracción real. La decisión procede del alcance E3 del encargo, no de una nueva decisión de negocio.

**Contexto fijado:** al iniciar una subida se captura el proyecto autenticado seleccionado. El selector queda deshabilitado durante la subida y mientras el documento siga en `en_cola`, `pendiente` o `procesando`; **se desbloquea en los estados finales** (`listo`, `parcial`, `error`, `sin_extractor`, `cancelado`) y cuando el sondeo agota su tiempo (`estado_no_disponible`), aunque la tarjeta siga en el compositor, y entonces se muestra la instrucción de que ya se puede cambiar de proyecto. Así no se puede enviar bajo el proyecto B un turno mientras el PDF recién admitido, todavía en proceso, pertenece al A que estaba seleccionado al iniciar la carga. (Código: `BottomBar.jsx`, prop `bloqueado` de `SelectorDeProyecto`; pruebas en `BottomBar.test.jsx`.)

**Pruebas:** en GitHub Actions `37454479696`, backend sin DB: 2363 pasadas / 1477 omitidas; backend con DB: 3838 pasadas / 2 omitidas; frontend y guardias pasaron. Localmente el backend sin DB había dado 2363 / 1479 y el frontend 1393 / 0. La integración de cola y el arnés de 20 usuarios sí corrieron en MariaDB desechable en CI. Los pisos sin DB suben backend +5 y frontend +4 respecto a `origin/master d283d90`; el piso DB quedó medido en 3838.

**Pendiente:** reauditoría adversarial aprobada del SHA final y cierre de CI tras actualizar el piso a 3838. La primera reauditoría rechazó `ff619e7` por persistir la posibilidad de cambiar de proyecto después de iniciar una subida; ahora el selector se bloquea durante la subida y mientras el PDF permanece en el compositor. Sus hallazgos anteriores de bloqueo de turno, traducción de autorización, tipo PDF y procedencia del piso se corrigieron. No se integró ni desplegó.

**Incidente de CI (2026-10-06):** la primera corrida aislada de backend con DB en PR #208 reportó `1 failed, 3836 passed, 2 skipped`. El único fallo fue `test_pdf_escaneado_desde_chat_se_encola_y_expone_su_estado`: el test construía `AuthUser.tenant_id` con el alias textual de `Entorno`, mientras la ruta que guarda en proyectos requiere el ID numérico real de tenant. El harness `JAX_CI_NO_DB=1` omitía ese camino. Se corrigió el fixture usando `tests.identidades._tenant_db_id`; la suite DB posterior pasó.

**Corrección de test y medición (2026-10-06):** la preparación de la carga usa ahora `POST /api/chat/upload` autenticado. El job de CI `37454479696` confirmó la integración y midió 20/20 turnos con p95 721.1 ms y máximo 769.4 ms. La primera corrida que alcanzó la carga dejó una fila abierta que contaminó una prueba posterior; se añadió un finalizador que borra esa fila solo en la MariaDB desechable. La corrida final pasó `3838 passed, 2 skipped`; piso DB actualizado de 3830 a 3838 conforme a la medición del runner.

**Alternativas descartadas:** no conectar el test harness al puerto 3308, porque es producción; no autorizar el opt-in que el harness ofrece; no usar un mock para atribuirle un p95 real. Las tres decisiones evitan mezclar un ensayo con operación real o convertir una simulación en evidencia falsa.

## Traspaso registrado

**Fecha y autor:** 2026-10-06, Codex.

**Objetivo:** cerrar E3 para PDFs escaneados subidos desde el chat, usando la cola durable del proyecto y mostrando estados reales sin esperar OCR en la petición.

**Hecho:** commits locales `8161f02` (admisión a cola), `8b1883d` (evidencia y handoff inicial), `ff619e7` (turno de texto no espera OCR) y `2984c5f` (selector ligado al contexto del PDF); el SHA de PR #208 se encuentra después de estos commits. La auditoría adversarial rechazó `8b1883d`, `ff619e7` y `a998c98`; cerró hallazgos funcionales, pero mantuvo bloqueada la evidencia DB/carga. El selector ahora se bloquea durante toda subida y mientras el PDF escaneado esté en el compositor. Suite frontend 1393 y backend no-DB 2363/1479 skips pasan.

**Pendiente:** corrida CI del commit que actualiza el piso/documentación y auditoría aprobada sobre SHA final. La carga de 20 usuarios y la integración DB ya pasaron. No se tocó `jax_memory` ni producción. No se editó `PENDIENTES.md`.

**Decisiones y alternativas:** usar `project_documents` y su dispatcher proviene de la arquitectura del proyecto verificada por el arquitecto Tier 3. No llamar LAS MANOS directamente ni usar una tarea efímera proviene del contrato vigente de cola. No presentar un p95 simulado y no conectarse al puerto 3308 provienen del encargo y de la regla de carpintero. El selector se bloquea mientras se sube porque la admisión duradera ocurre antes del POST del turno, y habilitar el cambio permitiría dos contextos distintos.

**Cierre:** la medición de carga y la auditoría adversarial del SHA `c2b7e1d` concluyeron. Se mantiene PR #208 en borrador hasta que CI termine verde sobre el head final. No integrar ni desplegar desde esta sesión. El checkout principal y producción permanecen intactos.

**Cierre de ronda 2 (2026-10-06):** los hallazgos del informe de auditoría `1 MAJOR, 5 MINOR` se atendieron en la rama `feat/e3-respaldo-chat` (SHA de código `65b9449`). Ese cierre **no cuenta como aprobación**: el `APROBADO` que se registró entonces lo dio un auditor invocado por quien implementó, y la auditoría independiente de `b209f7de` resultó **APROBADO CON CAMBIOS** con 2 MAJOR y 5 MINOR, atendidos en la ronda 3 (arriba). El OCR de LAS MANOS real continúa como pendiente fechado y asignado a Fernando; no se ejecutó producción ni se integró a `main`.
