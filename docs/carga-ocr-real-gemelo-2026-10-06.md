# Carga de OCR real con un gemelo aislado (2026-10-06)

Medición en hall9000 del costo de procesar PDF escaneados con el OCR REAL de LAS MANOS
(tesseract 5.5.0 `spa` + poppler a 300 dpi) mientras 20 usuarios chatean, sin tocar producción.
Ejecutó un escalón 2 con el diseño del auditor de escalón 3 (punto 7 de su veredicto sobre
b209f7de) y el GO de Fernando del 2026-10-06. Los números son de esta máquina y de este día: si
cambian el esquema, el volumen o el hardware, hay que volver a medir.

## Qué se midió y con qué

| Pieza | Valor |
|---|---|
| Host | hall9000, 32 núcleos, 91 GB; producción corriendo al lado (carga 1 min de fondo: 2,4 a 9) |
| LAS MANOS | jax `f47820f5`, mismos binarios del host; lanzador propio que solo anula la composición B7 (necesita el manifiesto de build de producción) |
| jax-platform | `365adba` (HEAD de `feat/e3-respaldo-chat` al armar el gemelo; la rama se rebasó después a `517b13f`: el diff 365adba..517b13f no toca la ruta de subida ni el OCR, solo `api/admin/models.py`, `contrato_dispatch.py`, pruebas, docs y `policy.yml`) |
| Base | `mariadb:12.3.3` efímera, nombre único, `--rm`; base `jax_memory_test_ocrgem` |
| Aislamiento | todo dentro del netns del contenedor (`nsenter --net`), sin ruta por defecto; 7777, 11434 y 3308 dieron conexión rechazada, también por `172.17.0.1` (se observó en la sesión y no se guardó la salida: no hay artefacto que lo respalde; solo quedan en los registros rechazos a 3306, que no es ninguno de esos puertos). Credenciales nuevas al azar en archivos 0600; `JACOBS_URL` y la URL de Ollama de LAS MANOS a puertos muertos; sin Telegram |
| Chat | proveedor SIMULADO (no es Ollama) con demora fija de 50 ms; responde en un formato que la plataforma marca `contract_degraded`, pero recorre el mismo camino del turno. El chat de la Mesa toma un `flock` por llamada (carril), así que este proveedor lo serializa: tope de unos 17 turnos/s |
| Topes de adjuntos | valores por defecto documentados en `docs/superpowers/specs/2026-09-17-frente-d-adjuntos-por-referencia.md` (10 MiB, 20 páginas, 8000 caracteres, 30 subidas/min por usuario, 1 subida y 1 proceso de PDF en proceso). Durante la corrida NO se leyeron los `JAX_ADJUNTO_*` ni los `JAX_ADJUNTOS_*` reales de `/etc/jax/.env` (la regla permitía solo `JAX_PROCESAMIENTO_*` y `OMP_THREAD_LIMIT`). Después, el 2026-10-06 por la tarde, la sesión principal (con la ventana de Fernando abierta) leyó solo esas claves en producción. `JAX_ADJUNTO_*`: `MAX_BYTES=10485760`, `MAX_CHARS=8000`, `MAX_PAGINAS=20`, `MAX_POR_MENSAJE=1`, `IMAGENES_EN_PROCESO=1`, `SUBIDAS_EN_PROCESO=1`, `PDF_PROCESOS=1`, `PDF_TIMEOUT_SEGUNDOS=30`, iguales a la columna de despliegue del spec. `JAX_ADJUNTOS_*`: `SUBIDAS_POR_MINUTO=30`, `CUOTA_BYTES_USUARIO=524288000`, `DISCO_LIBRE_MINIMO_BYTES=53687091200` (50 GiB), `RECHAZO_ESPERA_MS=1000`, `TTL_HORAS=24`. Que el gemelo usó esos mismos valores tiene respaldo independiente solo para `MAX_BYTES` y `MAX_PAGINAS` (los 413 de R3); para los demás se deduce de «se usaron los valores del spec», porque el archivo de entorno del gemelo no se conservó. La única diferencia conocida es el disco libre mínimo: 1 GiB en el gemelo frente a 50 GiB en producción; el disco libre mínimo se bajó a 1 GiB porque `/tmp` es tmpfs |
| LAS MANOS (config) | en producción no hay `JAX_PROCESAMIENTO_*` ni `OMP_THREAD_LIMIT`: rigen los defaults (4 workers de OCR, 2 de E/S, 50 rutas por trabajo, OpenMP sin límite) |
| Datos | estado financiero «Empresa Ficticia S.A.» con cifras al azar, 300 dpi, ruido e inclinación, PDF de solo imagen (JPEG DCT); `pdftotext` vacío en todos; 20 páginas y exactamente 10 485 760 bytes (el tope) por usuario, todos con sha distinto |
| Usuarios | 20 (y 40 en una corrida de escalamiento); cada uno sube 1 PDF por `POST /api/chat/upload` con `project_id`, sondea `GET /api/proyectos/{p}/documentos/{d}` cada 1 a 8 s y, mientras tanto, manda turnos `POST /api/chat` con pausa de 1 a 3 s |
| Medición | cargador asíncrono propio (latencias por petición); muestreo por segundo de `/proc` de los procesos del netns (los mismos contadores que `pidstat`, filtrados por netns para no contaminar con el tesseract de producción); guardia de `GET /health` del 7777 de producción cada 5 s |

Guardia: línea base de `/health` de producción, mediana de 12 muestras en 60 s = 1,5 a 1,6 ms. Regla de
corte: más de 3 veces eso durante más de 15 s (o carga 1 min > 1,5 × 32 núcleos). Resultado: no se
disparó nunca; máximo de `/health` durante las cargas 4,4 ms, cero respuestas distintas de 200; carga 1 min
máxima del host 20,3 (corte: 48; en esa cifra cuenta todo lo que corre en el host, no solo el gemelo).

## Resultados

Latencias en ms salvo donde dice s. p95 con el método del rango superior.

| Corrida | Usuarios | Cupo subidas | Chat p50 / p95 / máx | Subida p50 / p95 / máx | Subida → terminal p50 / p95 / máx (s) | Otros códigos |
|---|---|---|---|---|---|---|
| R0 línea base: solo chat, 60 s | 20 | n/a | 73 / 195 / 882 | n/a | n/a | 0 |
| R1 carga, cupo alto (10 por usuario, 50 global) | 20 | 10 / 50 | 64 / 153 / 767 | 297 / 401 / 447 | 213 / 365 / 366 | 0 |
| R2 carga, cupo de producción (2 / 4) | 20 | 2 / 4 | 64 / 155 / 618 | 270 / 380 / 401 | 213 / 352 / 357 | **0 respuestas 429** |
| R4 escalamiento | 40 | 10 / 50 | 96 / 388 / 1999 | 519 / 730 / 756 | 360 / 706 / 710 | 0 |
| R0b línea base: solo chat, 60 s | 40 | n/a | 165 / 777 / 2334 | n/a | n/a | 0 |
| R0c línea base: solo chat, 60 s | 60 | n/a | 792 / 21 841 / 28 661 | n/a | n/a | 0 |

Sondeo del documento (`GET`): p95 3,3 a 3,7 ms en todas. Los 20 (o 40) documentos terminaron en `parcial` (el
OCR marcó palabras o cifras dudosas, ver variantes) y ninguno en `error`.

Rendimiento (peticiones por segundo, promedio de toda la corrida): R1 chat 5,6 + sondeo 2,7 (8,35 con
subidas); R2 chat 5,7 + sondeo 2,8 (8,5); R4 chat 10,2 + sondeo 4,9 (15,2).

### Dónde está la cola

- **LAS MANOS no encola:** la espera entre «trabajo creado» y «primer archivo en marcha» fue 0,0 s en las
  tres corridas (R1, R2, R4). El trabajo nace cuando el despachador lo manda.
- **La cola está en la plataforma** (documentos `en_cola`): el despachador manda de a 4. Espera desde la
  subida hasta que el trabajo se crea: R1 p50 141 s, p95/máx 281 s; R2 igual (141 / 281 s); R4 la
  ventana de despacho fue de 635 s (el último trabajo salió a los 635 s).
- **OCR por archivo de 20 páginas:** p50 63 a 67 s, p95 65 a 71 s, o sea unos 3,3 s por página y por
  worker. Rendimiento sostenido: 4 workers ≈ 3,6 documentos de 20 páginas por minuto (≈ 1,2 páginas/s).
  El último documento terminó a los 352 s (20 usuarios) y a los 698 s (40): crece proporcional a los usuarios,
  ~17,5 s por documento al tope.

### Recursos del gemelo (por segundo, sumando los procesos de cada clase)

| | R1 (20) | R2 (20) | R4 (40) |
|---|---|---|---|
| tesseract: CPU% media / máx | 109 / 1201 | 90 / 821 | 59 / 854 |
| tesseract: RSS total máx (4 procesos) | 510 MB | 509 MB | 509 MB |
| pdftoppm: CPU% media / máx | 95 / 402 | 89 / 402 | 95 / 402 |
| pdftoppm: RSS total máx (4 procesos) | 309 MB | 310 MB | 309 MB |
| uvicorn plataforma: CPU% media / máx, RSS máx | 7 / 75, 211 MB | 7 / 71, 213 MB | 10 / 126, 285 MB |
| uvicorn LAS MANOS: CPU% media / máx, RSS máx | 1 / 11, 302 MB | 1 / 11, 370 MB | 1 / 13, 387 MB |
| Total del gemelo: CPU% media / máx | 216 / 1220 | 190 / 833 | 170 / 883 |

- Máximo 4 `tesseract` y 4 `pdftoppm` a la vez (el tope de 4 workers). Un pico de 1201 % es unos 12 núcleos de
  32. Hipótesis, no medida: serían los hilos OpenMP de cada tesseract, porque no hay `OMP_THREAD_LIMIT`; el muestreo
  suma por proceso y no cuenta hilos de tesseract (la columna `hilos_manos` es de uvicorn). En promedio el OCR ocupó 1 a 2 núcleos.
- Disco temporal: cada documento rasteriza sus 20 páginas a PNG bajo `JAX_WORKSPACE_DIR`
  (`ocr-pdf-*`), unos 55 MiB por documento (medido con `pdftoppm -png -r 300`). Con 4 en vuelo, transitorio máximo
  medido: 262 MiB (R1) y 261 MiB (R4). Crecimiento permanente: ~10 MiB por documento (original y texto), 201 MiB
  por cada 20.
- `/tmp` del gemelo es tmpfs (RAM); el workspace de producción está en disco real, donde la escritura puede ser más lenta.

### Variantes (R3, un usuario por archivo)

| Archivo | Respuesta de subida | Estado final | Tiempo (s) | Detalle de la ficha |
|---|---|---|---|---|
| legible, 3 pág. | 200 | `parcial` | 21 | confianza 96,0; dudas en la pág. 2 |
| baja calidad (desenfoque + ruido), 3 pág. | 200 | `parcial` | 23 | confianza 0; sin texto en 3 de 3 |
| páginas en blanco, 3 pág. | 200 | `parcial` | 11 | confianza 0; sin texto en 3 de 3 |
| 1 página con el JPEG truncado, 3 pág. | 200 | `parcial` | 18 | confianza 94,8; dudas en 3 de 3 |
| 21 páginas | **413** `adjunto_demasiadas_paginas` (max 20) | n/a | n/a | rechazado antes de procesar |
| tope + 1 byte (10 485 761) | **413** `adjunto_demasiado_grande` (max 10 485 760) | n/a | n/a | rechazado antes de procesar |
| tope exacto, 20 pág. | 200 | `parcial` | 73 | igual que R1 |

Hallazgo: ninguna de las variantes degradadas termina en `error`. El PDF de baja calidad y el de páginas en
blanco quedan `parcial` sin una sola palabra de texto: el estado no distingue «con dudas» de «sin texto».

## Punto de degradación

1. **El chat no se degrada por el OCR, dentro de lo medido (hasta 40 usuarios).** A igual número de
   usuarios, el p95 con OCR (153 y 388 ms) no supera al de solo chat (195 y 777 ms). Dentro de cada corrida el p95
   cae con el tiempo (183 → 113 ms en R1; 658 → 101 ms en R4) porque los usuarios dejan de chatear al terminar su
   documento, o sea que manda cuántos chatean a la vez, no cuántos PDF hay en vuelo.
2. **El chat se satura solo, con este proveedor simulado:** 20 usuarios p95 195 ms, 40 usuarios 777 ms,
   60 usuarios 21,8 s y el rendimiento cae de 17 a 12 turnos/s. Es el `flock` del carril de la Mesa
   (50 ms de servicio ⇒ ≈ 17 turnos/s). Con un modelo real el tope sería mucho más bajo; no se midió.
3. **La subida crece lineal:** ≈ 18 a 20 ms por subida simultánea de 10 MiB (p95 401 ms con 20, 730 ms con 40). Sin
   errores ni 429.
4. **Lo que sí se degrada es el tiempo hasta tener el documento listo:** crece linealmente (≈ 17,5 s por documento
   de 20 páginas) y es independiente del cupo. Con 20 simultáneos el último espera unos 6 min; con 40, unos 12 min.
   Si el criterio es «un PDF escaneado de 20 páginas listo en menos de 5 min», el límite **estimado** es de unos 16
   documentos al tope subidos a la vez: es una extrapolación: la recta de los dos puntos medidos (20 documentos → 352 s, 40 → 698 s) es ≈ 17,3 s por documento más ≈ 6 s fijos, así que 16 documentos dan ≈ 283 s y 17 dan ≈ 300,1 s, justo por encima del límite, no una
   medición; ninguna corrida tuvo 16 documentos (se corrió con 20 y con 40). Que en producción real, con menos de 4
   subidas simultáneas, no haya cola, **no se midió**: los topes de producción se leyeron después y coinciden con el spec (ver «Qué se midió», con sus límites de evidencia), pero
   no se corrió una carga con menos de 4 subidas; es solo lo que se esperaría del despachador de 4 trabajos.
5. **Producción no se vio afectada:** `/health` del 7777 no se movió de su línea base y el corte nunca se disparó.

## Conclusiones sobre el cupo

- Con el cupo de producción (2 por usuario, 4 globales) salieron **0 respuestas 429** de 20 subidas
  simultáneas, igual que con 10 / 50. La puerta `/api/chat/upload` sí toma ese cupo (`encolar_pdf_desde_chat`), pero el cupo
  solo cubre la escritura de unos milisegundos y las subidas ya llegan en serie a ese punto; su ocupación nunca pasó de lo que
  admite. Que sea por `JAX_ADJUNTO_SUBIDAS_EN_PROCESO=1` y `JAX_ADJUNTO_PDF_PROCESOS=1` es una hipótesis: no se probó.
- Por lo tanto el cupo de subidas **no protege la carga de OCR**: no limita cuántos documentos hay en cola ni en
  proceso. Lo que acota el OCR es el despachador (4 trabajos a la vez) y los 4 workers; la cola crece sin tope en la
  tabla (`en_cola`), solo se traduce en espera.
- Subir el cupo no costó nada medible en latencia (R1 vs R2: chat p95 153 vs 155 ms, subida p95 401 vs 380 ms).
  Decisión de producto, no mía: si se quiere limitar el OCR concurrente por usuario o global, hace falta otro
  tope (documentos `en_cola` + `procesando`), no este.

## Límites de la medición (qué quedó sin medir)

- Proveedor de chat simulado de 50 ms: no hay modelo real ni contención de GPU/CPU con Ollama; la conclusión 1 vale
  para la plataforma y el OCR, no para el modelo.
- Un solo documento por usuario; no se midió un usuario que sube varios, ni el chat mientras otros reintentan tras un 429.
- Corrida de 60 usuarios con OCR no hecha: el token de acceso vence entre los 15 y 18 min (no se midió el valor) y esa corrida pasaba de ese tiempo.
- Topes de adjuntos tomados del spec durante la corrida; los de producción se leyeron después y coinciden con el spec salvo el disco libre mínimo (ver «Qué se midió»), pero el archivo de entorno del gemelo no se conservó; B7 anulado en el gemelo; workspace en tmpfs.
- Primer intento de R1 descartado: los tokens vencieron a mitad (401); R1 se repitió con tokens frescos. R3 se corrió con cupo de producción.
- La prueba de 40 usuarios no tiene su línea base de 40 al lado en el mismo minuto: el p95 de solo chat con 40 es muy
  variable (cerca de la saturación), así que «con OCR ≤ sin OCR» a 40 usuarios no es una diferencia medida, solo
  que no hay degradación visible.

## Limpieza

Contenedor detenido con `docker stop` (se borró solo, `--rm`). Borrados los PDF sintéticos, el workspace, los adjuntos, los
temporales y los archivos de entorno con credenciales. Los procesos del gemelo (uvicorn de la plataforma, LAS MANOS, el
proveedor simulado y el monitor) se lanzaron con `timeout` y terminan solos; pids en el scratchpad
(`ocr-gemelo/pids.txt`). Registros crudos de cada corrida en `ocr-gemelo/res/` del scratchpad de la sesión.
