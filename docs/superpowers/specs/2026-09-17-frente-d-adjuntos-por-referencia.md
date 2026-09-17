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

`JAX_ADJUNTOS_DIR` (absoluto, 0700) tiene **una carpeta por usuario**,
`JAX_ADJUNTOS_DIR/<user_id>/` (0700). Esto es de RD7 (decisión del principal, 2026-09-17).
- **Solo `user_id`, sin tenant:** `user_id` es la PK global de `jax_users`, y la baja ya borraba
  por `user_id`. El sidecar sigue guardando `tenant_id`, y la búsqueda sigue exigiendo los dos.
- **La carpeta no es el control de acceso:** sirve para que la cuota y la búsqueda sean
  O(archivos del usuario). Quién es el dueño lo sigue decidiendo el sidecar; un sidecar ajeno
  copiado a la carpeta de otro sigue siendo 404 para ese otro.
- **El `user_id` se valida** (`[1-9][0-9]{0,19}`) antes de armar una ruta, aunque venga del JWT
  firmado.
  - Al escribir, uno malformado es `UsuarioInvalido` y no se crea nada.
  - Al leer, es el 404 de siempre.
- **La carpeta tiene que ser un directorio propio:** no un symlink y colgando directo de la
  raíz. Si no lo es, leer es 404 y escribir falla.
- **Sin migración:** el almacén es nuevo y no se desplegó con la disposición plana, así que no
  hay nada que migrar.

Dentro de cada carpeta:

| Archivo | Contenido |
|---|---|
| `<id>.dato` | imagen: bytes crudos. texto/PDF: texto UTF-8 extraído, ya recortado a `JAX_ADJUNTO_MAX_CHARS` |
| `<id>.json` | sidecar: `id, user_id, tenant_id, tipo, origen, mime, nombre, bytes, caracteres, recortado, creado, vence` |
| `.subiendo-<hex>` | temporal de una subida en curso |
| `.tmp-<hex>` | temporal de una escritura atómica |

- Archivos 0600. Escritura atómica: temporal en la misma carpeta, fsync, `os.replace`. El
  temporal de la subida también vive en la carpeta del usuario, así que el rename de la imagen
  queda en el mismo filesystem.
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

- `almacen.obtener(id, user)` / `almacen.leer(id, user)` buscan **solo en la carpeta del que
  pide** (RD7). Nunca listan ni abren la de otro; lo fija un test que graba `scandir`, `open` y
  `stat`. Devuelven el adjunto solo si `user_id` y `tenant_id` del sidecar coinciden y `vence`
  es posterior a ahora.
- Todos los demás casos lanzan la **misma** `AdjuntoNoEncontrado` → 404
  `{"code": "adjunto_no_encontrado"}`, sin extras. Esos casos son: id desconocido, ajeno, de
  otro tenant, vencido, malformado, traversal, symlink que sale del directorio, sidecar
  corrupto o dato borrado entre medio.
- Quien prueba ids no distingue "existe pero no es tuyo" de "no existe".
- Un vencido es 404 en la lectura aunque el limpiador todavía no haya pasado.
- Borrar es seguro frente a lecturas concurrentes. Se hace unlink del sidecar primero y del dato
  después. Un lector que ya abrió el dato lo lee entero (el inodo vive hasta el close); uno que
  llega después recibe 404.
- **Baja de usuario:** `POST /api/admin/users/{id}/baja`, después del commit, borra la carpeta
  de ese `user_id`: sidecars primero, después datos y temporales, y al final la carpeta. Es best-effort (`# fail-soft:`); el TTL cubre lo que no se pueda
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
- **Recorre las carpetas (RD7).** Una carpeta ilegible se saltea y se loguea.
- **Borra la carpeta que quedó vacía** con `os.rmdir`, que es atómico y falla si hay algo
  adentro. Esto no pisa una subida en curso:
  - Mientras una subida vive, su temporal está dentro de la carpeta, así que no está vacía.
  - La subida que preparó su carpeta y todavía no abrió nada la recrea (0700) al abrir su primer
    archivo (`_crear_exclusivo`, hasta 3 intentos).
  - En la raíz solo se tocan temporales viejos. Un archivo suelto con forma de la disposición plana
    anterior (`<id>.json`/`<id>.dato` en la raíz) se **ignora**: esa disposición nunca se
    desplegó, así que no hay nada que limpiar ahí.
  - Si el limpiador borra la carpeta entre el `mkdir` de la subida y su chequeo, o entre el
    chequeo y el primer `open`, la subida reintenta el tramo entero (3 intentos). Si pierde los
    3, responde 503 `adjuntos_reintentar` (es/en), nunca un 500.
- **La baja no sigue symlinks:** antes de listar, `borrar_de_usuario` hace el mismo chequeo de
  carpeta propia que la lectura. Una carpeta `5 -> 6` plantada no borra los archivos del 6.
  - **Ruling R24 del controller (2026-09-17):** no va dentro del bucle de `owner_cleanup`. Ese bucle
    duerme 6 h porque retiene 30 días; con un TTL mínimo de 1 h, un vencido quedaría en disco
    hasta 7 veces su vida.
- Qué borra:
  - vencidos;
  - sidecars corruptos, datos sin sidecar y temporales con más de 6 h (`ORFANO_MAX_SEGUNDOS`).
    El margen cubre la espera en cola de `turno_de_subida` y, para un PDF, de `turno_de_pdf`: el
    temporal toma su mtime antes de esas esperas. Con los dos topes en su piso (1), el drenaje más
    lento, son 240 subidas de peor caso delante (30 s de clasificación + 60 s de pypdf). Los
    techos (SUBIDAS 4, PDF 8) solo drenan más rápido y no achican el margen. Un huérfano no tiene
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
| 5 | `JAX_ADJUNTO_IMAGENES_EN_PROCESO` | `int()` **1..4** (`LIMITE_IMAGENES_EN_PROCESO`) | `1` |
| 6 | `JAX_ADJUNTO_SUBIDAS_EN_PROCESO` | `int()` **1..4** (`LIMITE_SUBIDAS_EN_PROCESO`) | `1` |
| 7 | `JAX_ADJUNTO_PDF_PROCESOS` | `int()` **1..8** (`LIMITE_PROCESOS_DE_PDF`) | `1` |
| 8 | `JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS` | `int()` **1..60** (`LIMITE_TIMEOUT_DE_PDF_SEGUNDOS`) | `30` |
| 9 | `JAX_ADJUNTOS_DIR` | ruta absoluta; directorio 0700 (sin bits de grupo/otros), escribible por el servicio; se crea 0700 si falta | `/srv/jax-data/adjuntos` (0700, dueño `fruiz`, disco real) |
| 10 | `JAX_ADJUNTOS_TTL_HORAS` | solo dígitos (`[1-9][0-9]{0,3}`), **1..168** | `24` |
| 11 | `JAX_ADJUNTOS_CUOTA_BYTES_USUARIO` | solo dígitos (`[1-9][0-9]{0,15}`), **1048576..1099511627776** (1 MiB..1 TiB) y **≥ `JAX_ADJUNTO_MAX_BYTES`** | `524288000` |
| 12 | `JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES` | solo dígitos (`[1-9][0-9]{0,15}`), **1073741824..1099511627776** (1 GiB..1 TiB) | `53687091200` |
| 13 | `JAX_ADJUNTOS_SUBIDAS_POR_MINUTO` (RD7) | solo dígitos (`[1-9][0-9]{0,2}`), **1..600**; ventana deslizante fija de 60 s | `30` |
| 14 | `JAX_ADJUNTOS_RECHAZO_ESPERA_MS` (RD7 fix round; rige el 401 y el 429 del middleware) | solo dígitos (`0\|[1-9][0-9]{0,3}`), **0..5000** (0 permitido) | `1000` |

Fuera de `/etc/jax/.env`, en la unidad de systemd de jax-platform: **`TMPDIR=/srv/jax-data/tmp`**
(0700, disco real). Starlette vuelca ahí el cuerpo de cada subida antes del handler (R25), y
`/tmp` es tmpfs en hall9000.

Motivos de los rangos:
- IMAGENES_EN_PROCESO y SUBIDAS_EN_PROCESO 1..4 (Final fix wave #2, 2026-09-17): acotan trabajo que
  corre en un hilo del proceso web reteniendo el GIL por tramo (base64 de la imagen; validar el
  texto de la subida). Más lugares no suman núcleos, suman contienda con el event loop. Medido:
  25 a la vez atrasan el tic del loop p95 60 ms (imágenes) y dan health p95 65 ms (subidas con
  base64, §1); una sola, 0,35 ms. A ~2,5 ms por lugar, 4 rondan el criterio de 10 ms. Es una
  extrapolación lineal de esos dos puntos, no una medición de 4: el deploy sigue en 1 (medido en
  RD5) y el techo solo impide que un dígito de más pase en silencio. SUBIDAS_EN_PROCESO acota solo
  la clasificación: pypdf espera su propio turno, del tamaño de PDF_PROCESOS (§7).
- PDF_PROCESOS 1..8: más procesos de pypdf no suman throughput en una instancia y reservan
  memoria de más. PDF_TIMEOUT 1..60: MAX_PAGINAS ya acota el trabajo, y 60 s no deja un worker
  ocupado más de un minuto.
- DIR: una ruta relativa depende del cwd; un directorio abierto a otros no se corrige solo, se
  avisa. TTL: piso 1 h (un adjunto se sube mientras se escribe el mensaje), techo 7 días (es
  entrada de un turno, no un archivo del usuario).
- CUOTA: piso 1 MiB (menos no deja subir una foto de teléfono), techo 1 TiB ("sin límite" con
  más ceros); menor que el tope por archivo rechazaría con el código equivocado archivos que el
  tope admite.
- RECHAZO_ESPERA_MS: 0 está permitido, porque apagar la espera es válido si nginx (`limit_req`) ya
  frena antes. Su costo está medido (§7.2): health p95 37–40 ms con 25 clientes que reintentan
  sin pausa. Techo 5000 ms: cada rechazo en espera retiene un socket y una corrutina; esperar más
  no frena más a un cliente que reintenta, y acerca la espera a los timeouts de clientes y
  proxies.
- SUBIDAS_POR_MINUTO: piso 1 (0 es apagar la función, no un límite). Techo 600 (10/s): una
  subida de imagen de 10 MB tarda p50 ~0,22–0,27 s en staging, así que un cliente sin freno hace
  ~4/s. Propuesta de 30, medida en RD7:
  - la interfaz manda 1 adjunto por mensaje (`MAX_POR_MENSAJE=1`);
  - con 30/min un usuario ingresa como mucho 300 MB/min, contra ~0,5 GB/s sin límite (RD5), y
    tarda ≥ 100 s en llenar la cuota de 500 MB;
  - bajo flood, health p95 queda en 0,30–0,32 ms.
- DISCO_LIBRE: piso 1 GiB (lo demás del filesystem se queda sin aire antes de que la guarda
  corte), techo 1 TiB (un mínimo mayor que el disco es "nunca aceptar" y tiene que verse al
  arrancar).

Tests: `conftest.py` fija 1..8 y la cuota con `setdefault` (rigen los del `.env` si están),
FUERZA `JAX_ADJUNTOS_DIR` a un `mkdtemp` propio, FUERZA el disco libre mínimo a 1073741824
(mide el disco del `mkdtemp`, no el de producción) y FUERZA `JAX_ADJUNTOS_SUBIDAS_POR_MINUTO` a
600 (los tests de HTTP suben varias veces por minuto con pocos usuarios) y FUERZA
`JAX_ADJUNTOS_RECHAZO_ESPERA_MS` a 0 (los tests de la espera sustituyen `_dormir`).

**Deploy (principal), además de las 14 líneas:** `limit_req` de nginx para
`/api/chat/upload`. Cómo nginx retransmite el 429 y el 401 retenidos (Ruling R30: los dos esperan
`JAX_ADJUNTOS_RECHAZO_ESPERA_MS`) se mide en el deploy.

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
- Código nuevo de RD7 (es/en): 429 `adjuntos_subidas_limite` (con `retry_after`) y cabecera
  `Retry-After` (§7.2).

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

**Plazo del commit (Ruling R29):** esperar la escritura no tiene que retener el candado para
siempre si el disco se cuelga.
- **Plazo:** `max(60 s, JAX_ADJUNTO_MAX_BYTES / 256 KiB/s)`, que da 60 s con el tope de
  producción. Sin variable nueva: sale del tope por archivo y de un disco degradado declarado en
  `cuota.py`.
- **Vencido el plazo:**
  - se sueltan reserva y candado;
  - se loguea `TimeoutError` sin id;
  - la subida recibe 503 `adjuntos_reintentar`.
- **Aceptado: una escritura que termina después del plazo puede pasar la cuota por un archivo
  por cada vencimiento de plazo.** El adjunto queda sin id conocido, y una subida siguiente del
  mismo usuario puede no haberlo contado; si el plazo vence varias veces, el exceso es de un
  archivo por vez. Ese exceso dura hasta que lo borran el limpiador o el TTL. Se acepta a cambio de no
  dejar al usuario sin subir hasta reiniciar.
- La cancelación repetida sigue esperando, pero dentro del plazo.

**Plazo de la lectura del uso (Final fix wave #2, 2026-09-17):** `almacen.uso_de_usuario` también
corre bajo el candado del usuario, en la reserva y en la confirmación. Colgada, retenía el candado
igual que el commit antes de R29.
- **Plazo:** 60 s (`PLAZO_DE_LECTURA_DE_USO_SEGUNDOS`, sin variable nueva). RD6 midió ~6 µs por
  sidecar; con 30 subidas/min y TTL de 168 h un usuario junta como mucho 302.400 sidecars (~1,8 s),
  y con el techo de 600/min, ~36 s.
- **Vencido:** se loguea `TimeoutError`, se sueltan candado y reserva, y la subida recibe 503
  `adjuntos_reintentar`. A diferencia del commit, no se espera al hilo: es una lectura y no deja
  nada a medio escribir.

**Disco trabado de forma persistente (costo declarado):** los dos plazos sueltan el candado del
usuario, pero no el hilo. Cada subida que vence deja un hilo abandonado en el pool por defecto de
`asyncio.to_thread` (`min(32, núcleos + 4)` hilos) hasta que el disco responda. Si el disco no
vuelve, esos hilos se acumulan y las siguientes operaciones de `to_thread` del proceso esperan
detrás. El borrado del temporal en el `finally` de la subida también corre en un hilo y puede
quedar bloqueado en el mismo disco. El candado de cada usuario sí se suelta.

**Por qué un candado en memoria alcanza:** jax-platform es **un solo proceso**.
`auth/rate_limit.py::exigir_un_solo_proceso` aborta el arranque con `--workers` o
`WEB_CONCURRENCY` > 1. Si algún día hay más de un proceso, la cuota necesita un candado
compartido (flock sobre el directorio o la base) antes de subir workers.

### 7.2 Límite de subidas por usuario (RD7, decisión del principal, 2026-09-17)

La cuota acota lo **guardado**, no lo **recibido**. En RD6, un usuario sobre su cuota seguía
mandando cuerpos de 10 MB, y Starlette los volcaba antes del 413: 23–25 volcados en vuelo, hasta
166 MB, y health p95 de ~1 a ~2,7 ms. Código: `adjuntos/limite_de_subidas.py`.

**Dónde corre, y por qué no es un `Depends`.**
- FastAPI 0.139 (`fastapi/routing.py::get_request_handler`) hace `await request.form()` antes
  de `solve_dependencies`. Ese `form()` es python-multipart parseando el cuerpo entero y
  Starlette volcando el archivo.
- Un `Depends`, `get_current_user` incluido, llega con el cuerpo ya leído. Lo fija
  `test_fastapi_lee_el_multipart_entero_antes_de_resolver_las_dependencias`.
- Por eso es un **middleware ASGI puro** (`LimiteDeSubidas`), montado **dentro** de
  `CORSMiddleware`, para que el 429 lleve sus cabeceras. Solo actúa en `POST /api/chat/upload`:
  1. Lee `Authorization` y verifica firma y vencimiento del JWT de acceso con `decode_token`
     (HS256, sin base).
  2. Pasa la clave `user_id` por `SlidingWindowLimiter`, la misma clase del login y del SMTP,
     con 60 s de ventana. Un intento rechazado no cuenta.
  3. Si el usuario se pasó, **espera `JAX_ADJUNTOS_RECHAZO_ESPERA_MS`** (deploy 1000) y responde
     429 **sin llamar a `receive()`**.
- **Sin token de acceso válido (Ruling R28, enmendado por R30):** el middleware responde,
  después de la misma espera `JAX_ADJUNTOS_RECHAZO_ESPERA_MS` que el 429, **exactamente el 401 que
  daría la ruta** (mismo status, cuerpo y cabeceras, incluido `WWW-Authenticate:
  Bearer` cuando corresponde), sin leer el cuerpo y sin gastar cupo. Cubre estos casos: sin
  cabecera, otro esquema, sin credenciales, firma inválida, vencido, token de refresh, y
  `user_id`/`tv` que no son enteros. Reusa `auth.middleware.bearer`, `decode_token`,
  `auth.middleware.validar_payload` (la parte de `verificar_sesion` que no mira la base) y el
  manejador de `HTTPException` de FastAPI. Los tests comparan contra la ruta sin middleware.
- **Aceptado: la autenticación va primero.** Un cuerpo multipart roto **y** sin token ahora es 401
  y no el 400 de parseo.
- **Riesgo aceptado (principal):** un token con firma válida pasa a la ruta sin mirar la base.
  Un token revocado pero no vencido (≤ 15 min) solo gasta el cupo de **su dueño** en el
  middleware; la autenticación de la ruta, con base, lo sigue rechazando con 401.
- **El límite cuenta intentos**, también los que la ruta rechaza después (401 por revocación,
  413 de cuota o de tamaño, 415, 422). Solo gastan el cupo del que llama (test
  `test_cuota_y_limite_juntos_los_rechazos_de_la_ruta_gastan_cupo`).
- **Estado en memoria**, un proceso (`exigir_un_solo_proceso`), hasta 20.000 claves (LRU). El
  limitador se rehace si cambia el valor configurado.

**¿Un rechazado sigue volcando su cuerpo?**
- **No.** Medido en staging: 0 fds en `TMPDIR` durante el flood, contra 23–25 en RD6.
- Pero los bytes que el cliente ya manda no desaparecen. Después del 429, uvicorn 0.51 (keep-alive)
  **lee y descarta** el resto del cuerpo en el event loop.
- Por eso el freno: mientras frena, uvicorn lee hasta 64 KB y pausa la lectura, y TCP frena al
  cliente.
- Medido con `upload_imagen_max` a c=25, un usuario y 10 MB:

| Variante | Rechazos/s | health p95 | Clientes que no ven el 429 |
|---|---|---|---|
| 429 inmediato | ~540 | **37,5 / 40,1 ms** | 0 |
| 429 inmediato + `Connection: close` | ~800 | 6,2 ms | **19 %** (reset) |
| freno 0,25 s | ~97 | 0,37 ms | 0 |
| **freno 1 s (elegido)** | ~25 | **0,32 / 0,30 ms** | 0 |

- Se elige 1 s por margen: descarta 4 veces menos bytes que 0,25 s con los mismos atacantes.
- Por decisión del principal, la espera es la variable `JAX_ADJUNTOS_RECHAZO_ESPERA_MS` (deploy 1000).
- **Costo declarado de las esperas concurrentes:** cada rechazo en espera retiene un socket y una
  corrutina durante la espera. No hay tope propio; lo acotan las conexiones que acepta el
  proceso y, en producción, nginx (conexiones y `limit_req`).
- **Medido en el fix round** (`2b80433`, un usuario, c=25): health p95 0,29 ms, 750 × 429 y 0 fds
  en `TMPDIR`.
- **La espera vale para los dos rechazos del middleware (Ruling R30):** el 429 del límite y el
  401 sin token de acceso válido. Por qué, medido con un flood anónimo a c=25 y 10 MB por pedido:
  - con el 401 inmediato (`2b80433`): 16.904 × 401 en 30 s, 0 fds en `TMPDIR`, pero **health p95
    36,0 ms**. Es el mismo mecanismo que el 429 sin espera: uvicorn descarta en el loop el resto
    de cada cuerpo;
  - con la espera de 1000 ms (`610f1ed`): 750 × 401 (p95 1022 ms), 0 fds en `TMPDIR`, **health
    p95 0,29 ms**.

**Producción (nginx):** delante está nginx con `client_max_body_size 50m` y, por defecto,
`proxy_request_buffering on`. nginx recibe el cuerpo entero del cliente antes de hablar con
uvicorn.
- Este límite protege el event loop, `TMPDIR` y el disco de adjuntos, no el ancho de banda ni el
  disco temporal de nginx.
- **Paso de deploy (principal):** `limit_req` de nginx para `/api/chat/upload`.
- **Se mide en el deploy:** cómo nginx retransmite el 429 y el 401, los dos retenidos (R30).

Camino de una subida:
0. (RD7) `LimiteDeSubidas`, antes de leer el cuerpo.
1. Starlette ya parseó el multipart. Hasta 1 MB queda en memoria; el resto va a un archivo de
   `TMPDIR`.
2. `copiar_subida` corre en un hilo y copia de a 1 MB a `.subiendo-*`, cortando en
   `max_bytes`.
3. `clasificar_archivo` corre en un hilo. Lee la cabecera para imagen o PDF, y valida el texto
   entero por bloques de 256 KB con un decodificador incremental.
4. Con el turno de subida ya suelto, pypdf espera `turno_de_pdf` (un semáforo del tamaño de
   `JAX_ADJUNTO_PDF_PROCESOS`, tomado antes del submit) y recibe **la ruta** en el pool de RD1. El
   timeout mide solo la corrida (Final fix wave #2, I1; §9.4).
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

## 9. Mediciones de carga

Todas en staging aislado (receta de `rd5-report.md`): `env -i`, `JAX_DB_NAME=jax_memory_test`,
uvicorn en 127.0.0.1:18080 recién arrancado por corrida, `TMPDIR` y `JAX_ADJUNTOS_DIR` en disco
real 0700, sin claves de proveedores, `ss -s` muestreado cada 5 s con aborto sobre 10.000
TIME-WAIT, conexiones a 3308 acotadas por el pool, ningún puerto de producción. Health = 5 VUs
contra `/api/health` durante 26 s, junto a la carga a c=25 durante 30 s. **Criterio: health p95
≤ 10 ms.** Tiempos en ms.

### 9.1 RD5 (2026-09-17, jax-platform `8491eac`; jax `71eb762`)

Por referencia, un usuario sin límite ni cuota (anteriores a RD6/RD7), `SUBIDAS_EN_PROCESO=1`,
`PDF_PROCESOS=1`, `IMAGENES_EN_PROCESO=1`. Dos corridas por fila.

| Corrida | health p95 | Carga p95 | RSS máx. |
|---|---|---|---|
| health solo | **0,3 / 0,3** | — | 86 / 86 MB |
| con `upload_imagen_max` c=25 | **1,2 / 0,4** | 2207 / 2498 | 177 / 172 MB |
| con `upload_pdf_max` c=25 | **0,4 / 0,4** | 3899 / 3831 | 138 / 127 MB (hijos pypdf 215 / 224 MB) |
| con `chat_imagen_max` c=25 | **0,9 / 1,3** | 1535 / 1517 | 137 / 142 MB |

- **Chat por id:** p95 191 ms a c=10 y 1516–1535 ms a c=25 (0 % de errores; el stub recibió cada
  imagen entera). Contra el contrato en línea: 274 ms a c=10 y 911–1153 ms a c=25; a c=25 es el
  costo de `IMAGENES_EN_PROCESO=1`, a cambio de health p95 3,1–3,7 → ~1 ms.
- **RSS:** máximo 177 MB bajo carga (en línea: 865–1514 MB).
- **500 chats secuenciales** sobre el mismo id: +11 MB (en línea: +48 MB), sin tendencia lineal
  (93 MB a los 25, 95 a los 225, 98 a los 475); p95 23,5 ms; 0 respuestas malas.
- **TMPDIR:** Starlette vuelca cada subida ahí antes del handler: hasta 25 fds abiertos y
  250 MB (imágenes) / 240 MB (PDFs) a c=25, archivos anónimos (0 entradas visibles). Se vacía a
  los 3 s. En tmpfs serían RAM fuera del RSS: por eso `TMPDIR` va a disco real (§6).
- **Disco:** `upload_imagen_max` a c=25 escribió 14,0 GB en 30 s en `JAX_ADJUNTOS_DIR` sin
  cuota. Motivó la cuota y el límite por usuario.

### 9.2 Floods (RD6, RD7 y Ruling R30, 2026-09-17)

Un usuario (un token) salvo el anónimo, imágenes de 10 MB, c=25.

| Flood | Commit | Respuestas | health p95 |
|---|---|---|---|
| Corte de cuota (300 MiB, sin límite por minuto) | `c88fc44` | 30 × 200, luego 4009 / 3753 × 413 `adjuntos_cuota_excedida` | **2,63 / 2,84** |
| Límite por minuto, 429 inmediato | `9d7718c` | 30 × 200, 16.252 / 15.849 × 429 | 37,5 / 40,1 (no cumple) |
| Límite por minuto, 429 retenido 1 s | `9d80953` | 30 × 200, 750 / 750 × 429 | **0,32 / 0,30** |
| Límite por minuto, fix round | `2b80433` | 30 × 200, 750 × 429 | **0,29** |
| Anónimo, 401 inmediato | `2b80433` | 16.904 × 401 | 36,0 (no cumple) |
| Anónimo, 401 retenido 1 s (R30) | `610f1ed` | 750 × 401 (p95 1022) | **0,29** |

En el corte de cuota, el disco se quedó en 300 MiB desde el primer segundo y Starlette siguió
volcando 23–25 cuerpos en `TMPDIR`: eso motivó el límite en el middleware (§7.2), que no lee el
cuerpo (0 fds en `TMPDIR` en todos los floods con límite o sin token).

### 9.3 Final fix wave #2 (2026-09-17, jax-platform `a8cff58`; jax `eab3483`, `2a04d76` en cuota-2)

Mismo recipe, con el `loadtest/adjuntos.js` commiteado. Perfil de deploy (`SUBIDAS_POR_MINUTO=30`,
cuota 500 MB, `RECHAZO_ESPERA_MS=1000`) salvo donde se indica.

| Corrida | health p50 / p95 / p99 | Carga | RSS máx. |
|---|---|---|---|
| solo-1 / solo-2 | 0,23 / **0,28** / 0,35 · 0,23 / **0,29** / 0,36 | — | 86 MB |
| pdf-1 / pdf-2: `upload_pdf_max` c=25 (perfil throughput: 600/min, cuota 1 TiB) | 0,26 / **0,43** / 0,59 · 0,26 / **0,40** / 0,53 | 231 / 232 × 200, 0 % errores, p95 4044 / 4129 | 139 / 144 MB (hijo pypdf 223 MB) |
| pdf-cola: igual, con `SUBIDAS_EN_PROCESO=4` y **`PDF_TIMEOUT_SEGUNDOS=1`** | 0,26 / **0,40** / 0,55 | 229 × 200, 0 % errores, 0 líneas de timeout ni reciclado | 128 MB |
| flood-1 / flood-2: `flood_limite` c=25 | 0,24 / **0,30** / 0,58 · 0,24 / **0,30** / 0,55 | 30 × 200 y 750 × 429 retenidos (p95 1004 / 1009) | 123 / 130 MB |
| anon-1: `flood_anonimo` c=25 | 0,24 / **0,30** / 0,57 | 750 × 401 retenidos | 94 MB |
| cuota-2: `flood_cuota` c=25 (perfil cuota: 600/min, 300 MiB) | 0,24 / **0,31** / 1,51 | 30 × 200, 570 × 413, 650 × 429 | 121 MB |

- Todas las corridas: 0 violaciones de red, 0 abortos, TIME-WAIT máx. 1062, conexiones a 3308 ≤ 5,
  `TMPDIR` vacío a los 3 s.
- **pdf-cola:** 25 subidas esperan el turno de pdf con un worker y timeout de 1 s. Cada
  extracción dura ~0,15 s y la cola entera ~3,75 s, así que antes del arreglo la espera se habría
  contado en el timeout. Con el turno tomado antes del submit, ninguna venció.
- **cuota-2:** el límite por minuto cuenta también los 413 (§7.2). Con 600/min, después de 600
  intentos el resto es 429.

### 9.4 Qué cambió en el Final fix wave #2 y sigue pendiente

- **I1:** el timeout de pypdf medía también la espera en la cola del pool (1 worker, timeout 2 s,
  dos extracciones válidas de 1,5 s: la segunda salía `pdf_ilegible:timeout` y el reciclado mataba
  a la primera), y `turno_de_subida` quedaba tomado durante pypdf. Ahora `turno_de_pdf` se toma
  antes del submit y el turno de subida se suelta al terminar de clasificar. Una cancelación real
  con el PDF ya corriendo retiene el lugar hasta que el worker termine o venza su presupuesto
  (entonces recicla, igual que sin cancelación).
- **Pendiente de deploy (principal):** `TMPDIR` del unit en disco real, `limit_req` de nginx y
  medir cómo nginx retransmite el 401 y el 429 retenidos.
- **No medido:** el chat con un proveedor real lento (retención de ~13,4 MB de base64 por chat en
  vuelo; estimado ~335 MB a c=25) y `TMPDIR` en tmpfs.
