# Relecto — v0.4

AI-assisted radiology reporting for imaging centres. The pipeline is images → AI draft → **checker** → radiologist signs. This repository contains the **chest CT image pipeline** (Phase 1), the **checker**, the de-identification, the database, the local web app and the quality gate (release R1, part 1).

The checker reads report text only. It never adds findings, measurements or diagnoses: every issue points to words already in the report.

## Start (Mac)

```bash
./relecto serve        # first run installs everything into .venv, then serves http://127.0.0.1:8000
```
Or double-click **`start.command`** (right-click → Open the first time).

Other commands:

```bash
./relecto check clinical-content/examples/demo_ct_chest.txt --exam ct_chest
./relecto gate                 # quality gate on the golden set (fails if the checker got worse)
./relecto validate-content     # check the clinical YAML files after an edit
./relecto test                 # 42 automated tests
./relecto analyze ../dicom-test/LIDC-IDRI-0001   # chest CT study → findings, key images, draft report, checks
./relecto db-upgrade           # apply database migrations
```

## Run with Docker (app + PostgreSQL)

```bash
cp deploy/.env.example deploy/.env      # then put a long random password in it
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```
Open http://127.0.0.1:8000. This is the edge-node profile S (checker only). Login and roles (Keycloak) come in R1 part 2 — until then, keep it on your own machine.

## Chest CT pipeline (Phase 1)

```bash
./tools/get-test-ct.sh 5                          # public LIDC-IDRI scans → ../dicom-test (outside the repo)
./relecto analyze ../dicom-test/LIDC-IDRI-0001    # results → ../analysis/LIDC-IDRI-0001
```
Loads the CT series (DICOM geometry checked), finds lung nodules, measures long/short axis and volume, sets side and lung third, renders key images, writes a French draft with placeholders for the radiologist, runs the checker plus the image-vs-report check (IMG-001: nodule seen on the images but missing or on the wrong side in the report).

Today the nodules come from the radiologists' outlines shipped with LIDC-IDRI (`source="reference"`). The AI detection model plugs into the same slot next; the outlines then become the answer key it is scored against. Sources and licence: `docs/DATA_SOURCES.md`.

## Project layout

```text
backend/relecto/
  imaging/     CT loading, nodule measurement, key images, draft report, image-vs-report check
  checker/     rules engine (parser, extraction, rules, YAML loader)
  deid/        de-identification before any check or storage
  llm/         optional local LLM layer with guardrails (off by default)
  api/         FastAPI app + web screen
  db/          models (reports, checks, decisions, audit log)
  quality.py   quality gate
  cli.py       the ./relecto commands
backend/migrations/   database migrations (Alembic)
backend/tests/        automated tests
clinical-content/     owned by radiologists: vocabulary, rule packs, golden sets
deploy/               Docker Compose for an edge node
.github/workflows/    automatic checks on every push
```

## Editing the medical rules (for the clinical lead)

Everything medical is in `clinical-content/`, in French, with comments:

- `lexicon/fr.yaml` — findings, sides, negations, one-sided anatomy.
- `rule-packs/<exam>.yaml` — required sections, technique, which rules run and their severity.
- `golden-sets/*.jsonl` — annotated reference reports. Every release must pass them.

After an edit: raise the file's `version`, run `./relecto validate-content` and `./relecto gate`.

## Data protection

- Every report is de-identified before it is checked or stored. The database holds de-identified text only; the audit log never holds report text.
- `data/private/` (local database) is never committed. Never put real patient data in the repository.
- De-identification is rules-based and **not certified**: validate it on real samples with human review before any pilot.

## Status

| Done (v0.3) | Next (R1 part 2) |
| --- | --- |
| YAML rule packs, MOD-001, versions on every report | Login and roles (Keycloak, OIDC) |
| Database + migrations, audit log | Worklist and report editor screens |
| Quality gate, CI, Docker | 100 annotated reports from the clinical lead |

Scores on the golden set are on **synthetic reports written alongside the rules**: they prove the code works, not clinical performance.
