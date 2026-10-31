"""Optional LLM layer (local model through Ollama).

The LLM only answers narrow questions the rules cannot express. Its output is
accepted only if:
  * it is valid JSON with the expected fields,
  * every evidence quote exists verbatim in the report,
  * it introduces no number that is absent from the report (no invented
    measurements),
  * it does not duplicate an issue the rules already raised.
Anything else is dropped silently. Report text must be de-identified first.
"""

import json
import os
import re
import urllib.request
from typing import List, Optional

from ..checker.issues import Evidence, Issue

DEFAULT_MODEL = os.environ.get("RELECTO_LLM_MODEL", "mistral:7b-instruct")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")

PROMPT = """Tu es un relecteur de comptes rendus de radiologie ({exam}).
Tu ne vois PAS les images. Tu ne dois JAMAIS ajouter de résultat, de mesure ou de diagnostic.
Signale uniquement :
1. une incohérence entre RÉSULTATS et CONCLUSION que des règles simples ne détectent pas ;
2. une phrase probablement déformée par la dictée vocale (mot absurde, négation perdue).
Pour chaque problème, cite EXACTEMENT (copie mot pour mot) le passage concerné.
Si tout est correct, renvoie une liste vide.

Réponds UNIQUEMENT en JSON :
{{"issues": [{{"type": "incoherence|dictee", "explication": "...", "citation": "..."}}]}}

COMPTE RENDU :
\"\"\"
{report}
\"\"\"
"""

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _call_ollama(prompt: str, model: str, timeout: int = 120) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "format": "json", "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["response"]


def validate_llm_output(raw: str, report: str, existing: List[Issue]) -> List[Issue]:
    """Keep only grounded, non-inventive, non-duplicate LLM issues."""
    try:
        data = json.loads(raw)
        items = data.get("issues", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    report_numbers = set(_NUM.findall(report))
    covered = [(e.start, e.end) for i in existing for e in i.evidence]
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        quote = str(it.get("citation", "")).strip()
        expl = str(it.get("explication", "")).strip()
        if len(quote) < 4 or not expl:
            continue
        start = report.find(quote)
        if start < 0:
            continue  # not verbatim: possibly hallucinated
        if set(_NUM.findall(expl)) - report_numbers:
            continue  # explanation introduces a number not in the report
        end = start + len(quote)
        if any(s <= start and end <= e for s, e in covered):
            continue  # already flagged by a rule
        kind = it.get("type", "incoherence")
        out.append(Issue(
            rule_id="LLM-" + ("DICT" if kind == "dictee" else "COH"),
            severity="warning", category="Vérification IA",
            title=expl[:160], message="Suggestion de l'IA, à confirmer par le radiologue.",
            evidence=[Evidence("", start, end)], source="llm",
        ))
    return out


def llm_check(report: str, exam_cfg: dict, existing: List[Issue],
              model: Optional[str] = None) -> List[Issue]:
    try:
        raw = _call_ollama(PROMPT.format(exam=exam_cfg["label"], report=report), model or DEFAULT_MODEL)
    except Exception as exc:  # LLM is optional: never block the rule results
        print(f"[relecto] LLM step skipped: {exc}")
        return []
    return validate_llm_output(raw, report, existing)
