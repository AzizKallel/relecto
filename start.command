#!/bin/bash
# Double-click to start Relecto (needs Docker Desktop running).
cd "$(dirname "$0")"
if [ ! -f deploy/.env ]; then
  cp deploy/.env.example deploy/.env
  sed -i '' "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" deploy/.env
  sed -i '' "s/^RELECTO_SECRET=.*/RELECTO_SECRET=$(openssl rand -hex 32)/" deploy/.env
  echo "Secrets generated in deploy/.env"
fi
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build || { echo "Docker Desktop est-il lancé ?"; read -r; exit 1; }
echo "Relecto démarre… (le premier lancement télécharge les modèles d'IA)"
for i in $(seq 1 60); do curl -fs http://127.0.0.1:8000/api/health >/dev/null && break; sleep 2; done
open http://127.0.0.1:8000
