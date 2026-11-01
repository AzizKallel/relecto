"""Relecto command line.

  relecto check FILE --exam ct_chest [--json] [--llm]
  relecto serve [--port 8000]
  relecto gate                      # quality gate on the golden set (exit 1 if it fails)
  relecto db-upgrade                # apply database migrations
  relecto validate-content          # check the clinical YAML files
  relecto analyze STUDY_DIR [--source auto|ai|reference]   # CT study -> findings -> draft -> checks
  relecto worker                    # AI worker: runs queued analyses
  relecto dicom [--port 11112]      # DICOM receiver for scanners / PACS
  relecto import FOLDER             # register DICOM studies from a folder and queue them
  relecto create-user EMAIL --name N --role radiologist|secretary|admin
  relecto evaluate-ai DIR...        # score the AI against reference outlines (public datasets)
"""

import argparse
import json
import sys

COLORS = {"critical": "\033[31m", "warning": "\033[33m", "info": "\033[34m"}
LABELS = {"critical": "CRITIQUE", "warning": "ATTENTION", "info": "INFO"}
RESET = "\033[0m"


def cmd_check(args) -> int:
    from .checker import run_check
    from .checker.content import get_content

    text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    if not args.raw:
        from .deid import deidentify
        text = deidentify(text).text
    result = run_check(text, exam=args.exam, use_llm=args.llm, llm_model=args.model)
    if args.json:
        print(json.dumps({"versions": result.versions, "issues": [i.to_dict() for i in result.issues]},
                         ensure_ascii=False, indent=2))
        return 0
    color = sys.stdout.isatty() and not args.no_color
    pack = get_content().pack(args.exam)
    print(f"\nExamen : {pack.label} — {len(result.issues)} point(s) à vérifier "
          f"({result.versions['rule_pack']}, lexique v{result.versions['lexicon']})\n")
    for n, i in enumerate(result.issues, 1):
        tag = LABELS[i.severity]
        if color:
            tag = COLORS[i.severity] + tag + RESET
        print(f"{n}. [{tag}] {i.title}  ({i.rule_id})")
        for e in i.evidence:
            print(f"     « {e.text} »")
        if i.suggestion:
            if i.suggestion.mode == "replace":
                print(f"     → Proposition : « {i.suggestion.original} » → « {i.suggestion.replacement} »")
            else:
                print(f"     → Proposition : ajouter à la conclusion « {i.suggestion.replacement} »")
        print(f"     {i.message}\n")
    if not result.issues:
        print("Aucun problème détecté par les règles actuelles.\n")
    print("Contrôle documentaire uniquement : ne remplace pas la relecture du radiologue.\n")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    from .db import migrate
    migrate()
    uvicorn.run("relecto.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_gate(args) -> int:
    from .quality import run_gate
    return run_gate(update_baseline=args.update_baseline)


def cmd_db_upgrade(args) -> int:
    from .db import database_url, migrate
    migrate()
    print(f"Database up to date: {database_url().split('@')[-1]}")
    return 0


def cmd_validate(args) -> int:
    from .checker.content import ContentError, reload_content
    try:
        c = reload_content()
    except ContentError as exc:
        print(f"ERREUR dans le contenu clinique : {exc}")
        return 1
    print(f"Lexique v{c.lexicon.version} : {len(c.lexicon.findings)} résultats")
    for p in c.packs.values():
        enabled = [r for r in p.rules if p.enabled(r)]
        print(f"{p.exam} v{p.version} : {len(enabled)} règles actives")
    return 0


def cmd_analyze(args) -> int:
    from pathlib import Path
    from .imaging.pipeline import analyze_study

    study = Path(args.study)
    out = Path(args.out) if args.out else study.parent.parent / "analysis" / study.name
    a = analyze_study(study, out, source=args.source, progress=lambda t: print(f"  … {t}", flush=True))
    print(f"\n{a.ct.meta.get('patient_id')} — {a.ct.hu.shape[0]} coupes, "
          f"{a.ct.spacing[1]:.2f} x {a.ct.spacing[2]:.2f} x {a.ct.spacing[0]:.2f} mm, {a.seconds} s")
    for q in a.ct.qc:
        print(f"  QC : {q}")
    print(f"\n{len(a.findings)} résultat(s) :")
    for f in a.findings:
        print(f"  {f.finding_id}  {f.long_axis_mm} x {f.short_axis_mm} mm  poumon {'droit' if f.side == 'R' else 'gauche'}"
              f"  {f.location_fr}  coupe {f.key_slice + 1}  ({f.source})")
    for n in a.notes:
        print(f"  Note : {n}")
    print("\n--- BROUILLON ---\n" + a.draft)
    print(f"--- VÉRIFICATION : {len(a.issues)} point(s) ---")
    for i in a.issues:
        print(f"  [{i.severity}] {i.rule_id} {i.title}")
    print(f"\nFichiers : {out}")
    return 0


def cmd_worker(args) -> int:
    from .db import migrate
    from .worker import loop
    migrate()
    loop(once=args.once)
    return 0


def cmd_dicom(args) -> int:
    import logging
    from .db import migrate
    from .dicom_receiver import serve
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    migrate()
    serve(args.port, args.ae_title)
    return 0


def cmd_import(args) -> int:
    from .db import migrate
    from .studies import ingest_folder
    migrate()
    ids = ingest_folder(args.folder, source="folder", queue=not args.no_analysis)
    print(f"{len(ids)} examen(s) enregistré(s){'' if args.no_analysis else ' et mis en file d’analyse'}.")
    return 0


def cmd_create_user(args) -> int:
    import getpass
    from fastapi import HTTPException
    from .auth import create_user
    from .db import migrate
    migrate()
    pw = args.password or getpass.getpass("Mot de passe (10 caractères min., une majuscule, un chiffre) : ")
    try:
        create_user(args.email, args.name, args.role, pw, args.title)
    except HTTPException as exc:
        print(f"Erreur : {exc.detail}")
        return 1
    print(f"Compte créé : {args.email} ({args.role})")
    return 0


def cmd_evaluate_ai(args) -> int:
    from .imaging.evaluation import evaluate_folders
    return evaluate_folders(args.studies, args.out)


def main(argv=None) -> int:
    from .checker.content import get_content

    p = argparse.ArgumentParser(prog="relecto", description="Relecto — contrôle qualité des comptes rendus")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="Vérifier un compte rendu (.txt, ou - pour l'entrée standard)")
    c.add_argument("file")
    c.add_argument("--exam", default="ct_chest")
    c.add_argument("--json", action="store_true")
    c.add_argument("--llm", action="store_true", help="Ajouter la vérification par LLM local (Ollama)")
    c.add_argument("--model", default=None)
    c.add_argument("--raw", action="store_true", help="Ne pas dé-identifier (texte déjà anonymisé)")
    c.add_argument("--no-color", action="store_true")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("serve", help="Lancer l'application web")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(func=cmd_serve)

    g = sub.add_parser("gate", help="Porte qualité sur le jeu de référence")
    g.add_argument("--update-baseline", action="store_true", help="Enregistrer les scores actuels comme référence")
    g.set_defaults(func=cmd_gate)

    sub.add_parser("db-upgrade", help="Mettre à jour la base de données").set_defaults(func=cmd_db_upgrade)
    sub.add_parser("validate-content", help="Vérifier les fichiers cliniques").set_defaults(func=cmd_validate)

    a = sub.add_parser("analyze", help="Analyser un examen scanner (dossier DICOM)")
    a.add_argument("study")
    a.add_argument("--out", default=None, help="Dossier de sortie (défaut : ../analysis/<examen>)")
    a.add_argument("--source", default="auto", choices=["auto", "ai", "reference"])
    a.set_defaults(func=cmd_analyze)

    w = sub.add_parser("worker", help="Lancer le moteur d'analyse IA")
    w.add_argument("--once", action="store_true", help="Traiter la file puis s'arrêter")
    w.set_defaults(func=cmd_worker)

    d = sub.add_parser("dicom", help="Récepteur DICOM (scanner / PACS)")
    d.add_argument("--port", type=int, default=11112)
    d.add_argument("--ae-title", default="RELECTO")
    d.set_defaults(func=cmd_dicom)

    i = sub.add_parser("import", help="Importer des examens DICOM depuis un dossier")
    i.add_argument("folder")
    i.add_argument("--no-analysis", action="store_true")
    i.set_defaults(func=cmd_import)

    u = sub.add_parser("create-user", help="Créer un compte")
    u.add_argument("email")
    u.add_argument("--name", required=True)
    u.add_argument("--role", default="radiologist", choices=["admin", "radiologist", "secretary"])
    u.add_argument("--title", default=None)
    u.add_argument("--password", default=None, help="(déconseillé : saisie masquée si absent)")
    u.set_defaults(func=cmd_create_user)

    e = sub.add_parser("evaluate-ai", help="Comparer l'IA aux contours de référence")
    e.add_argument("studies", nargs="+")
    e.add_argument("--out", default=None, help="Fichier JSON des résultats")
    e.set_defaults(func=cmd_evaluate_ai)

    args = p.parse_args(argv)
    if getattr(args, "exam", None) and args.exam not in get_content().packs:
        p.error(f"examen inconnu '{args.exam}' (disponibles : {', '.join(sorted(get_content().packs))})")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
