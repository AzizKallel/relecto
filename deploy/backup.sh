#!/bin/bash
# Nightly backup: database dump + study files. Usage: deploy/backup.sh /path/to/backup-disk
set -euo pipefail
DEST="${1:?backup folder}"; TS=$(date +%Y%m%d-%H%M)
cd "$(dirname "$0")"
mkdir -p "$DEST"
docker compose --env-file .env exec -T db pg_dump -U relecto -Fc relecto > "$DEST/relecto-db-$TS.dump"
docker run --rm -v deploy_studies:/data -v "$DEST":/out alpine tar czf "/out/relecto-studies-$TS.tgz" -C /data .
find "$DEST" -name 'relecto-*' -mtime +30 -delete
echo "Backup written to $DEST"
