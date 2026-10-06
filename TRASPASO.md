# Traspaso continuo — E3 respaldo chat

**Fecha:** 2026-10-06
**Rama:** `feat/e3-respaldo-chat`
**Base:** `origin/master` d283d90
**Worktree:** `/home/fruiz/wt/jxp-e3-respaldo`

## Estado

- Auditoría adversarial anterior rechazó SHA `8b1883d`: chat no debía quedar bloqueado por el PDF; había dos brechas de verificación y cuatro hallazgos mayores.
- Corregido en el árbol de trabajo: el turno de texto se envía sin esperar OCR ni adjuntar o mostrar el PDF como leído; queda el estado vivo en el compositor y se identifica el proyecto de destino. Los errores de autorización usan i18n de Documentos. Los PDFs identificados por bytes conservan sufijo `.pdf` al entrar a la cola, aunque el nombre original tenga otra extensión.
- Corregida procedencia de pisos: master ya tenía 1389 tests frontend; el piso propuesto pasa a 1392. Backend pasa de 2362 a 2363 por un test puro adicional.
- Suites completas frontend y backend sin DB están en curso; no hay DB desechable disponible localmente. No usar `jax_memory` ni habilitar el opt-in de producción.

## Archivos modificados aún sin commit

`.github/workflows/policy.yml`, `backend/api/proyectos_documentos.py`, `backend/tests/test_proyectos_documentos_api.py`, `frontend/src/components/BottomBar/BottomBar.jsx`, `frontend/src/components/BottomBar/BottomBar.test.jsx`, `frontend/src/i18n/en.js`, `frontend/src/i18n/es.js`.

## Próximos pasos

1. Recoger resultados de suites y actualizar pisos/reportes con cifras observadas.
2. Corregir fallos de verificación si los hay; ejecutar `git diff --check`.
3. Commit de cambios, pedir auditoría adversarial del SHA exacto y corregir cualquier hallazgo.
4. No fusionar ni desplegar. No editar `PENDIENTES.md`.
5. Cerrar la medición de carga solo con DB aislada; reportar N/A si sigue inaccesible.
