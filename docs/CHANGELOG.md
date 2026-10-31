# Changelog

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
