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
- Verificación local completa sin DB: backend `2362 passed, 1479 skipped`; frontend `1390 passed, 0 failed`. Los tests que abren MariaDB se omitieron por `JAX_CI_NO_DB=1`.
- Delta de piso medido en worktrees limpios contra `origin/master d283d90`: backend `+4` pasadas (`test_adjuntos_upload.py` 15→17 y dos pruebas unitarias de estado); frontend `+6` (`FileAttachment.test.jsx`, cinco checks locales preexistentes conservados y uno nuevo de OCR).

## Para completar la medición

Repetir el escenario con el job de CI que levanta MariaDB desechable o en un host que tenga un contenedor aislado accesible en un puerto que no sea 3306 ni 3308. Medir duración de cada POST `/api/chat`, p95, cantidad de turnos completados y el estado del documento mientras el despachador está activo; registrar si LAS MANOS fue real o simulado.
