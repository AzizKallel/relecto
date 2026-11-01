# Deploying Relecto in a centre

Relecto runs on one machine inside the centre (the *edge node*). Images, reports and accounts
stay on that machine; nothing is sent outside.

## Hardware

| Size of centre | Machine | Time per chest CT (lobes + nodules) |
|---|---|---|
| Pilot / small | 8-core CPU, **16 GB RAM**, 1 TB SSD | ~3–6 min (CPU) |
| Busy centre | + NVIDIA GPU 12 GB+ (RTX 4070 or better), 32 GB RAM | ~20–40 s |
| Several sites | central GPU server, one edge node per site | — |

With less than 12 GB of RAM the nodule model runs with smaller tiles (`RELECTO_AI_MAX_PATCH`)
— same model, slower. Check accuracy with `relecto evaluate-ai` after any hardware change.

## Install (Mac or Linux, Docker)

1. Install Docker Desktop. On a Mac: Settings → Resources → Memory **10 GB or more**.
2. Double-click `start.command` (Mac) or run:
   ```bash
   cp deploy/.env.example deploy/.env            # put two long random secrets in it
   docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
   ```
3. Open http://127.0.0.1:8000 → the first screen creates the administrator account.
4. Administration → add radiologists and secretaries, fill in the centre's name and address
   (printed on every report).

The first analysis downloads the AI model weights once (~1.5 GB, from GitHub) into the
`models` volume.

## Connect the scanner / PACS

Declare Relecto as a DICOM destination on the scanner or PACS:

- AE title: `RELECTO` · host: this machine's IP · port: `11112`
- Restrict senders: `RELECTO_DICOM_ALLOWED_AES=CT_SCANNER1,PACS` in `deploy/.env`
- Set `RELECTO_BIND=0.0.0.0` so other machines of the network can reach the ports.

Studies sent to Relecto appear in the worklist and are analysed automatically.
Without a DICOM link, secretaries can drop the study's `.zip` on the worklist.

## Access from the centre's other PCs

Do not expose port 8000 to the Internet. For access from other workstations:
put Relecto behind HTTPS (e.g. Caddy or nginx with the centre's certificate), set
`RELECTO_BIND=0.0.0.0`, and allow only the centre's network in the firewall.
Sessions are marked Secure automatically when served over HTTPS.

## Backups

```bash
deploy/backup.sh /Volumes/BackupDisk        # database dump + study files, keeps 30 days
```
Schedule it nightly (cron / launchd) and test a restore once a month.

## Update

```bash
git pull && docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```
Database migrations run automatically at start. Every signed report keeps the versions of
the engine, rules and models that produced its draft.

## Without Docker (development)

```bash
./relecto serve        # web app on 127.0.0.1:8000 (SQLite)
./relecto worker       # AI worker — install the models first: .venv/bin/pip install -r requirements-ai.txt
./relecto dicom        # DICOM receiver on port 11112
```
