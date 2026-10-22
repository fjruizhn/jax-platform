# Entrega de invitaciones de Virtual Office

El proceso web nunca entrega correo de invitación. `axioma-office-invitation-worker.timer`
activa una instancia `Type=oneshot` cada 30 segundos, y systemd no inicia otra
instancia del mismo servicio mientras la anterior sigue activa.

Antes de habilitar el timer, el operador ejecuta la migración de Office, instala los
archivos de `ops/virtualoffice/`, y ejecuta como root:

```bash
ops/virtualoffice/provision-invitation-worker.sh /etc/jax/runtime.env
systemctl daemon-reload
systemd-run --wait --pipe --property=User=jaxinvite --property=EnvironmentFile=/etc/jax/invitation-worker.env /srv/jax-prod/jax-platform/backend/.venv/bin/python -Es /srv/jax-prod/jax-platform/ops/virtualoffice/run-invitation-worker.py --check-import
systemd-run --wait --pipe --property=User=jaxinvite --property=EnvironmentFile=/etc/jax/invitation-worker.env /srv/jax-prod/jax-platform/backend/.venv/bin/python -Es -c "import sys;sys.path.insert(0,'/srv/jax-prod/jax-platform/backend');from virtual_office.invitation_smtp_preflight import main;raise SystemExit(main())"
systemctl enable --now axioma-office-invitation-worker.timer
```

El provisionador crea el usuario de sistema y principal MariaDB `jaxinvite`. El
principal recibe solamente SELECT para configuración y las tablas necesarias para
leer el outbox, UPDATE para `office_invitation_outbox`, e INSERT para
`office_invitation_event`. No recibe privilegios para emitir/revocar invitaciones,
administrar usuarios, ni DDL.

`/etc/jax/invitation-worker.env` es el único `EnvironmentFile` del worker. Es
`root:root 0600` e incluye sólo conexión del principal, `FERNET_KEY` y las
variables Office necesarias para descifrar, validar y entregar una invitación. El
worker falla antes de reclamar una fila si falta configuración de invitación o SMTP.

Salud y recuperación:

```bash
sudo -u jaxinvite /srv/jax-prod/venv/bin/python -I -m virtual_office.invitation_worker_health
systemctl status axioma-office-invitation-worker.timer axioma-office-invitation-worker.service
```

La salud informa solamente conteos `queued`, `claimed` y `stale`. Un `stale > 0`
sale con estado 2: el siguiente worker convierte el intento abandonado en `UNKNOWN`;
nunca lo devuelve a `QUEUED`. `OnFailure=aviso-fallo@%n.service` usa el template
de alerta auditado del host; el readiness efectivo verifica que existe antes de producción.
