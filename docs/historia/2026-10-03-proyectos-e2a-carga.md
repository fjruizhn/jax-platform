# Proyectos E2a, carga del peor caso, EXPLAIN y revisión visual (T12)

Fecha: 2026-10-03. Quién midió: Claude Sonnet 5.5 (escalón 2, implementador). Rama
`feat/proyectos-e2a` de jax-platform, HEAD de partida `683344f`; el orquestador entra en `137d75d`.
Tipo de registro: HISTORIA (lo medido ese día). Un número de carga caduca si cambia el esquema,
el volumen de datos o la infraestructura: se vuelve a medir.

## Qué se midió y con qué

Orquestador: `loadtest/proyectos_e2a.py` (subcomandos `medir`, `visual`, `limpiar SUFIJO`), con
sus pruebas puras en `loadtest/test_proyectos_e2a.py`. Reutiliza `loadtest/proyectos_e1.py`.

- Backend REAL de este worktree, un solo uvicorn en `127.0.0.1:18280`, HTTP de punta a punta.
- Base de PRUEBA propia por corrida (`jax_memory_test_<sufijo>`), creada y armada por los caminos
  de la suite, ELIMINADA al terminar (también en un fallo; probado con un SIGTERM a media corrida).
  Nunca `/etc/jax/.env` ni `jax_memory`. El clonador de la plantilla se niega por sus 5 triggers
  (`BaseDeTestInvalida`), por eso la base propia, el mismo camino que usó E1 en `peor-caso`.
- Workspace (`JAX_WORKSPACE_DIR`) en un directorio temporal, nunca `~/jax-workspace`. Al terminar
  la corrida no quedaba ningún archivo bajo `entrada/` (el despachador borra la copia al recibir
  el resultado).
- LAS MANOS FALSO (`ManosFalsas`, puerto 18281): `POST /procesamiento/trabajos` responde 202 con
  `job_id`; `GET` devuelve `running` y, pasados 3 s, `completed` con un resultado `ok` por ruta.
  Exige la credencial de servicio. Sin GPU, sin OCR, sin datos reales. El despachador REAL (T7)
  corre contra él. El LAS MANOS de producción (:7777) no se tocó.
- Lote: 120 archivos con los tamaños de `~/jax-workspace/proyectos/lacteos-victoria/fuente/`
  (solo `lstat`; el contenido no se abrió ni se copió): 308.235.773 bytes = 294,0 MiB, mayor
  32.782.476 B, menor 537 B. Bytes aleatorios nuevos en cada subida (nada es duplicado) y extensiones
  de las que la API publica en `GET /api/proyectos/documentos/limites`. Se subió 3 veces por corrida.
- Chat en paralelo: 20 usuarios distintos, miembros del mismo proyecto, en bucle cerrado y sin
  tiempo de pensar (más duro que un usuario real), durante toda la corrida (30 s antes, las 3 subidas
  con 5 s de reposo entre ellas, 15 s después). Un «turno» = `GET /api/proyectos/{id}` por el
  MISMO uvicorn que recibe la subida (autorización de proyecto de E1 por el event loop y el pool que
  comparte) + `resolve_scope` + `retrieve_authorized` de B9 directo contra la base, que es lo que
  `api/chat.py` corre antes de llamar al modelo (mismo método que E1, `peor-caso`). No se llama a
  ningún modelo.
- Subidas simultáneas: 10 usuarios, cada uno dueño de un proyecto distinto, 5 rondas, lotes de 4
  archivos de 64 a 512 KiB.
- Memoria: VmRSS del uvicorn leído de `/proc/<pid>/status` cada 100 ms.
- EXPLAIN: las funciones REALES de `proyectos_documentos/repositorio.py` se llaman y un parche de
  `aiomysql.Cursor.execute` graba la sentencia que mandan; a esa misma sentencia con sus mismos
  parámetros se le hace `EXPLAIN` y, si es un SELECT, `ANALYZE` (da las filas leídas de verdad). Sobre
  10.000 filas en `project_documents`: 60 % en un proyecto (LACTOVI), el resto en los otros 11;
  ~20 % ocultas; ~2 % `en_cola`, ~8 % abiertas con job, el resto terminales. `ANALYZE TABLE` antes.

Entorno (hall9000): AMD Ryzen 9 9950X, 32 hilos; MariaDB 12.3.3 (la misma versión de producción);
Python 3.14.4, uvicorn 0.51.0, starlette 1.3.1, fastapi 0.139.2, httpx 0.28.1; `/tmp` es tmpfs.
La máquina NO estaba en reposo: load average 5,2 / 11,0 / 12,9 al terminar (otras sesiones corriendo).

Reproducir (dos corridas completas, 2026-10-03 04:16 y 04:18 CST):

    set -a; . ~/.config/jax/test-db.env; set +a
    JAX_REPO_PATH=/home/fruiz/worktrees/jax-proyectos-e2a \
    JAX_CONFIG_PATH=/home/fruiz/worktrees/jax-proyectos-e2a/config/config.toml \
    PYTHONPATH=/home/fruiz/worktrees/jax-proyectos-e2a \
    python loadtest/proyectos_e2a.py medir

## Resultados (corrida 1 / corrida 2)

### Turno de chat, parte de plataforma, 20 usuarios (p95, ms)

| Fase | Turno total | Parte HTTP (uvicorn de la subida) | Parte B9 (autorización directa) |
|---|---|---|---|
| Sin subida (antes, reposo, después) | 42,0 / 41,2 | 4,6 / 4,4 | 38,6 / 37,9 |
| Durante la subida de 294 MiB | 41,5 / 40,3 | 4,7 / 4,3 | 38,2 / 37,2 |
| Durante el procesamiento (tras la subida) | 40,0 / 39,4 | 4,1 / 4,2 | 37,0 / 36,1 |

Turnos: 49.695 y 50.528 (37.354 / 37.907 sin subida; 1.308 / 1.462 durante la subida). Errores: 0 en
las dos. p99 sin subida 52,6 / 52,7 ms; durante la subida 48,2 / 49,5 ms. Máximo sin subida
114,6 / 135,2 ms; durante la subida 56,4 / 84,2 ms. Veredicto con el criterio de E1 (p95 con
escrituras mayor que 2x el de sin ellas, o cualquier error): no degrada, en las dos.

La subida no mueve el p95 más allá del ruido. Los máximos de «sin subida» son mayores que los de
«durante la subida» porque esa muestra es 25 veces más grande y la máquina estaba cargada por otras
sesiones, no porque la subida mejore nada.

### Subida del lote de LACTOVI (294 MiB, 120 archivos), tres repeticiones por corrida

| | Corrida 1 | Corrida 2 |
|---|---|---|
| Respuesta 202, archivos aceptados | 3 de 3, 120 cada una | 3 de 3, 120 cada una |
| Duración de la subida (repeticiones 1, 2, 3) | 0,68 / 0,67 / 0,66 s (433-445 MiB/s) | 0,73 / 0,67 / 0,71 s (400-436 MiB/s) |
| Hasta que la última fila deja `en_cola`, tras la respuesta | 0,04 / 0,06 / 0,02 s | 0,08 / 0,07 / 0,05 s |
| Hasta que todas son terminales, tras la respuesta | 8,68 / 3,84 / 3,78 s | 8,64 / 3,77 / 3,79 s |
| Filas finales de LACTOVI | 360, todas `listo`, 924.707.319 B | 360, todas `listo`, 924.707.319 B |

El «hasta terminales» no es el tiempo de procesar: es la cadencia del despachador (vuelta de 10 s) más
los 3 s de retraso del LAS MANOS falso. La primera subida de cada corrida tarda más porque su
sincronización cae en otra fase del ciclo de 10 s. Los 5 trabajos por lote salen de dividir 120 rutas
en trozos de 50 (`rutas_por_trabajo`). El tiempo de OCR real no se midió.

### Memoria del uvicorn durante la subida de 294 MiB

Base 98,1 / 98,8 MiB (mediana de los 10 s previos); pico 118,0 / 119,2 MiB en el peor instante de una
subida: +19,9 / +20,4 MiB. El lote entero (294 MiB) no se carga en memoria. Aclaración: Starlette deriva
a un archivo temporal cada parte mayor de 1 MiB, y aquí `/tmp` es tmpfs, así que esos bytes viven en
RAM del sistema aunque no cuenten en el RSS del proceso; con `/tmp` en disco no.

### 10 subidas simultáneas a 10 proyectos distintos

5 rondas por corrida = 50 lotes por corrida (200 archivos): 50 de 50 respuestas 202 en las dos, **0
respuestas 5xx**. Latencia de la subida p95 25,0 / 27,4 ms. Una ronda completa tarda 23-30 ms. Todas las
filas terminales 4,6 s después de la última subida. Log del backend: 0 líneas `ERROR`, 0 tracebacks, 0
deadlocks (1213), 0 lock timeouts (1205) en las dos corridas.

### EXPLAIN sobre 10.000 filas (idéntico en las dos corridas)

| Consulta | Plan (índice, filas estimadas) | Filas leídas de verdad | Alertas |
|---|---|---|---|
| listar visibles, 1.ª página (proyecto grande) | `PRIMARY` range, ~9.700 | 3.839 para 51 filas | ninguna |
| listar visibles, 2.ª página | `PRIMARY` range, ~8.100 | 64 | ninguna |
| listar visibles, proyecto chico | `idx_project_documents_lista`, 295 | 51 | ninguna |
| listar ocultos | `idx_project_documents_lista`, ~1.133 | 51 | `Using filesort` (el aceptado) |
| tomar_en_cola | `idx_project_documents_despacho` (ref), 190 | 190 | ninguna |
| trabajos_abiertos | `idx_project_documents_despacho` (range), ~567 | 567 | **`Using temporary`** |
| INSERT condicional | const sobre scope, usuario y membresía | n/a | ninguna |
| extra: filas_abiertas_de_trabajo | `idx_project_documents_despacho`, 49 | 48 | **`Using filesort`** |
| extra: aplicar_resultado (UPDATE) | `idx_project_documents_despacho`, 49 | n/a | ninguna |
| extra: existente_por_sha | `uq_project_documents_sha` const | n/a | ninguna |

Los tiempos reales de cada llamada fueron de 0,1 a 1,9 ms.

Lo que el criterio de la T12 («ni filesort ni temporary, salvo el filesort ya aceptado de ocultos»)
deja afuera, sin tocarlo:

1. `trabajos_abiertos` usa `Using temporary` (el `SELECT DISTINCT job_id`). A 10.000 filas es 0,4 ms; el
   plan lee todos los trabajos abiertos en cada vuelta del despachador.
2. `listar visibles` no usa `idx_project_documents_lista` sino `PRIMARY` (`id < ? ORDER BY id DESC`) y
   filtra por `project_id` sobre la marcha. Para el proyecto chico el optimizador sí eligió el índice de
   la lista, pero para el proyecto más antiguo y grande leyó 3.839 filas para devolver 51. El
   docstring del repositorio declara el índice de la lista como el de `listar`. Con 10.000 filas no
   duele (1,9 ms); el costo depende de cuántas filas de OTROS proyectos más nuevos haya que saltar,
   así que crece con la tabla. Sin medir a mayor escala no se sabe dónde deja de ser aceptable.
3. Extra, fuera del brief: `filas_abiertas_de_trabajo` tiene `Using filesort` (ordena ~50 filas).

## Revisión visual (claro y oscuro)

Capturas en `docs/capturas/e2a/` (20 PNG, 1280x800; sufijo `-light` / `-dark`): pestaña Documentos
(lista con los ocho estados, y vista de ocultos), Selector con resumen e ignorados (antes y con los
`<details>` abiertos), botón en la barra de Chat (sin proyecto, con proyecto, con papel de solo
lectura, ventana «Elegí un proyecto», Selector abierto desde la barra) y en la barra de Pipeline.
Hecho con playwright-core 1.64 y Chromium (el motor del MCP de playwright), contra el backend de la
corrida `visual`: base propia, LAS MANOS falso con 1 h de retraso (lo despachado se queda
`procesando`).

Sin diálogos del navegador (se escuchó el evento `dialog` en las dos pasadas: 0), y el barrido de
`confirm(`, `alert(` y `prompt(` desnudos en `frontend/src` no encuentra ninguna llamada (solo
comentarios); `src/politica/dialogosDelNavegador.test.js`: 10 de 10.

Defectos vistos (se anotan, no se arreglan aquí):

1. **Los botones «Archivos» y «Carpeta» del Selector quedan pegados en la barra de Chat y Pipeline y no
   se pueden cerrar.** Al pulsar el botón de documentos (📄), el Selector se monta en línea y sus dos
   botones de elegir (44 px de alto, en una fila de controles de 24 px) aparecen en medio de la barra.
   Escape no los cierra (solo cierra los diálogos), un segundo clic en 📄 tampoco, y siguen ahí al
   cambiar de modo y de proyecto. Medido: `Archivos@44`, `Carpeta@44` tras el clic y tras Escape.
   Capturas `chat-selector-de-documentos-*`, `pipeline-barra-*`, `chat-barra-solo-lectura-*`.
2. En la lista de documentos cada fila muestra el correo completo de quien subió; con un correo largo
   la fecha cae a otra línea (`documentos-*`, «Planilla de turnos.xls»). Menor.
3. En Pipeline, junto al 📄 de documentos del proyecto aparecen «Archivos» y «Carpeta» (los del
   Selector o los del propio Pipeline, no se distinguen): dos pares de controles de nombre parecido y
   alto distinto en la misma fila. Es consecuencia del 1, pero merece revisar el nombre.

En las capturas se ve «LAS MANOS caído» y «No se pudo cargar la lista de pipelines detenidos»: es el
arnés (la base propia no tiene `jacobs_pipelines`, que crea Jacobs; el backend responde 500 a esa
lista), no un defecto de E2a.

## Límites de lo medido

- Todo es loopback en una sola máquina: la subida de 294 MiB tarda 0,7 s. Un cliente real por LAN o
  internet mantiene la petición abierta mucho más tiempo; el efecto sobre el event loop y las conexiones
  de ese caso no se midió.
- El turno de chat es solo la parte de plataforma. Quedan fuera el modelo y `save_message` (la
  persistencia del mensaje en la memoria, fire-and-forget que necesita embeddings).
- LAS MANOS es falso: no hay medición de OCR, de GPU ni de lo que pasa si LAS MANOS tarda o falla.
- 10.000 filas es poco para juzgar los dos hallazgos de EXPLAIN; el crecimiento no se midió.
- Una sola máquina cargada por otras sesiones: la variación entre las dos corridas (p95 con 1-2 ms de
  diferencia) es el ruido medido.

## Riesgos aceptados

*(Ronda final de la Parte B, 2026-10-03; decisión del controlador tras la auditoría de escalón 3.
Se aceptan con su cota; ninguno es un fallo abierto.)*

- **`trabajos_abiertos` con `Using temporary`.** El `SELECT DISTINCT job_id` de los trabajos abiertos
  arma una tabla temporal. Cota: a lo sumo 4 trabajos abiertos × 50 filas; medido 0,4 ms.
- **`filas_abiertas_de_trabajo` con `filesort`.** Ordena las filas de UN trabajo por `id`. Cota: ≤ 50
  filas por trabajo, que es el tope de rutas de LAS MANOS.
- **Las filas `en_cola` de proyectos no activos se recorren en cada vuelta del despachador.** El JOIN
  con `jax_project_scope` las descarta, pero se leen. Cota: el `LIMIT 1000` de `tomar_en_cola`.
- **El extractor de la prueba de paridad (`frontend/src/api/proyectos.test.js`) toma cualquier cadena
  entre comillas** de las llamadas que lee. Solo agranda la red (pide texto para algo que quizá no es un
  código), nunca deja un código sin texto.
- **Más de 300 filas cargadas en la pestaña Documentos: las que pasan de 300 quedan con el estado
  congelado.** El sondeo de 5 s refresca solo las primeras 300 (`TOPE_SONDEO` de `Documentos.jsx`,
  declarado en el código); las demás se actualizan al recargar la pestaña.
- **Si falla la re-consulta del botón 📄 (papel y estado del proyecto), el botón se cierra.** Falla
  cerrado: nunca se ofrece subir sin saber el papel.
- **Desenlace incierto del POST a LAS MANOS: trabajo duplicado posible.** Un `ReadTimeout` o un corte
  después de mandar el pedido no dice si LAS MANOS creó el trabajo. Mitigado con la ventana de 5 min
  sin re-despachar esas filas; si aun así sale dos veces, el resultado del duplicado se ignora.
- **Las capturas de la revisión visual se tomaron con `playwright-core` y no con el MCP de
  playwright.** Mismo motor (Chromium); cambia solo el arnés.
- **nginx responde 413 en HTML si un admin sube `max_bytes_lote` por encima de `client_max_body_size`.**
  El cliente no recibe el JSON con el código y la persona ve el mensaje genérico. Se corrige alineando
  los dos valores en el despliegue (Parte C), no en el código.
