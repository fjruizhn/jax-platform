# Pieza 4 de OP 358 · Enganche de verticales (2026-10-27)

**Qué es.** El contrato por el que las salas del hub 2D (pieza 3, catálogo
informativo estático) dejan de ser decoración y se enganchan a los proyectos
reales de la oficina: la sala **activa** (Agencia) abre y filtra los proyectos
de su vertical; las demás salas siguen informativas hasta que su vertical se
active. Cierra MINOR 2 (ver abajo). El resolver canónico
(`authorize_office_in_cursor`) no se toca: quien puede ver un proyecto lo
sigue pudiendo ver exactamente igual; la vertical es un eje de organización,
no de permisos.

## Decisiones de diseño

1. **Registro de verticales en código, no en base de datos.**
   `backend/virtual_office/verticales.py` declara el registro que el servidor
   anuncia; la base solo guarda la asignación por proyecto. Cambiar el estado
   de una vertical (activarla) es un cambio de código revisado, no una fila
   editable en producción — la activación de una vertical es una decisión de
   negocio con consecuencias de producto (la sala pasa a abrir proyectos).
   Orden del registro: junta, agencia, bufete, arquitectura, contable,
   banca, biblioteca. Solo **agencia** está `activa`; el resto
   `proximamente` (junta, bufete, arquitectura, contable, banca) o `futura`
   (biblioteca).
2. **Tabla compañera `office_project_vertical`** (PK `project_id`, FK a
   `office_project`). Ausencia de fila = proyecto general, sin sala. La
   vertical se asigna **solo al crear** (idempotente): no hay endpoint de
   cambio de vertical en v1 porque mover un proyecto entre verticales
   arrastraría preguntas de visibilidad/auditoría que no aportan hoy.
3. **Estados honestos.** `activa` (la sala abre y filtra), `proximamente`
   (sala visible, diálogo informativo), `futura` (visible, etiqueta
   distinta). El endpoint de lista acepta cualquier vertical **registrada**
   como filtro (una `proximamente` devuelve lista vacía — es la verdad), y
   rechaza con 422 `office_vertical_invalid` las desconocidas. Crear con
   vertical no `activa` o desconocida → 422
   `office_project_vertical_invalid`.
4. **Sin endpoints de conteo en v1.** El hub no muestra números de
   proyectos por sala; cuando se pidan, se mide antes de agregarlos (regla
   de rendimiento: nada de COUNT(*) por sala en el camino del listado).

## Contratos de API

- `GET /api/virtual-office/verticales` → `{"verticales": [{id, estado}, …]}`
  en el orden del registro. Exige perfil de Office activo (403 al extraño),
  hereda `no-store` del middleware global.
- `GET /api/virtual-office/projects?vertical=<id>` — filtro por vertical
  registrada; `LEFT JOIN` a la tabla compañera, `vertical` en cada fila
  (null = general). Solo proyectos ya visibles para quien pregunta: el
  predicado de visibilidad es el de siempre.
- `POST /api/virtual-office/projects` acepta `vertical` opcional; se valida
  contra el registro y se persiste en la misma transacción idempotente
  (drift de vertical entre intento y commit → reintento de fase B, igual
  que el drift existente de grupo). La firma de idempotencia incluye la
  vertical: cambiar de sala en el formulario es otro intento, otra clave.
- Externos: la publicación externa sigue anulando `description` pero
  **conserva** la vertical — el eje no filtra información sensible.
- Migración runtime `office-v13c` (DDL idempotente) + privilegio INSERT
  para el rol web en `runtime-table-privileges-v2.txt`.

## Frontend

- La página pide el registro al montar **en ambas instalaciones** (Axioma
  lo ignora para el hub; un 403 del registro en instalación de oficina
  dispara el `onOfficeDenied` de siempre). Error del registro → tarjeta
  `role="alert"`, el listado de proyectos sigue funcionando.
- `HubOficina` ya no recibe catálogo local: renderiza **solo** salas cuyo
  id esté en el registro del servidor **y** en sus mapas VISUAL/i18n
  locales. Un backend que corre por delante del frontend nunca rompe el
  hub (seguridad de orden de despliegue).
- Sala activa → filtra el listado (`?vertical=`), etiqueta «Filtrado por
  sala:» con botón «Quitar filtro» (`TAMANO_BOTON_ACCION`, política de
  24px). Diálogo informativo para las demás. El creador de proyectos
  ofrece el selector de sala solo con verticales (solo `activa` se ofrece)
  y la preselección respeta el filtro activo.
- La firma de idempotencia del creador incluye la vertical cuando se elige
  sala; sin sala, el payload no lleva la clave (compatibilidad exacta con
  los tests existentes de creación).

## MINOR 2 (declaración medida, cierre)

MINOR 2 = el CSS de las salas (`index.css`) y `public/salas/agencia.jpg`
viven en el build compartido y por tanto viajan también en el bundle de la
instalación Axioma. **Decisión medida y consciente:** un solo artefacto,
dos instalaciones; son ~43 KB de imagen y unas clases CSS muertas en
Axioma. No se introduce carga condicional ni scoping de rutas públicas:
el costo de complejidad supera el beneficio medido (no afecta LCP de la
instalación Axioma: la imagen no se referencia fuera del hub). Reevaluar
solo si el catálogo de imágenes crece.

## Pruebas

- **Backend** `tests/test_office_verticales_api.py` (7 pruebas): registro
  anunciado en orden con una sola activa, 403 al extraño, asignación al
  crear + exposición en detalle/lista/replay idempotente, ausencia =
  general, 422 por vertical inválida al crear, filtro de lista (incluye
  vacía honesta para `proximamente` y 422 para desconocida), y externo con
  descripción anulada y vertical conservada. La suite con DB corre en CI
  (`backend-tests-con-db`, MariaDB efímera): en hall9000 el guard
  fail-closed de `base_de_test.py` impide ejecutarla contra 3306/3308
  (puertos de producción) y no hay docker local — por diseño, no se
  sortea.
- **Frontend** 1553/1553 (piso 1545 → 1553 en `policy.yml`). +1
  HubOficina reescrito sobre registro (incluye sala desconocida que nunca
  se renderiza), +4 OfficeProjectCreator (sala en el creador, guard de
  preselección, firma con vertical), +3 VirtualOffice.oficina (filtro +
  quitar, vacío honesto, error del registro no tumba el listado). Las
  suites de rutas/raíz/piel existentes se actualizaron para mockear
  `officeVerticalesApi` (la página ahora lo llama al montar) y el test de
  piel espera el hub con `findByRole` porque la sala es asíncrona.

## Pendientes de las piezas que siguen

- Pieza 5 (admin completa + worker SMTP) y pieza 6 (temas por instalación)
  según el roadmap de OP 358.
- Activar la siguiente vertical = cambio de un estado en el registro +
  su sala ya existente en VISUAL/i18n; sin migración nueva.
- Cuando Fernando quiera números por sala: medir antes de agregar (ver
  «Sin endpoints de conteo»).
