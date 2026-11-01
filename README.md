# Relecto — v0.5

AI radiology reporting platform for imaging centres. The scanner sends a chest CT, AI models
locate and measure the lung nodules, the platform drafts the French report, the checker flags
errors, and the radiologist corrects and signs it. All of this runs on the centre's own
machine; no image or report leaves it.

```text
Scanner / PACS ──DICOM──▶ receiver ─┐
Secretary ── .zip upload ──────────┤──▶ worklist ──▶ AI worker (lobes + nodules, measurements)
                                    │                    │
                                    │                    ▼
                         radiologist ◀── viewer + draft report + checks ──▶ signature ──▶ PDF
```

## Start in a centre (Docker)

Double-click **`start.command`** (Docker Desktop must be running, memory ≥ 10 GB), or:

```bash
cp deploy/.env.example deploy/.env      # set the two secrets
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Open http://127.0.0.1:8000: the first screen creates the administrator account. Full guide,
including connecting the scanner: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## What it does

| Area | Features |
|---|---|
| Intake | DICOM receiver (AE `RELECTO`, port 11112, sender allow-list), `.zip` upload, folder import, studies grouped and de-duplicated by Study UID |
| AI | Lung lobe segmentation and lung nodule detection (open-source models, run locally — [`docs/MODELS.md`](docs/MODELS.md)); our own measurement of long/short axis, volume, density, side and lobe; geometry checks |
| Reading | Worklist with status, priority, assignment, search · browser viewer: axial/coronal/sagittal, lung/mediastinum/bone windows, scroll, zoom, pan, nodule and lobe overlays, jump to finding |
| Report | Draft that states only measured facts and leaves placeholders, checker (laterality, negation, measurements, modality, structure…), image-vs-report check (IMG-001), one-click fixes, signature that locks the report, PDF with the centre's header |
| Control | Accounts and roles (admin, radiologist, secretary), throttled login, audit log of every action (never report text), quality dashboard (AI findings confirmed, suggestions accepted, turnaround), versions of engine, rules and models stamped on every signed report |

## Commands (without Docker)

```bash
./relecto serve            # web app http://127.0.0.1:8000
./relecto worker           # AI worker   (./relecto install-ai first)
./relecto dicom            # DICOM receiver
./relecto import FOLDER    # import DICOM studies from a folder
./relecto evaluate-ai ../dicom-test/LIDC-IDRI-000*   # score the AI against reference outlines
./relecto test             # automated tests
```

## Project layout

```text
backend/relecto/
  api/         web API + web application (static/app.html)
  imaging/     CT loading, AI models, measurements, draft, viewer data, evaluation
  checker/     report rules engine (French)
  auth.py      accounts, sessions, roles
  studies.py   study intake (zip, folder) · dicom_receiver.py (scanner / PACS)
  worker.py    AI job queue · reporting.py (PDF) · deid/ · llm/ · db/
backend/migrations/   database migrations
backend/tests/        automated tests (synthetic scans; LIDC tests when downloaded)
clinical-content/     owned by radiologists: vocabulary, rule packs, golden sets
deploy/               Docker Compose (db, app, worker, dicom), backup script
docs/                 deployment, models, validation, data sources, changelog
```

## Status and limits

Ready for a **supervised pilot**: every report is read and signed by a radiologist.

Not done yet, and required before routine clinical use:

- Validation on Tunisian scans annotated by the centre's radiologists (target: 100+ CT).
- Legal: INPDP declaration/authorisation for processing health data; data-processing contract with each centre.
- Medical-device route (EU MDR class IIa for diagnostic-support software; quality system ISO 13485, IEC 62304, ISO 14971).
- HTTPS and the centre's firewall when opened to the network; nightly backups scheduled.
- More exams: today the AI covers chest CT nodules; other exams get the checker and manual reporting.
