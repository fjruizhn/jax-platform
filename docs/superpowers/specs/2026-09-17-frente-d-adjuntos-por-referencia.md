# Frente D · Adjuntos por referencia

Fecha: 2026-09-17 · Autor: Mr. Hyde (RD2) · Rama `feat/adjuntos-cableados`

## 1. Por qué

**HISTORIA** (medido en staging, `final-fix-report.md` del frente D, 2026-09-17). La subida
devolvía el archivo en base64 y el chat lo recibía de vuelta dentro del JSON. Con 25 subidas
concurrentes, el p95 de `/api/health` (5 VUs) fue:

| Escenario | health p95 | health p95 con tope de subidas = 1 |
|---|---|---|
| health solo | 0,3 ms | 0,3 ms |
| + `upload_imagen_max` c=25 | 64,9 ms | 39,2 ms |
| + `upload_pdf_max` c=25 | 244,1 ms | 114,4 ms |

Qué quedaba en el event loop, por pedido: render JSON de 14 MB de base64 (23,6 ms, fuera de
cualquier tope), `.decode("ascii")` (2,6 ms). pypdf retenía el GIL aun en un hilo (140 ms por
PDF de 20 páginas). Criterio del principal: health p95 ≤ 10 ms.

**DECISIÓN (principal, 2026-09-17):** subida binaria + chat por referencia (b), y pypdf en un
ProcessPoolExecutor acotado (c, hecho en RD1). Se mantiene `JAX_ADJUNTO_SUBIDAS_EN_PROCESO`.

## 2. Disposición en disco

`JAX_ADJUNTOS_DIR` (absoluto, 0700):

| Archivo | Contenido |
|---|---|
| `<id>.dato` | imagen: bytes crudos. texto/PDF: texto UTF-8 extraído, ya recortado a `JAX_ADJUNTO_MAX_CHARS` |
| `<id>.json` | sidecar: `id, user_id, tenant_id, tipo, origen, mime, nombre, bytes, caracteres, recortado, creado, vence` |
| `.subiendo-<hex>` | temporal de una subida en curso |
| `.tmp-<hex>` | temporal de una escritura atómica |

- Archivos 0600. Escritura atómica: temporal en el mismo directorio, fsync, `os.replace`.
- El dato se escribe primero y el sidecar después. **El sidecar es el commit**: sin sidecar, el
  adjunto no existe.
- **DECISIÓN de controlador (R23-2):** metadatos en sidecar JSON y no en una tabla, porque no
  hace falta migración de producción. Sigue el patrón de archivo de `owner_cleanup`, que no usa
  la base.
- **DECISIÓN de controlador (R23-3):** del PDF se guarda solo el texto extraído. El chat nunca
  vuelve a correr pypdf, y no quedan bytes del PDF en disco.
- `nombre` pasa por `nombre_seguro` y solo vive dentro del sidecar. **Nunca forma parte de una
  ruta.**

## 3. Modelo de dueño

- `almacen.obtener(id, user)` / `almacen.leer(id, user)` devuelven el adjunto solo si
  `user_id` y `tenant_id` coinciden y `vence` es posterior a ahora.
- Todos los demás casos lanzan la **misma** `AdjuntoNoEncontrado` → 404
  `{"code": "adjunto_no_encontrado"}`, sin extras. Esos casos son: id desconocido, ajeno, de
  otro tenant, vencido, malformado, traversal, symlink que sale del directorio, sidecar
  corrupto o dato borrado entre medio.
- Quien prueba ids no distingue "existe pero no es tuyo" de "no existe".
- Un vencido es 404 en la lectura aunque el limpiador todavía no haya pasado.
- Borrar es seguro frente a lecturas concurrentes. Se hace unlink del sidecar primero y del dato
  después. Un lector que ya abrió el dato lo lee entero (el inodo vive hasta el close); uno que
  llega después recibe 404.
- **Baja de usuario:** `POST /api/admin/users/{id}/baja`, después del commit, borra los
  adjuntos de ese `user_id`. Es best-effort (`# fail-soft:`); el TTL cubre lo que no se pueda
  borrar.
- **Baja que se cruza con una subida en vuelo:** si el sidecar de esa subida se confirma después
  de que la baja recorrió el directorio, ese adjunto sobrevive hasta su TTL. Nadie puede leerlo:
  la lectura exige al dueño, y el dueño ya no tiene sesión.

## 4. Ids

- `secrets.token_urlsafe(24)`: 192 bits, 32 caracteres `[A-Za-z0-9_-]`.
- Se validan con `fullmatch` de alfabeto y largo exactos **antes** de construir una ruta, sin
  tocar el disco.
- Después se resuelve la ruta y se exige que quede dentro del directorio. Con ese alfabeto solo
  un symlink plantado podría sacarla.

## 5. Vencimiento y limpieza

- `vence = creado + JAX_ADJUNTOS_TTL_HORAS`, fijado al subir.
- `start_limpieza_de_adjuntos` es una tarea del lifespan, hermana de `start_owner_file_cleanup`.
  Pasa al arrancar y después cada 15 min.
  - **Ruling R24 del controller (2026-09-17):** no va dentro del bucle de `owner_cleanup`. Ese bucle
    duerme 6 h porque retiene 30 días; con un TTL mínimo de 1 h, un vencido quedaría en disco
    hasta 7 veces su vida.
- Qué borra:
  - vencidos;
  - sidecars corruptos, datos sin sidecar y temporales con más de 6 h (`ORFANO_MAX_SEGUNDOS`).
    El margen cubre la espera en cola de `turno_de_subida`: el temporal toma su mtime antes de
    esa espera. Son 240 subidas de peor caso (60 s de pypdf + 30 s). Un huérfano no tiene
    sidecar, así que nadie lo lee. El `.dato` renombrado refresca su mtime.
- Una entrada rota (un directorio con nombre de adjunto, un archivo sin permiso, EIO) se loguea
  y se saltea. No aborta la pasada para los demás usuarios. Lo mismo vale en la baja.
- Qué deja:
  - lo vigente;
  - lo reciente (una subida en curso);
  - lo que no reconoce.

## 6. Variables de entorno: referencia ÚNICA de deploy

Todas fail-closed: si falta una o está fuera de rango, el lifespan no arranca (antes de abrir la
base). Rangos leídos del código (`adjuntos/limites.py`, `adjuntos/almacen.py`,
`adjuntos/cuota.py`). Valores de deploy: decisión del principal (2026-09-17).

"`int()` > 0" = lo que acepta `int()` de Python (admite espacios alrededor, signo `+` y `_`
entre dígitos) y mayor que 0, sin techo. "Solo dígitos" = regex `[1-9][0-9]{0,N}`: sin
espacios, sin signo y sin ceros a la izquierda.

| # | Variable | Rango exacto (código) | Deploy (`/etc/jax/.env`) |
|---|---|---|---|
| 1 | `JAX_ADJUNTO_MAX_BYTES` | `int()` > 0 | `10485760` |
| 2 | `JAX_ADJUNTO_MAX_CHARS` | `int()` > 0 | `8000` |
| 3 | `JAX_ADJUNTO_MAX_PAGINAS` | `int()` > 0 | `20` |
| 4 | `JAX_ADJUNTO_MAX_POR_MENSAJE` | `int()` > 0 | `1` |
| 5 | `JAX_ADJUNTO_IMAGENES_EN_PROCESO` | `int()` > 0 | `1` |
| 6 | `JAX_ADJUNTO_SUBIDAS_EN_PROCESO` | `int()` > 0 | `1` |
| 7 | `JAX_ADJUNTO_PDF_PROCESOS` | `int()` **1..8** (`LIMITE_PROCESOS_DE_PDF`) | `1` |
| 8 | `JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS` | `int()` **1..60** (`LIMITE_TIMEOUT_DE_PDF_SEGUNDOS`) | `30` |
| 9 | `JAX_ADJUNTOS_DIR` | ruta absoluta; directorio 0700 (sin bits de grupo/otros), escribible por el servicio; se crea 0700 si falta | `/srv/jax-data/adjuntos` (0700, dueño `fruiz`, disco real) |
| 10 | `JAX_ADJUNTOS_TTL_HORAS` | solo dígitos (`[1-9][0-9]{0,3}`), **1..168** | `24` |
| 11 | `JAX_ADJUNTOS_CUOTA_BYTES_USUARIO` | solo dígitos (`[1-9][0-9]{0,15}`), **1048576..1099511627776** (1 MiB..1 TiB) y **≥ `JAX_ADJUNTO_MAX_BYTES`** | `524288000` |
| 12 | `JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES` | solo dígitos (`[1-9][0-9]{0,15}`), **1073741824..1099511627776** (1 GiB..1 TiB) | `53687091200` |

Fuera de `/etc/jax/.env`, en la unidad de systemd de jax-platform: **`TMPDIR=/srv/jax-data/tmp`**
(0700, disco real). Starlette vuelca ahí el cuerpo de cada subida antes del handler (R25), y
`/tmp` es tmpfs en hall9000.

Motivos de los rangos:
- PDF_PROCESOS 1..8: más procesos de pypdf no suman throughput en una instancia y reservan
  memoria de más. PDF_TIMEOUT 1..60: MAX_PAGINAS ya acota el trabajo, y 60 s no deja un worker
  ocupado más de un minuto.
- DIR: una ruta relativa depende del cwd; un directorio abierto a otros no se corrige solo, se
  avisa. TTL: piso 1 h (un adjunto se sube mientras se escribe el mensaje), techo 7 días (es
  entrada de un turno, no un archivo del usuario).
- CUOTA: piso 1 MiB (menos no deja subir una foto de teléfono), techo 1 TiB ("sin límite" con
  más ceros); menor que el tope por archivo rechazaría con el código equivocado archivos que el
  tope admite.
- DISCO_LIBRE: piso 1 GiB (lo demás del filesystem se queda sin aire antes de que la guarda
  corte), techo 1 TiB (un mínimo mayor que el disco es "nunca aceptar" y tiene que verse al
  arrancar).

Tests: `conftest.py` fija 1..8 y la cuota con `setdefault` (rigen los del `.env` si están),
FUERZA `JAX_ADJUNTOS_DIR` a un `mkdtemp` propio y FUERZA el disco libre mínimo a 1073741824
(mide el disco del `mkdtemp`, no el de producción).

## 7. Contrato de `POST /api/chat/upload`

Imagen:
```json
{"id": "…32…", "tipo": "imagen", "nombre": "foto.png", "mime": "image/png", "bytes": 123}
```

Texto o PDF:
```json
{"id": "…32…", "tipo": "texto", "origen": "texto|pdf", "nombre": "i.pdf", "bytes": 10063078,
 "caracteres": 8000, "recortado": true, "vista_previa": "…hasta 200 caracteres…"}
```

- Nunca devuelve base64 ni el texto completo.
- **Ruling R24 del controller (2026-09-17):** devolver una vista previa acotada y no el texto. El texto
  ya está en el servidor, y devolverlo invitaría a reenviarlo en el chat.
- `mime` en la imagen sirve para que la interfaz sepa qué faceta la acepta.
- Errores sin cambio: 413 `adjunto_demasiado_grande` (con `max_bytes`), 415
  `adjunto_tipo_no_permitido`, 422 `adjunto_vacio` / `pdf_ilegible` / `pdf_sin_texto`.
- Código nuevo: 404 `adjunto_no_encontrado` (es/en).
- Códigos nuevos de RD6 (es/en): 413 `adjuntos_cuota_excedida` (con `cuota_bytes`) y 507
  `adjuntos_sin_espacio` (sin números: el estado del disco no es asunto del cliente).

### 7.1 Cuota por usuario y disco libre (RD6, Principal Ruling "quota", 2026-09-17)

RD5 midió ~0,5 GB/s escritos en `JAX_ADJUNTOS_DIR` con `upload_imagen_max` a c=25, retenidos
hasta el TTL. Sin techo, un solo usuario llena el disco. Código: `adjuntos/cuota.py`.

**Orden de los chequeos, todos antes de la copia** (Starlette ya volcó el cuerpo, así que su
tamaño es un hecho medido con `seek`/`tell` en un hilo, no una declaración del cliente):
1. **Tope por archivo** → 413 `adjunto_demasiado_grande`. Sin estado y lo más barato. Va
   primero porque un archivo que no entra ni con la cuota vacía tiene que recibir ese motivo,
   no "cuota excedida" (test `test_max_bytes_se_mira_antes_que_la_cuota`). La copia lo sigue
   cortando igual, como defensa.
2. **Cuota del usuario** → 413 `adjuntos_cuota_excedida`. Depende solo de lo suyo; va antes
   que el disco para que el usuario reciba el motivo que puede resolver él.
3. **Disco libre** → 507 `adjuntos_sin_espacio`. Global, lo último antes de escribir:
   `shutil.disk_usage(JAX_ADJUNTOS_DIR)` en `asyncio.to_thread`; rechaza si, escrito el
   archivo, quedaría menos que el mínimo (`libre - tamaño < mínimo`). Si medir falla
   (cualquier excepción), se rechaza igual con el mismo 507 y se loguea el tipo de la
   excepción (fail-closed).

**Qué cuenta:** la suma del campo `bytes` (lo SUBIDO; de un PDF queda solo el texto, pero cuenta
lo subido) de los adjuntos **vigentes** del usuario, es decir, los que `obtener` le devolvería
(sidecar legible, suyo, sin vencer), más lo **reservado** por sus subidas en vuelo. Un sidecar
corrupto no es de nadie y no cuenta. Clave: `user_id` (PK global, como la baja). Se relee el
directorio en cada chequeo (sin caché: los sidecars son la verdad y no hay nada que invalidar
cuando limpian el limpiador, la baja o el vencimiento).

**Atomicidad:** `cuota.reserva()` toma un `asyncio.Lock` **por user_id**, lee el uso y, si entra,
suma la reserva. `Reserva.confirmar()` vuelve a tomar el candado, relee el disco y confirma con
el tamaño copiado real; escribe el sidecar (el commit) **sin soltar el candado** y recién ahí
convierte la reserva en adjunto. Si la tarea se cancela durante la escritura, espera al hilo
antes de soltar. La reserva se libera al salir por cualquier camino, sin `await`. Dos subidas
del mismo usuario que juntas se pasan: la segunda ve la reserva de la primera y es 413 antes de
copiar (test con la lectura lenta y vigilada: sin candado, las dos reservan).

**Cancelación durante el commit:** el hilo que escribe el sidecar no se puede interrumpir.
`confirmar` sigue esperándolo aunque la tarea se cancele una o muchas veces, y recién cuando
terminó suelta candado y reserva y relanza la cancelación. Consecuencia: **una subida cancelada
en el commit deja el adjunto guardado**. Cuenta en la cuota del usuario hasta vencer (TTL) y el
cliente nunca recibe su id, así que nadie lo puede usar; lo borra el limpiador al vencer. Una
cancelación antes del commit (copia, clasificación, lectura de la cuota) no deja nada contado.

**Por qué un candado en memoria alcanza:** jax-platform es **un solo proceso**.
`auth/rate_limit.py::exigir_un_solo_proceso` aborta el arranque con `--workers` o
`WEB_CONCURRENCY` > 1. Si algún día hay más de un proceso, la cuota necesita un candado
compartido (flock sobre el directorio o la base) antes de subir workers.

Camino de una subida:
1. Starlette ya parseó el multipart. Hasta 1 MB queda en memoria; el resto va a un archivo de
   `TMPDIR`.
2. `copiar_subida` corre en un hilo y copia de a 1 MB a `.subiendo-*`, cortando en
   `max_bytes`.
3. `clasificar_archivo` corre en un hilo. Lee la cabecera para imagen o PDF, y valida el texto
   entero por bloques de 256 KB con un decodificador incremental.
4. pypdf recibe **la ruta** en el pool de RD1.
5. Se guarda.
6. `finally` borra el temporal.

**Ruling R25 del controller (2026-09-17):** Starlette recibe y vuelca el cuerpo entero antes
de llamar al handler, así que el 413 llega después de la ingesta. Lo acota nginx
(`client_max_body_size 50m`). **PENDIENTE (deploy, principal):** `TMPDIR` del unit en disco
real, porque `/tmp` es tmpfs en hall9000.

Pico medido con tracemalloc para 10 MB: parseo ≈ 1,3–1,8 MB y handler ≈ 2,1 MB, contra
10 + 14 + 14 MB de antes.

## 8. Contrato de `POST /api/chat` (RD3, hecho)

```json
{"message": "resumí", "facet": "jax_local", "adjuntos": [{"id": "…32…"}]}
```

- `AdjuntoRef` con `extra=forbid`; el id se valida en el borde con el largo y el alfabeto del
  almacén (`almacen.PATRON_ID`). Un id malformado es **422 de pydantic** y no llega al disco. El
  contrato en línea (`base64`, `contenido`, `tipo`, `nombre`) es 422 `extra_forbidden`.
- Orden en `chat()`, antes de memoria, estado y proveedor:
  1. hyde con adjuntos → 422 `adjuntos_no_soportados`;
  2. tope `JAX_ADJUNTO_MAX_POR_MENSAJE` → 422 `adjuntos_demasiados`, sin tocar el almacén;
  3. `buscar_adjuntos`: el sidecar de cada id (dueño, vigente). Cualquier "no" → 404
     `adjunto_no_encontrado`, el mismo cuerpo, sin decir qué id falló;
  4. si hay imagen: visión contra la faceta resuelta → 422 `imagen_no_soportada`, **antes** de
     leer la imagen. El re-chequeo del dispatch sigue igual;
  5. `leer_adjuntos`: el texto con `almacen.leer` (en un hilo; un dato borrado entre medio es el
     mismo 404) y la imagen con `almacen.leer_imagen_en_base64`.
- **Imagen:** se lee y codifica en un hilo, de a 262.143 bytes, dentro de `turno_de_imagen`
  (`JAX_ADJUNTO_IMAGENES_EN_PROCESO`). Los tramos van al cuerpo del proveedor como
  `http_client.LiteralJsonCrudo`: `CuerpoJsonDeUnUso` los entrega como partes, sin `json.dumps`
  y sin juntarlos. Nunca existe un `str` con el base64.
  - **HISTORIA** (medido 2026-09-17, tic de 1 ms en el loop, 25 imágenes de 10 MB): las 25 a la
    vez atrasan el tic p95 60 ms; de a una, 0,35 ms. Por eso el tope se queda, con este nuevo
    trabajo.
  - Pico por chat con imagen de 10 MB (tracemalloc): 14,1 MB; residual tras el despacho: 0,1 MB.
- **Memoria y logs:** memoria persistente con las líneas `[adjunto nombre=… tipo=… …]` del
  sidecar; historial en RAM con el texto y la línea de la imagen. Ni el base64 ni el id van a
  logs, memoria, historial o bus de estado. Los logs del limpiador y de la baja muestran una
  huella (`id#<12 hex>`), no el id.

## 9. Qué falta

- **RD4 (frontend):** `AttachButton`/`FileAttachment`/`BottomBar` trabajan por id. La vista
  previa de la imagen usa un object URL local (R23-4), y los textos van en i18n es/en. Hasta RD4
  la interfaz manda el contrato en línea y recibe 422: la rama no se mergea en ese estado.
- **RD5 (carga):** health con 5 VUs, solo y junto a `upload_imagen_max`, `upload_pdf_max` y
  `chat_imagen_max` a c=25. Criterio: p95 ≤ 10 ms y RSS acotado. `loadtest/adjuntos.js` (en jax)
  tiene que subir primero y chatear por id.
