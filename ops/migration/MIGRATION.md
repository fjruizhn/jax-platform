# Runbook de migración — Ecosistema JAX / Axioma → máquina nueva

Generado: 2026-07-08 · Origen: hall9000 (172.16.20.5)

Este documento lista TODO lo necesario para levantar el ecosistema en una máquina nueva.
Lo que está en git ya está seguro; lo que NO está en git hay que copiarlo a mano (marcado ⚠).

## ⭐ Forma recomendada: script automatizado

En la máquina destino, corre PRIMERO en modo simulación y luego real:

```bash
cd /home/fruiz/migration-20260708
bash restore-hall9000.sh --dry-run   # 1ª pasada: muestra qué haría, sin tocar nada
bash restore-hall9000.sh             # ejecución real, con checkpoints por fase
```

El script cubre las 8 fases (prereqs → clonar → DB → .env → venvs → frontend →
ollama → systemd) con confirmaciones y sin arrancar servicios. Las secciones de
abajo son la referencia manual / fallback de cada fase.

Tras arrancar los servicios (manual, según el checklist), verifica la salud:

```bash
sudo -v                          # cachea sudo para el chequeo completo de DB
bash verify-hall9000.sh          # servicios · puertos · 3 endpoints · DB · ollama
```

`verify-hall9000.sh` es de SOLO LECTURA y re-ejecutable (exit 0 = todo OK; 1 = hay
fallos), apto para monitoreo/cron.

---

## 1. Repositorios git — ✅ ya respaldados en GitHub

| Repo | Origen | GitHub | Estado |
|------|--------|--------|--------|
| jax (ecosistema: las_manos, jacobs, memory) | /home/fruiz/jax | `git@github.com:fjruizhn/Jax.git` | pusheado ✓ |
| jax-platform (Axioma) | /home/fruiz/jax-platform | `git@github.com:fjruizhn/jax-platform.git` | pusheado ✓ |
| keyd | /home/fruiz/jax/keyd | upstream rvaiya/keyd (vendored) | no migrar (dependencia) |

**En la máquina nueva:**
```bash
# Requiere que la SSH key de la máquina nueva esté registrada en GitHub (cuenta fjruizhn)
git clone git@github.com:fjruizhn/Jax.git         ~/jax
git clone git@github.com:fjruizhn/jax-platform.git ~/jax-platform
```

**Fallback offline** (si no hay GitHub): bundle con TODA la historia de jax-platform:
```bash
# Copiar jax-platform-full.bundle a la máquina nueva, luego:
git clone jax-platform-full.bundle ~/jax-platform
```

---

## 2. Credenciales — ✅ CIFRADAS EN ESTA CARPETA

**Artefacto:** `jax.env.enc` — `/etc/jax/.env` cifrado con openssl AES-256 (passphrase que
elegiste, NO está guardada en ningún lado; recuérdala/guárdala en tu gestor de contraseñas).

Contiene: JAX_DB_*, JAX_JWT_SECRET, FERNET_KEY, API keys (OpenAI/Gemini/DeepSeek/Kimi/ZAI),
TELEGRAM_*, FRONTEND_ORIGIN. (Aún SIN SMTP_* — agregar para el reset de password.)

**Descifrar en la máquina nueva:**
```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in jax.env.enc -out .env
# revisar, luego instalar:
sudo mkdir -p /etc/jax && sudo install -m 600 -o root -g root .env /etc/jax/.env
rm .env   # borrar la copia en claro
```
> ⚠ Si rotas `JAX_JWT_SECRET` invalidas TODAS las sesiones (ver CLAUDE.md). Decisión consciente.

---

## 3. Base de datos MariaDB `jax_memory` — ✅ DUMP YA GENERADO

**Artefacto listo en esta carpeta:** `jax_memory-20260708.sql.gz` (3.7M, 33 tablas, gzip verificado).

```bash
# (Ya ejecutado en hall9000 — comando de referencia:)
#   MYSQL_PWD=... mysqldump --single-transaction --routines --triggers \
#     -h localhost -u jax_user jax_memory | gzip > jax_memory-20260708.sql.gz

# En la máquina nueva:
sudo mysql -e "CREATE DATABASE IF NOT EXISTS jax_memory CHARACTER SET utf8mb4"
gunzip < jax_memory-20260708.sql.gz | sudo mysql jax_memory
```
> Recordar: MariaDB 11.8 con columnas vector(768). Verificar que la versión destino soporte vector.

---

## 4. Servicios systemd (5 units) — ✅ YA COPIADOS

**Artefactos listos en esta carpeta:** `systemd-units/` (5 units: jax-las-manos, jax-platform,
jax-platform-frontend, jax-memory-worker.service + .timer).

```bash
# En la máquina nueva:
sudo cp systemd-units/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jax-las-manos jax-platform jax-platform-frontend jax-memory-worker.timer
```
> Revisar rutas ExecStart/WorkingDirectory/User dentro de cada unit — apuntan a /home/fruiz/...

---

## 5. Runtime / dependencias

- **Node.js v24.16.0 vía nvm** (⚠ NUNCA NodeSource en este servidor — política CLAUDE.md):
```bash
nvm install v24.16.0 && nvm use v24.16.0
cd ~/jax-platform/frontend && npm install
```
- **Python 3.12 + venv** por servicio:
```bash
cd ~/jax-platform/backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# (requirements ahora incluye aiosmtplib + psutil de la feature auth/admin)
```
- **Ollama models** (~16 GB, requieren GPU RX 9060 XT / Vulkan):
```bash
ollama pull qwen3:14b          # 9.3 GB — motor principal
ollama pull nomic-embed-text   # 274 MB — embeddings
ollama pull qwen2.5:7b         # 4.7 GB
ollama pull llama3.2:3b        # 2.0 GB
```

---

## 6. Config global ruflo/claude (opcional, 817M)

`/home/fruiz/.claude/` — CLAUDE.md global, memoria, checkpoints, proyectos.
Migrar solo si quieres conservar memoria/historial de Claude Code. Contiene datos locales;
revisar antes de copiar. Los `.claude/`, `.claude-flow/`, `.swarm/` DENTRO de los repos
están gitignoreados (runtime local) — se regeneran con `ruflo init`.

---

## 7. Pendientes operativos post-migración (feature auth recién mergeada)

Antes de que login/reset funcione en la máquina nueva:
1. Correr migración DB → crea `password_reset_tokens` + columnas failed_attempts/locked_until/last_login.
2. Setear en `/etc/jax/.env`: `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `FRONTEND_ORIGIN`.
   (Sin SMTP el reset solo loguea el link, no envía email.)
3. Deploy frontend: `npm run build` NO actualiza prod — requiere rsync a la VM dev (172.16.20.11).
   Ver "Lecciones operativas" en ~/jax-platform/CLAUDE.md.
4. Deuda ADN pendiente: Login + ResetPassword usan colores dark-only (no theme-aware).

---

## Checklist rápido

- [ ] `git clone` jax + jax-platform (o restore de bundle)
- [ ] Copiar `/etc/jax/.env`
- [ ] Restore DB `jax_memory`
- [ ] Copiar + habilitar 5 units systemd
- [ ] nvm install v24.16.0 + npm install + venv/pip
- [ ] ollama pull (4 modelos)
- [ ] Correr migración DB + setear SMTP env
- [ ] Deploy frontend (rsync a VM dev)
