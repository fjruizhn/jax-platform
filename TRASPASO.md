# Traspaso continuo — E3 respaldo chat

**Fecha:** 2026-10-06
**Rama:** `feat/e3-respaldo-chat`
**Base:** `origin/master` d283d90
**Worktree:** `/home/fruiz/wt/jxp-e3-respaldo`

## Estado

- La auditoría del SHA `8b1883d` rechazó el envío bloqueado por PDF, las brechas de integración/carga y cuatro puntos mayores. Tras corregirlos, la auditoría del SHA `ff619e7` cerró todos menos el posible cambio de proyecto durante la subida, la carga/DB faltantes y este handoff obsoleto.
- Corregido en el árbol: el turno de texto sale sin esperar OCR ni adjuntar o mostrar el PDF como leído; queda el estado vivo en el compositor y se identifica el proyecto de destino. El selector de proyecto se desactiva durante cualquier subida y mientras el PDF siga en el compositor. Los errores de autorización usan i18n de Documentos salvo códigos compartidos de la Mesa. Los PDFs identificados por bytes conservan sufijo `.pdf` al entrar a la cola y nombre original como metadato.
- Pisos: master tenía 1389 frontend; piso actual 1393. Backend pasa de 2358 a 2363.
- Suite frontend completa: `1393 passed`; backend sin DB: `2363 passed, 1479 skipped, 3 warnings`. Faltan admisión real en cola, aislamiento HTTP y carga de 20 usuarios porque no hay MariaDB aislado disponible localmente. No usar `jax_memory` ni habilitar el opt-in de producción.

## Archivos modificados aún sin commit

Cambios aún por commit: `.github/workflows/policy.yml`, `backend/api/proyectos_documentos.py`, `backend/tests/test_proyectos_documentos_api.py`, `frontend/src/components/BottomBar/BottomBar.jsx`, `frontend/src/components/BottomBar/BottomBar.test.jsx`, `frontend/src/components/BottomBar/SelectorDeProyecto.jsx`, `frontend/src/components/BottomBar/SelectorDeProyecto.test.jsx`, `frontend/src/i18n/en.js`, `frontend/src/i18n/es.js`, `docs/carga-e3-respaldo-chat-2026-10-06.md`.

## Próximos pasos

1. Commit de corrección del selector y de pisos/documentación.
2. Registrar este handoff en la Biblioteca y retirarlo de la rama en commit separado.
3. Pedir auditoría adversarial del SHA final exacto.
4. No fusionar ni desplegar. No editar `PENDIENTES.md`.
5. Cerrar la medición de carga solo con DB aislada; reportar N/A si sigue inaccesible.
