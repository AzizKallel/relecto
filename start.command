#!/bin/bash
# Double-click to start Relecto (needs Docker Desktop running).
cd "$(dirname "$0")"
ENV=deploy/.env
[ -f "$ENV" ] || cp deploy/.env.example "$ENV"
# Fill in any missing or default secret (keeps existing ones).
if ! grep -q '^POSTGRES_PASSWORD=..........' "$ENV" || grep -q '^POSTGRES_PASSWORD=change-me' "$ENV"; then
  grep -v '^POSTGRES_PASSWORD=' "$ENV" > "$ENV.tmp"; echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)" >> "$ENV.tmp"; mv "$ENV.tmp" "$ENV"
  docker volume rm deploy_pgdata >/dev/null 2>&1   # old test database used the old password
fi
if ! grep -q '^RELECTO_SECRET=..........' "$ENV" || grep -q '^RELECTO_SECRET=change-me' "$ENV"; then
  grep -v '^RELECTO_SECRET=' "$ENV" > "$ENV.tmp"; echo "RELECTO_SECRET=$(openssl rand -hex 32)" >> "$ENV.tmp"; mv "$ENV.tmp" "$ENV"
fi
grep -q '^RELECTO_BIND=' "$ENV" || echo "RELECTO_BIND=127.0.0.1" >> "$ENV"
chmod 600 "$ENV"
echo "Construction et démarrage de Relecto (la première fois : 10-20 min)…"
docker compose -f deploy/docker-compose.yml --env-file "$ENV" up -d --build || { echo; echo "Échec : Docker Desktop est-il lancé ?"; read -r; exit 1; }
for i in $(seq 1 90); do curl -fs http://127.0.0.1:8000/api/health >/dev/null && break; sleep 2; done
open http://127.0.0.1:8000
echo "Relecto est lancé : http://127.0.0.1:8000"
