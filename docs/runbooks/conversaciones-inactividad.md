# Cierre de conversaciones web por inactividad

Decisión de Fernando, 2026-10-05.

La memoria (worker de facts) solo extrae de conversaciones **cerradas**
(`conversations.ended_at`). Una conversación web se cierra tras N minutos sin
actividad.

## Configuración

| Variable | Valor | Por defecto |
|---|---|---|
| `JAX_CONVERSACION_INACTIVIDAD_MIN` | minutos, entero > 0 | `30` |

Va en `/etc/jax/.env` (opcional: sin la variable rige 30). Un valor ilegible o
<= 0 no tumba el servicio: avisa en `WARNING` y usa 30. Cambiarla exige reiniciar
`jax-platform`.

## Mecanismos (todos best-effort, `logger.warning` ante fallo)

1. **Perezoso**: `_get_conv_uuid` (`backend/api/chat.py`) cierra la conversación
   rastreada si lleva >= umbral sin actividad y abre una nueva.
2. **Barrido periódico**: tarea asyncio del lifespan, cada 5 min
   (`INTERVALO_BARRIDO_INACTIVIDAD_S`), cierra y deja de rastrear las vencidas;
   se cancela al apagar.
3. **Al arrancar**: `backend/conversaciones_inactivas.py` cierra en la base las
   conversaciones web (`axioma-web`, `axioma-web-proyecto`) con `ended_at IS NULL`
   cuya última actividad (último mensaje, o `started_at`) es anterior al umbral.
   Las de otro origen (REPL `terminal`) no se tocan.

## Índices que usa el cierre al arrancar

`idx_conversations_open (ended_at, started_at)` y
`idx_messages_conversation_turn (conversation_id, turn_number, id)`.
Pendiente de verificar con `EXPLAIN` sobre una MariaDB desechable (ver informe de la rama).
