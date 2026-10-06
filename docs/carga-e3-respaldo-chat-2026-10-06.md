# E3 · respaldo de PDF escaneado en el chat

**Fecha:** 2026-10-06
**Estado de carga:** medido en CI con MariaDB desechable, sin tocar producción.

## Intento y resultado

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

## Para completar la medición

No queda pendiente la medición E3 solicitada. Si cambia el código del camino, el volumen de proyecto o la infraestructura, repetirla; el resultado actual describe el runner de CI con LAS MANOS y el proveedor simulados.

## Registro de trabajo y traspaso

**Objetivo:** registrar el PDF escaneado del chat en el proyecto autenticado y entregar respuesta sin esperar el OCR; mostrar después el estado verdadero del procesador.

**Hecho (2026-10-06):** `PdfSinTexto` ahora lleva el total de páginas. `/api/chat/upload` exige `project_id` para el escaneado, vuelve a validar membresía/papel de escritura y proyecto activo, limita bytes/páginas con la configuración existente, y reutiliza el guardado durable, deduplicación y dispatcher de `project_documents`. El nuevo GET de estado comprueba visibilidad del proyecto y oculta documentos ocultos/ajenos con 404. BottomBar sondea el estado y la tarjeta usa i18n es/en y `aria-live`. No se envía el PDF al modelo ni se presenta como leído: E2b no está implementado.

**Decisión técnica:** se reutiliza el dispatcher existente, que ya llama el API vigente de LAS MANOS; no se crea una tarea efímera en FastAPI ni se espera OCR dentro de la petición. La suba de chat no crea un adjunto de texto que el modelo pudiera confundir con extracción real. La decisión procede del alcance E3 del encargo, no de una nueva decisión de negocio.

**Contexto fijado:** al iniciar una subida se captura el proyecto autenticado seleccionado. El selector queda deshabilitado durante la subida y, si el archivo resultó escaneado, hasta que el usuario quite la tarjeta del compositor o salga del modo Chat. Así no se puede enviar bajo el proyecto B un turno mientras el PDF recién admitido pertenece al A que estaba seleccionado al iniciar la carga.

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

**Siguiente paso exacto:** esperar CI del nuevo SHA de PR #208; si pasa, ejecutar la medición de 20 turnos concurrentes mientras el documento está en cola en entorno DB aislado, actualizar esta evidencia y pedir auditoría del SHA resultante. Si sigue sin entorno de carga aislado, dejar carga como `N/A` y no pedir integración.

**Siguiente paso:** esperar CI del SHA final después de subir el nuevo piso DB y este registro, luego auditar ese SHA exacto. El checkout principal y producción permanecen intactos.
