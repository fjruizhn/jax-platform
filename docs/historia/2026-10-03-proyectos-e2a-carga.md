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
- **Subir de nuevo un documento OCULTO que quedó en `error` sin procesar no lo re-encola.** Sigue dando
  «Ya estaba en el proyecto, oculto» (`duplicado_oculto`): se mantiene el contrato de que un oculto no se
  resucita (`test_duplicado_y_duplicado_oculto`, `test_oculto_en_error_no_se_resucita`). Para procesarlo,
  primero se restaura y después se vuelve a subir. (La fila visible que sí se re-encola pasa a ser de
  quien la subió de nuevo: `subido_por` y `nombre_original` nuevos.)

## Reprocesar

> **Hay dos mediciones.** La primera (13:03 a 13:12) es de cuando reprocesar compartía el cupo de
> subir; encontró la degradación que motivó el cupo propio y queda aquí como historia. La segunda
> («Medición 2», 13:25 a 13:37, más abajo) es con el cupo propio de reprocesar 1/1 y es la vigente.

### Medición 1 (cupo compartido con subir) -- HISTORIA

Fecha: 2026-10-03 (13:03 a 13:12 CST). Quién midió: Claude Opus 5.5 (escalón 2, implementador), rama
`feat/proyectos-reprocesar` de jax-platform (PR #186). Tipo de registro: HISTORIA. Es la prueba de
carga de `POST /api/proyectos/{id}/documentos/{doc}/reprocesar` (regla 4 del rendimiento). Un número
de carga caduca si cambia el esquema, el volumen de datos o la infraestructura: se vuelve a medir.

### Qué se midió y con qué

Subcomando nuevo `reprocesar` de `loadtest/proyectos_e2a.py` (mismo orquestador, mismo backend REAL en
un uvicorn propio, LAS MANOS falso, workspace temporal y base propia que se elimina al terminar). La
base propia NO está en la MariaDB de producción: se corrió contra un MariaDB 12.3.3 descartable en
`127.0.0.1:3399` (contenedor propio, borrado después; `JAX_TEST_DB_ENV` apuntando a él), con el esquema
de JAX en `2b0c163`, el mismo que usa el job con DB de CI.

- PEOR CASO del `fuente/`: un solo proyecto (LACTOVI) con 20.000 archivos de 4.096 B en 2.000
  subcarpetas (una de cada 250 cuelga de 5 niveles más) y un original por usuario a 8 niveles de
  profundidad bajo `zzz/` (el último en el orden del recorrido). Los 20.000 rellenos pesan lo mismo que el
  objetivo, así que el prefiltro por tamaño no ayuda: se leen y se hashean todos antes de llegar al
  objetivo. La fila no tiene `carpeta_procesado` (la ficha NO sirve) y su `nombre_original`
  (`Informe-<i>.pdf`) no se parece al archivo (`scan-<i>.pdf`): la preferencia por nombre tampoco acorta.
- 10 usuarios (CONTRIBUTOR de LACTOVI), cada uno con su documento en `sin_extractor`, en bucle cerrado
  durante 60 s: `POST .../reprocesar` y, si da 202, el arnés repone la fila a `sin_extractor`. Cupo por
  defecto de `axioma_config`: 2 por usuario, 4 global (el mismo de subir). Los 429 son esperados.
- En paralelo: 20 usuarios de chat (mismo turno que en E2a: `GET /api/proyectos/{id}` + `resolve_scope` +
  `retrieve_authorized`) y 5 clientes de `GET /api/proyectos/{LACTOVI}/documentos?limite=50`, 20 s antes,
  los 60 s de carga y 10 s después. El criterio de degradación es el de E2a (p95 con carga > 2x el de sin
  carga, o cualquier error).
- Descriptores abiertos del uvicorn: `/proc/<pid>/fd` cada 20 ms durante toda la corrida.
- Dos variantes del cliente que recibe un 429: INSISTENTE (espera 0,01 s; el peor caso, un cliente que no
  respeta el rechazo) y que RESPETA (espera 1 s).
- Una petición SOLA (5 seguidas, sin chat, sin lista, sin otros usuarios) antes de cada corrida.

Entorno: el de arriba (hall9000, Ryzen 9 9950X, Python 3.14); load average 1,8 al empezar y 5,7 al
terminar la primera corrida (la máquina no estaba en reposo).

Reproducir (con `JAX_TEST_DB_ENV` apuntando a una MariaDB descartable y `JAX_REPO_PATH`/`JAX_CONFIG_PATH`/
`PYTHONPATH` del JAX pinneado):

    E2A_PAUSA_429_S=1 python loadtest/proyectos_e2a.py reprocesar     # variante que respeta el 429
    python loadtest/proyectos_e2a.py reprocesar                       # variante insistente

### Resultados

Endpoint (las latencias son las de las respuestas 202; un 429 sale en ~12 ms sin recorrer nada):

| Corrida | Variante | 202 en 60 s | rps de 202 | p50 | p95 | p99 | 429 | rps total | Descriptores máx. |
|---|---|---|---|---|---|---|---|---|---|
| 13:03 | insistente | 16 | 0,25 | 16.012 ms | 17.039 ms | 17.263 ms | 15.579 | 244,6 | 101 |
| 13:05 | insistente | 16 | 0,24 | 16.436 ms | 18.035 ms | 18.142 ms | 15.593 | 230,6 | 97 |
| 13:07 | insistente | 16 | 0,24 | 16.664 ms | 18.549 ms | 18.591 ms | 14.697 | 217,6 | 92 |
| 13:09 | respeta | 16 | 0,23 | 16.742 ms | 18.690 ms | 18.955 ms | 360 | 5,5 | 95 |
| 13:11 | respeta | 16 | 0,23 | 17.170 ms | 19.261 ms | 19.401 ms | 360 | 5,4 | 89 |

Una petición sola, sin otra carga: 227 a 262 ms (13:07), 234 a 253 ms (13:09), 280 a 293 ms (13:11), las
cinco de cada serie con 202. Descriptores del uvicorn en reposo: 26 a 53; el máximo de toda la corrida
fue 101 (de 1.024 que tiene `LimitNOFILE` en producción): el recorrido en profundidad no agota
descriptores. Sin 5xx, sin `fuente_ilegible`, sin deadlocks (1213) ni lock timeouts (1205), 0
tracebacks en el log del backend en las cinco corridas.

Paralelo, p95 sin carga -> durante el reprocesar:

| Corrida | Chat (turno completo) | Lista de documentos (GET) | Veredicto de la lista |
|---|---|---|---|
| 13:03 insistente | 37,4 -> 37,2 ms | 6,6 -> 14,2 ms (2,15x) | DEGRADA |
| 13:05 insistente | 38,7 -> 38,1 ms | 6,7 -> 14,2 ms (2,12x) | DEGRADA |
| 13:07 insistente | 39,6 -> 39,1 ms | 7,1 -> 17,2 ms (2,42x) | DEGRADA |
| 13:09 respeta | 39,4 -> 40,9 ms | 7,3 -> 12,4 ms (1,70x) | no degrada |
| 13:11 respeta | 39,2 -> 38,2 ms | 7,1 -> 9,7 ms (1,37x) | no degrada |

Errores de chat y de la lista: 0 en todas.

### Qué dicen los números, sin maquillar

1. **El peor caso es lento: unos 16 a 19 s por reprocesar** con 4 a la vez (el cupo global) y el chat y la
   lista corriendo, contra 0,23 a 0,29 s una sola. Con los 4 cupos globales ocupados, cada ronda dura ~16
   s y salen 16 respuestas 202 por minuto. No hubo errores ni descriptores de más; es una cuestión de latencia.
2. **Este endpoint comparte el cupo de SUBIR** (decisión tomada): en el peor caso, 4 reprocesados a la
   vez dejan sin cupo global a todas las subidas durante ~16 s (responden 429 `subidas_simultaneas`).
3. **Con un cliente insistente, la lista de documentos SÍ se degrada más de 2x** (2,1x a 2,4x) con el criterio
   de E2a; con un cliente que espera 1 s tras un 429 no (1,4x a 1,7x). El chat no se degrada en ninguna.
   La carga de 429 (14.000 a 15.600 en un minuto) es parte de lo que degrada la lista en la variante
   insistente; cuánto, no se separó. Hay un máximo de 1,7x aun respetando el 429: está dentro del criterio,
   pero no con mucho margen.
4. **Por qué tarda, medido aparte** (fuera del servidor, en un solo proceso, sin chat ni lista, sobre un
   árbol igual): `buscar_original` sola tarda 134 ms; con 2 hilos a la vez 339 ms cada uno; con 4 hilos a la
   vez 1.270 ms cada uno (9,5x). Es el costo de recorrer y hashear 20.000 archivos pequeños con hilos que
   se pelean el GIL. Los 16 s dentro del servidor son bastante más que esos 1,3 s: la diferencia es lo que
   suman el chat, la lista y el despachador, que comparten proceso y GIL con esos hilos. Que ese sea el
   mecanismo es una hipótesis (la medición aislada lo respalda en parte); no se probó con un proceso aparte
   para el recorrido.
5. Lo que SÍ está acotado: descriptores (máximo 101), memoria (no se midió el RSS en este escenario),
   errores (0). Lo que NO se arregló en este PR: ni el cupo, ni el costo del recorrido (fuera de alcance;
   queda como pendiente).

### Límites de lo medido

- Es el peor caso a propósito: los documentos reales de LACTOVI llevan `carpeta_procesado` con ficha y el
  endpoint no recorre nada (un `open` y un hash). Esta cifra vale solo para una fila sin ficha en un
  `fuente/` de ese tamaño.
- Archivos de 4 KiB: con archivos de megas el costo se va a hashear, no a recorrer, y los hilos sueltan el
  GIL durante más tiempo; no se midió.
- LAS MANOS es falso y la fila vuelve a `sin_extractor` por obra del arnés: el despachador casi nunca
  llegó a mandar el trabajo (`trabajos_recibidos` 0 en el falso), así que NO se midió el reprocesar con el
  despacho real de fondo. La carga del despachador sobre estas filas está medida en el escenario de subida.
- La base y el workspace son de una sola máquina y de un solo uvicorn; el ciclo del recorrido compite con
  otras sesiones en hall9000 (load average de 2 a 6).
- `/tmp` es tmpfs: el recorrido no toca disco. En el disco de producción (`/srv/jax-workspace`) puede ser
  más lento la primera vez y igual de rápido con la caché de páginas caliente.

### Medición 2 (cupo propio de reprocesar, 1 por usuario y 1 global)

Fecha: 2026-10-03 (13:25 a 13:37 CST). Mismo escenario de peor caso (20.000 archivos sin pista, 10
usuarios, chat y lista en paralelo, MariaDB descartable en 3399, nunca la 3308), con tres diferencias:

- Reprocesar toma su cupo PROPIO (`proyectos.documentos.reprocesar_por_usuario` = 1 y
  `reprocesar_globales` = 1, sembradas por migración): como mucho un recorrido completo a la vez. Un
  segundo pedido recibe 429 `reprocesos_simultaneos` con `Retry-After: 2` sin recorrer nada. El cupo de
  subir (2 por usuario, 4 global) no se tocó.
- Una subida chica (4 KiB, bytes nuevos) del dueño de LACTOVI cada 0,5 s durante TODA la corrida, para
  comprobar que no recibe 429 por culpa de un reprocesar en curso.
- Por eso la línea base de la lista de documentos subió de 6,6-7,3 ms (medición 1) a ~9 ms: esas
  subidas también corren en la fase «sin carga». Los cocientes se calculan contra ESTA línea base.

Corridas, fondo PESADO (20 usuarios de chat + 5 clientes de la lista en bucle cerrado, como en la
medición 1). I = cliente insistente (espera 0,01 s tras un 429), R = respeta el 429 (espera 1 s), S =
un solo usuario reprocesa, sin nadie más que reciba 429:

| Corrida | 202 en 60 s | p50 / p95 / p99 de los 202 | 429 | rps total | Descr. máx. | Chat p95 sin -> con carga | Lista p95 sin -> con carga | Subidas concurrentes |
|---|---|---|---|---|---|---|---|---|
| I1 13:25 | 5 | 12,7 / 15,6 / 15,6 s | 21.385 | 311 | 79 | 38,6 -> 37,9 ms | 9,6 -> 17,1 ms (1,78x) | 187 de 187 en 202 |
| I2 13:27 | 5 | 11,9 / 13,8 / 13,8 s | 20.623 | 335 | 79 | 38,5 -> 39,1 ms | 9,5 -> 19,0 ms (2,00x, en el límite) | 174 de 174 |
| R1 13:28 | 5 | 12,8 / 16,3 / 16,3 s | 540 | 7,9 | 74 | 38,8 -> 37,2 ms | 9,2 -> 10,9 ms (1,18x) | 189 de 189 |
| R2 13:30 | 4 | 15,2 / 19,4 / 19,4 s | 540 | 8,2 | 77 | 36,7 -> 36,4 ms | 9,0 -> 11,0 ms (1,22x) | 184 de 184 |
| S1 13:32 | 6 | 10,7 / 12,8 / 12,8 s | 0 | 0,09 | 70 | 39,9 -> 40,8 ms | 9,3 -> 12,2 ms (1,31x) | 185 de 185 |

(Con n tan chico, p95 y p99 de los 202 son prácticamente el máximo.) Una petición sola, sin otra carga,
siguió en 230 a 310 ms en las siete corridas. Sin 5xx, sin `fuente_ilegible`, sin deadlocks (1213) ni
lock timeouts (1205), 0 tracebacks en todas.

Corridas con fondo LIGERO (2 usuarios de chat y 1 cliente de la lista; el resto igual), para separar
lo que cuesta un recorrido de lo que cuesta la tormenta de 429:

| Corrida | Quién reprocesa | 202 en 60 s | p50 / p95 / p99 de los 202 | 429 | rps total | Descr. máx. | Chat p95 | Lista p95 | Subidas |
|---|---|---|---|---|---|---|---|---|---|
| L1 13:34 | un usuario | 61 | 0,98 / 1,08 / 1,08 s | 0 | 1,0 | 66 | 39,8 -> 41,6 ms | 3,3 -> 3,8 ms (1,15x) | 178 de 178 |
| L2 13:36 | 10, insistentes | 32 | 1,84 / 2,65 / 3,29 s | 28.164 | 462 | 76 | 43,8 -> 47,3 ms | 3,7 -> 9,9 ms (**2,68x, DEGRADA**) | 177 de 177 |

### Contra el criterio

1. **Una subida concurrente a un reprocesar pasa sin 429: CUMPLE.** 1.274 subidas en las siete
   corridas, todas 202, 865 de ellas mientras había un reprocesar en curso; ninguna recibió 429. (Con
   el cupo compartido de la medición 1 esas subidas habrían quedado sin cupo durante ~16 s.)
2. **El chat no pasa de 2x en ninguna variante: CUMPLE** (de 0,96x a 1,08x).
3. **La lista de documentos no pasa de 2x en ninguna variante: NO SE CUMPLE en una.**
   - Con el cupo propio la degradación bajó respecto de la medición 1 (2,1x a 2,4x -> 1,8x a 2,0x con el
     mismo fondo pesado), y con un cliente que respeta el 429 es de 1,2x. Pero un cliente que insiste sin
     pausa sigue llegando al límite con fondo pesado (I2: 2,00x) y lo pasa con fondo ligero (L2: 2,68x).
   - **La tormenta de 429 es lo que lo causa, no el recorrido.** Un solo usuario reprocesando, sin nadie
     que reciba 429, da 1,15x con fondo ligero (L1) y 1,31x con el pesado (S1). Los 10 usuarios insistentes
     (28.164 rechazos en un minuto, 462 por segundo) llevan el mismo escenario a 2,68x (L2). Los 429 salen
     rápido (~12 ms), pero cada uno pasa por autenticación, autorización de proyecto y una lectura de la
     base antes de llegar al cupo, y a 460 por segundo eso le quita lugar a la lista. No se midió una
     corrida que SOLO martille 429 sin reprocesar nada: no se puede, porque un 429 existe solo si hay un
     reprocesar en curso que llena el cupo; L1 contra L2 es la comparación que separa el efecto.
4. **El reprocesar individual NO queda cerca de 0,23 a 0,29 s cuando el servidor está ocupado: NO SE
   CUMPLE.** Solo, tarda 0,23 a 0,31 s. Con el fondo ligero tarda 0,98 s (L1, un usuario, 1,08 s p95), y
   con el fondo pesado de 20 usuarios de chat y 5 de la lista en bucle cerrado, de 10 a 15 s (S1), aunque
   sea el único reprocesando. Es decir, el cupo propio acotó cuántos hay a la vez (y devolvió las subidas),
   pero no abarata CADA recorrido: sigue siendo del orden de 4 a 50 veces su costo en reposo según cuánto
   tráfico comparta el proceso. La causa sigue sin demostrarse (hipótesis: los hilos del recorrido
   se pelean el GIL con el event loop; la medición aislada de la medición 1 la respaldaba en parte).

### Qué sigue abierto (no se arregló en este PR)

- Un cliente que insiste contra el 429 puede degradar la lista de documentos más de 2x. El cliente del
  frontend no reintenta solo y el servidor manda `Retry-After`, pero un cliente hostil no respeta nada:
  hace falta un freno ANTES de la autenticación y de la base (por IP o por usuario), o responder el 429
  desde el cupo antes de leer el proyecto.
- El recorrido sin ficha sigue siendo caro con el servidor ocupado. Opciones: correrlo en un proceso
  aparte (sin GIL compartido), o guardar el sha256 -> ruta en el momento de la ingesta (no hay recorrido).
- Los límites de lo medido de la medición 1 siguen valiendo (peor caso a propósito, archivos de 4 KiB,
  LAS MANOS falso y filas repuestas por el arnés, un uvicorn, `/tmp` en tmpfs).
