# E3 · respaldo de PDF escaneado en el chat

**Fecha:** 2026-10-06
**Estado de carga:** no medido; el p95 queda pendiente de un MariaDB aislado disponible.

## Intento y resultado

La carga solicitada es de 20 usuarios enviando turnos de chat mientras un PDF escaneado espera en la cola de LAS MANOS. No se ejecutó ni se asigna un p95 ficticio.

El arnés local detectó `JAX_DB_PORT=3308`, puerto de la instancia de producción, y rechazó crear la base de pruebas (`base_de_test.py::exigir_conexion_permitida`). No se habilitó `JAX_TEST_DB_PERMITIR_INSTANCIA_DE_PRODUCCION`. Docker está instalado, pero su daemon devuelve `permission denied` en `/var/run/docker.sock`; tampoco hay `mariadbd`/`mysqld` instalado para iniciar una instancia desechable. El modo `JAX_CI_NO_DB=1` no puede ejercitar la cola durable ni servir `/api/chat`, por lo que no representa el escenario de carga.

| Medida | Resultado |
|---|---:|
| Usuarios simultáneos de chat | No ejecutado |
| Solicitudes de chat medidas | 0 |
| PDF escaneado despachado | No ejecutado |
| p95 del turno de chat | N/A — sin medición |
| Base de datos de producción `jax_memory` usada | No |

## Lecturas verificadas

- La ruta de subida encola el original en `project_documents` y avisa al despachador sin esperar al trabajo de LAS MANOS.
- La cola y su sondeo HTTP del procesador corren en el despachador existente; este cambio no llama OCR dentro del turno de chat.
- Las pruebas sin DB no demuestran latencia ni integración real del despachador.
- Verificación local completa sin DB: backend `2363 passed, 1479 skipped`; frontend `1393 passed, 0 failed`. Los tests que abren MariaDB se omitieron por `JAX_CI_NO_DB=1`.
- Delta de piso medido contra `origin/master d283d90`: backend `+5` pasadas (dos casos de upload sin DB, dos lecturas unitarias de estado y una comprobación del sufijo PDF); frontend `1389→1393` (`+4`: estado OCR, turno sin contenido OCR, traducción de autorización y selector bloqueado para conservar el proyecto). El test `FileAttachment` preexistente ya estaba versionado en `origin/master`.

## Para completar la medición

Repetir el escenario con el job de CI que levanta MariaDB desechable o en un host que tenga un contenedor aislado accesible en un puerto que no sea 3306 ni 3308. Medir duración de cada POST `/api/chat`, p95, cantidad de turnos completados y el estado del documento mientras el despachador está activo; registrar si LAS MANOS fue real o simulado.

## Registro de trabajo y traspaso

**Objetivo:** registrar el PDF escaneado del chat en el proyecto autenticado y entregar respuesta sin esperar el OCR; mostrar después el estado verdadero del procesador.

**Hecho (2026-10-06):** `PdfSinTexto` ahora lleva el total de páginas. `/api/chat/upload` exige `project_id` para el escaneado, vuelve a validar membresía/papel de escritura y proyecto activo, limita bytes/páginas con la configuración existente, y reutiliza el guardado durable, deduplicación y dispatcher de `project_documents`. El nuevo GET de estado comprueba visibilidad del proyecto y oculta documentos ocultos/ajenos con 404. BottomBar sondea el estado y la tarjeta usa i18n es/en y `aria-live`. No se envía el PDF al modelo ni se presenta como leído: E2b no está implementado.

**Decisión técnica:** se reutiliza el dispatcher existente, que ya llama el API vigente de LAS MANOS; no se crea una tarea efímera en FastAPI ni se espera OCR dentro de la petición. La suba de chat no crea un adjunto de texto que el modelo pudiera confundir con extracción real. La decisión procede del alcance E3 del encargo, no de una nueva decisión de negocio.

**Contexto fijado:** al iniciar una subida se captura el proyecto autenticado seleccionado. El selector queda deshabilitado durante la subida y, si el archivo resultó escaneado, hasta que el usuario quite la tarjeta del compositor o salga del modo Chat. Así no se puede enviar bajo el proyecto B un turno mientras el PDF recién admitido pertenece al A que estaba seleccionado al iniciar la carga.

**Pruebas:** backend completo en modo aislado sin DB: 2363 pasadas / 1479 omitidas / 3 warnings; frontend: 1393 pasadas / 0 fallidas. Prueba focal backend de sufijo PDF: 1 pasada / 121 omitidas por el filtro; pruebas focales frontend de compositor (23) y selector (7) pasaron. Los casos de admisión real en la cola y aislamiento HTTP requieren MariaDB y no se ejecutaron aquí. Los pisos suben backend +5 y frontend +4 respecto a `origin/master d283d90`.

**Pendiente:** cargar el escenario de 20 usuarios en MariaDB desechable, medir y registrar p95 con LAS MANOS real o simulada, correr pruebas DB de cola/autorización y obtener auditoría adversarial aprobada del SHA final. La primera reauditoría rechazó `ff619e7` por persistir la posibilidad de cambiar de proyecto después de iniciar una subida; ahora el selector se bloquea durante la subida y mientras el PDF permanece en el compositor. Sus hallazgos anteriores de bloqueo de turno, traducción de autorización, tipo PDF y procedencia del piso se corrigieron. No se integró ni desplegó.

**Alternativas descartadas:** no conectar el test harness al puerto 3308, porque es producción; no autorizar el opt-in que el harness ofrece; no usar un mock para atribuirle un p95 real. Las tres decisiones evitan mezclar un ensayo con operación real o convertir una simulación en evidencia falsa.

## Traspaso registrado

**Fecha y autor:** 2026-10-06, Codex.

**Objetivo:** cerrar E3 para PDFs escaneados subidos desde el chat, usando la cola durable del proyecto y mostrando estados reales sin esperar OCR en la petición.

**Hecho:** commits locales `8161f02` (admisión a cola), `8b1883d` (evidencia y handoff inicial), `ff619e7` (turno de texto no espera OCR) y `2984c5f` (selector ligado al contexto del PDF). La auditoría adversarial rechazó `8b1883d` y `ff619e7`; el segundo confirmó el cierre de los hallazgos funcionales previos y pidió fijar proyecto, actualizar/eliminar el handoff y resolver DB/carga. El selector ahora se bloquea durante toda subida y mientras el PDF escaneado esté en el compositor. Las suites completas pasan en los conteos indicados arriba.

**Pendiente:** prueba DB de ingreso/aislamiento, prueba de carga de 20 usuarios con PDF en proceso y auditoría aprobada sobre SHA final. No se tocó `jax_memory` ni producción. No se editó `PENDIENTES.md`.

**Decisiones y alternativas:** usar `project_documents` y su dispatcher proviene de la arquitectura del proyecto verificada por el arquitecto Tier 3. No llamar LAS MANOS directamente ni usar una tarea efímera proviene del contrato vigente de cola. No presentar un p95 simulado y no conectarse al puerto 3308 provienen del encargo y de la regla de carpintero. El selector se bloquea mientras se sube porque la admisión duradera ocurre antes del POST del turno, y habilitar el cambio permitiría dos contextos distintos.

**Siguiente paso exacto:** configurar una base MariaDB desechable aislada, ejecutar primero `cd /home/fruiz/wt/jxp-e3-respaldo/backend && python3 -m pytest -q tests/test_proyectos_documentos_api.py`, después medir 20 turnos concurrentes mientras el documento está en cola; actualizar esta evidencia y pedir auditoría del SHA resultante. Si sigue sin DB aislada, dejar carga como `N/A` y no pedir integración.

**Siguiente comando (backend desde `backend/`):** `env -u JAX_DB_HOST -u JAX_DB_PORT -u JAX_DB_USER -u JAX_DB_PASSWORD -u JAX_DB_NAME JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/tmp/jax-e3-test-config-absent.json JAX_WORKSPACE_DIR=/tmp/jxp-e3-workspace PYTHONPATH=/home/fruiz/jax:/home/fruiz/jax/las_manos python3 -m pytest -q`.
