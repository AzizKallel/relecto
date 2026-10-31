# Changelog

## 0.4.0 — 1 Nov 2026 (Phase 1, step 1: chest CT pipeline)
- `relecto analyze STUDY`: DICOM CT loading with geometry and QC (orientation, duplicates, spacing), HU rescale.
- Reads DICOM SEG annotations, merges the 4 LIDC readers into consensus nodules.
- Measurements: long/short axis, volume, mean HU, side (patient LPS geometry), lung third (body-limited lung mask).
- Key images in radiological convention, French draft report (measured facts only, placeholders elsewhere).
- New check IMG-001: nodule on the images missing or on the wrong side in the report.
- Validated on LIDC-IDRI 0001–0005: sizes and volumes within the readers' ranges; 3–6 s per study.
- 9 new tests (synthetic scans with known answers, incl. mirrored storage; LIDC test when scans are present). 42 tests.

## 0.3.0 — 31 Oct 2026 (R1, part 1)
- New project layout: `backend/`, `clinical-content/`, `deploy/`, `docs/`, CI.
- Clinical vocabulary and per-exam rule packs moved to YAML (`clinical-content/`), validated on load; a rule runs only if its exam pack enables it.
- New rule MOD-001: vocabulary of another modality (MRI words in a CT report and the reverse), ignoring recommendations and comparisons.
- Versions of engine, lexicon and rule pack stamped on every check, decision and signed report.
- Database (SQLAlchemy + Alembic): centres, users, reports, check runs, decisions, append-only audit log. SQLite for development, PostgreSQL in production.
- API: reports persisted across re-checks, sign endpoint locks a report, health endpoint, content reload.
- Quality gate (`relecto gate`): blocks a release if any rule's precision < 0.50 or drops vs the baseline.
- Docker image and docker-compose (app + PostgreSQL), GitHub Actions CI (SQLite + PostgreSQL + gate + Docker build).
- 33 tests, passing on SQLite and PostgreSQL 16.

## 0.2.0 — 31 Oct 2026
- De-identification (names, dates, IDs, phones, e-mails, addresses), local web app, decision log, 26 tests.

## 0.1.0 — 31 Oct 2026
- Rules engine for French CT chest and MRI brain reports, CLI, evaluation script, 13 tests.
