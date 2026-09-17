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
  - **DECISIÓN de controlador (RD2):** no va dentro del bucle de `owner_cleanup`. Ese bucle
    duerme 6 h porque retiene 30 días; con un TTL mínimo de 1 h, un vencido quedaría en disco
    hasta 7 veces su vida.
- Qué borra:
  - vencidos;
  - sidecars corruptos, datos sin sidecar y temporales con más de 1 h (`ORFANO_MAX_SEGUNDOS`).
- Qué deja:
  - lo vigente;
  - lo reciente (una subida en curso);
  - lo que no reconoce.

## 6. Variables de entorno (fail-closed; el lifespan no arranca sin ellas)

| Variable | Rango | Motivo |
|---|---|---|
| `JAX_ADJUNTOS_DIR` | ruta absoluta; directorio 0700 (sin bits de grupo/otros), escribible por el servicio; se crea 0700 si falta | Una ruta relativa depende del cwd. Un directorio abierto a otros no se corrige solo: se avisa |
| `JAX_ADJUNTOS_TTL_HORAS` | entero **1..168** | Piso: un adjunto se sube mientras se escribe el mensaje. Techo: 7 días; es entrada de un turno, no un archivo del usuario |

**PENDIENTE (deploy, principal):** agregar las dos líneas a `/etc/jax/.env` y crear el
directorio 0700, propiedad del usuario del servicio, en un disco real (no tmpfs). Ejemplo:
`/srv/jax-data/adjuntos`. Los tests fuerzan un `mkdtemp` propio (`conftest.py`).

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
- **DECISIÓN de controlador (RD2):** devolver una vista previa acotada y no el texto. El texto
  ya está en el servidor, y devolverlo invitaría a reenviarlo en el chat.
- `mime` en la imagen sirve para que la interfaz sepa qué faceta la acepta.
- Errores sin cambio: 413 `adjunto_demasiado_grande` (con `max_bytes`), 415
  `adjunto_tipo_no_permitido`, 422 `adjunto_vacio` / `pdf_ilegible` / `pdf_sin_texto`.
- Código nuevo: 404 `adjunto_no_encontrado` (es/en).

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

Pico medido con tracemalloc para 10 MB: parseo ≈ 1,3–1,8 MB y handler ≈ 2,1 MB, contra
10 + 14 + 14 MB de antes.

## 8. Qué cambia después

- **RD3 (chat):** `/api/chat` recibe `adjuntos: [{"id": …}]` (`extra=forbid`) y deja de
  aceptar el contrato en línea (base64/contenido). Además:
  - lee con `almacen.leer` (to_thread, dueño);
  - aplica el tope por mensaje;
  - un id ajeno o vencido da el 404 de arriba;
  - empalma el base64 en el cuerpo del proveedor.

  Hasta RD3 el contrato en línea sigue vivo y probado. La interfaz actual espera
  `base64`/`contenido` en la respuesta de subida, así que queda rota entre RD2 y RD4: la rama no
  se mergea en ese estado.
- **RD4 (frontend):** `AttachButton`/`FileAttachment`/`BottomBar` trabajan por id. La vista
  previa de la imagen usa un object URL local (R23-4), y los textos van en i18n es/en.
- **RD5 (carga):** health con 5 VUs, solo y junto a `upload_imagen_max`, `upload_pdf_max` y
  `chat_imagen_max` a c=25. Criterio: p95 ≤ 10 ms y RSS acotado.
