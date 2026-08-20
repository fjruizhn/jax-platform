# Paquete de migración JAX / Axioma → hall9000

Restaura el ecosistema en la máquina nueva. **Corre los scripts en este orden:**

```bash
# 0. (desde el origen) transferir el paquete
rsync -avz -e "ssh -p <puerto, ver /etc/jax/.env>" /home/fruiz/migration-20260708/ \
  fruiz@<IP interna, ver /etc/jax/.env>:/home/fruiz/migration-20260708/

# — ya en hall9000, como usuario fruiz —
cd /home/fruiz/migration-20260708

# 1. SIMULAR la restauración (no toca nada; revisa el plan)
bash restore-hall9000.sh --dry-run

# 2. RESTAURAR de verdad (con checkpoints por fase; pide la passphrase del .env)
bash restore-hall9000.sh

# 3. ARRANCAR servicios uno por uno y verificar cada 'start' antes de 'enable'
#    (el checklist final de restore-hall9000.sh te da los comandos exactos)

# 4. VERIFICAR salud (read-only; exit 0 = todo OK)
sudo -v && bash verify-hall9000.sh
```

## Contenido

| Archivo | Qué es |
|---------|--------|
| `restore-hall9000.sh` | Restauración en 8 fases (prereqs → clonar → DB → .env → venvs → frontend → ollama → systemd). Soporta `--dry-run`. **No arranca servicios.** |
| `verify-hall9000.sh` | Healthcheck post-arranque (servicios · puertos · 3 endpoints · DB · ollama). Solo lectura, re-ejecutable. |
| `MIGRATION.md` | Runbook completo: detalle de cada fase + pasos manuales/fallback + pendientes operativos (SMTP, deploy, etc.). |
| `jax-platform-full.bundle` | Historia git completa de jax-platform (fallback si no hay acceso a GitHub). |
| `jax_memory-20260708.sql.gz` | Dump de la base de datos MariaDB (33 tablas). |
| `jax.env.enc` | `/etc/jax/.env` cifrado (openssl AES-256). Necesita tu passphrase. |
| `systemd-units/` | Los 7 units systemd. |

## Requisitos en hall9000 (ya preparados)
Git · MariaDB 11.8 · Ollama (ROCm) · Node v24.16.0 (nvm) · usuario `fruiz` · SSH key registrada en GitHub (cuenta `fjruizhn`).

> Detalle completo, credenciales, descifrado y checklist post-restauración → **`MIGRATION.md`**.
