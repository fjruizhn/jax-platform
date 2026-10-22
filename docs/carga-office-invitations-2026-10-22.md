# Carga de invitaciones Office · 2026-10-22

**Tipo:** VERDAD OPERACIONAL. **Fuente:** ejecución local del arnés
`loadtest/office_invitations.py`, resultado
`/tmp/office-invitations-load-2b3c4d5e.json`, SHA de arnés pendiente de
integración. No acredita rendimiento de producción.

## Entorno y frontera

- MariaDB desechable `cv-op343-db-20261020`, expuesta únicamente en
  `127.0.0.1:33991`; esquema nuevo `jax_memory_test_2b3c4d5e`.
- El esquema fue clonado y migrado por `base_de_test.asegurar_base_de_test`;
  los principales web/migrador se provisionaron con
  `ops/migration/provision-db-runtime.sh`.
- El proceso ASGI real se levantó en un puerto loopback efímero. No hubo
  tráfico a DNS, vhost ni base de producción.
- El arnés rechaza host que no sea `127.0.0.1`/`::1`, puertos 3306/3308 y un
  puerto que no pertenezca al contenedor Docker desechable declarado.
- Cada nivel usa `c` usuarios concurrentes y cada usuario ejecuta 4
  operaciones **en serie**. Antes de medir lista se crearon 100 invitaciones
  activas mediante el endpoint real y cada lista verificó devolver 100.
- Autenticación, MFA, emisión, aceptación y revocación recorren HTTP real.
  Para la aceptación, el arnés lee el bearer cifrado de la outbox de su propia
  base aislada; no se simula autorización.

## Resultado

Las cifras son p95 en milisegundos; todas las operaciones listadas recibieron
HTTP 200, sin 429 ni otros errores. La tasa de login del **proceso local de
benchmark** se fijó explícitamente a `10000/60` para IP y correo: evita que
un bucket en memoria agotado por c=1 distorsione c=5/c=10. No cambia la
configuración de producción ni es una afirmación sobre la protección allí.

| Operación | c=1 (4 peticiones) | c=5 (20) | c=10 (40) |
| --- | ---: | ---: | ---: |
| Lista de 100 activas | 3.49 ms · 5.63 rps | 13.52 ms · 23.63 rps | 24.47 ms · 36.52 rps |
| Emitir para aceptar | 4.57 ms · 232.60 rps | 12.35 ms · 426.24 rps | 28.98 ms · 363.70 rps |
| Aceptar | 163.30 ms · 5.94 rps | 174.09 ms · 26.78 rps | 190.97 ms · 45.26 rps |
| Emitir para revocar | 5.72 ms · 203.96 rps | 14.31 ms · 388.70 rps | 26.78 ms · 393.75 rps |
| Revocar | 3.71 ms · 272.28 rps | 11.84 ms · 461.53 rps | 21.90 ms · 455.50 rps |

Con el criterio diagnóstico del arnés (p95 superior a 3× c=1), la primera
degradación aparece en lista y revocación a c=5; emisión a c=10. Aceptación
no pasa 3× c=1 hasta c=10. Es un indicador de cola en esta máquina, no un
umbral de GO de producción.

## Evidencia histórica rechazada

La corrida anterior `jax_memory_test_1a2b3c4d`, guardada en
`/tmp/office-invitations-load-1a2b3c4d.json`, queda como **RECHAZADA** para
capacidad: rotulaba `c` aunque lanzaba `c × 4` operaciones de una vez,
medía lista antes de sembrar el peor caso y agotaba el limitador compartido,
con 429 durante aceptación. Se conserva como antecedente, no se reemplaza
silenciosamente ni se usa para decidir publicación.
