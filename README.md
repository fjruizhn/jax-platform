# JAX Platform

En memoria de Jairo Urbina. En honor al Prof. Raúl Jacobs.

## Estructura

```
jax-platform/
  backend/   — FastAPI, puerto 8080
  frontend/  — React 19 + Tailwind, puerto 5173 (dev)
```

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

## Frontend

```bash
cd frontend
npm install
npm run dev
```

## Variables de entorno

`/etc/jax/.env` — mismas credenciales que LAS MANOS.

## Servicio systemd

```bash
sudo systemctl enable jax-platform
sudo systemctl start jax-platform
```
