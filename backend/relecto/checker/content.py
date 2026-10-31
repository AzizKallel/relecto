"""Load the clinical content (vocabulary + per-exam rule packs) from YAML.

The content lives in `clinical-content/` and is owned by radiologists. Code
never hard-codes medical vocabulary: it reads it from here, validates it, and
records the versions used on every check (traceability).
"""

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml

SEVERITIES = ("critical", "warning", "info")
SIDE_CODES = {"bilateral": "B", "right": "R", "left": "L"}

# Technical patterns: not clinical content, kept in code.
MEASURE = r"(?P<val>\d+(?:[.,]\d+)?)(?:\s*x\s*\d+(?:[.,]\d+)?){0,2}\s*(?P<unit>mm|cm|millimetres?|centimetres?)\b"
CLAUSE_BREAK = r"[,;:()]|\bmais\b|\bavec\b|\bassocie(?:e)?s? a\b"
ENUM_START = r"^\s*(?:de |d'|des |du |ni |ou |et de |et d')"


class ContentError(ValueError):
    """Invalid clinical content: the message names the file and the entry."""


@dataclass(frozen=True)
class FindingType:
    key: str
    label_fr: str
    pattern: str
    gender: str
    requires_size: bool
    significant: bool


@dataclass
class Lexicon:
    version: str
    findings: List[FindingType]
    finding_res: list            # [(FindingType, compiled regex)]
    side_res: list               # [("R"|"L"|"B", compiled regex)]
    neg_pre: "re.Pattern"
    neg_post: "re.Pattern"
    implied_side: list           # [(compiled regex, side code, message)]
    measure: "re.Pattern" = field(default_factory=lambda: re.compile(MEASURE))
    clause: "re.Pattern" = field(default_factory=lambda: re.compile(CLAUSE_BREAK))
    enum_start: "re.Pattern" = field(default_factory=lambda: re.compile(ENUM_START))


@dataclass
class RulePack:
    exam: str
    label: str
    version: str
    modality: str
    required_sections: List[str]
    technique: Optional[dict]
    rules: Dict[str, dict]

    def enabled(self, rule_id: str) -> bool:
        return bool(self.rules.get(rule_id, {}).get("enabled", False))

    def severity(self, rule_id: str, default: str) -> str:
        return self.rules.get(rule_id, {}).get("severity", default)

    def config(self, rule_id: str) -> dict:
        return self.rules.get(rule_id, {})


def content_dir() -> Path:
    env = os.environ.get("RELECTO_CONTENT_DIR")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        cand = parent / "clinical-content"
        if cand.is_dir():
            return cand
    raise ContentError("clinical-content/ folder not found; set RELECTO_CONTENT_DIR")


def _compile(pattern: str, where: str) -> "re.Pattern":
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ContentError(f"{where}: invalid pattern {pattern!r} ({exc})") from exc


def _require(d: dict, key: str, where: str):
    if not isinstance(d, dict) or key not in d:
        raise ContentError(f"{where}: missing '{key}'")
    return d[key]


def load_lexicon(path: Path) -> Lexicon:
    where = path.name
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    findings, finding_res, keys = [], [], set()
    for i, f in enumerate(_require(data, "findings", where)):
        w = f"{where} findings[{i}]"
        key = _require(f, "key", w)
        if key in keys:
            raise ContentError(f"{w}: duplicate key '{key}'")
        keys.add(key)
        forms = _require(f, "forms", w)
        if f.get("gender") not in ("m", "f"):
            raise ContentError(f"{w}: gender must be m or f")
        pattern = "|".join(forms)
        ft = FindingType(key, f.get("label", key), pattern, f["gender"],
                         bool(f.get("requires_size", False)), bool(f.get("significant", False)))
        findings.append(ft)
        finding_res.append((ft, _compile(r"\b(?:" + pattern + r")\b", w)))

    sides = _require(data, "sides", where)
    side_res = []
    for name in ("bilateral", "right", "left"):
        forms = _require(sides, name, f"{where} sides")
        side_res.append((SIDE_CODES[name], _compile(r"\b(?:" + "|".join(forms) + r")\b", f"{where} sides.{name}")))

    neg = _require(data, "negation", where)
    neg_pre = _compile("(?:" + "|".join(_require(neg, "before", f"{where} negation")) + ")", f"{where} negation.before")
    neg_post = _compile("(?:" + "|".join(_require(neg, "after", f"{where} negation")) + ")", f"{where} negation.after")

    implied = []
    for i, item in enumerate(data.get("implied_side", [])):
        w = f"{where} implied_side[{i}]"
        side = SIDE_CODES.get(item.get("side"))
        if side not in ("R", "L"):
            raise ContentError(f"{w}: side must be right or left")
        implied.append((_compile(r"\b" + _require(item, "anatomy", w) + r"\b", w), side, _require(item, "message", w)))

    return Lexicon(str(_require(data, "version", where)), findings, finding_res, side_res, neg_pre, neg_post, implied)


def load_pack(path: Path) -> RulePack:
    where = path.name
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = _require(data, "rules", where) or {}
    for rule_id, cfg in rules.items():
        if not isinstance(cfg, dict):
            raise ContentError(f"{where} rules.{rule_id}: expected a mapping")
        if cfg.get("severity", "warning") not in SEVERITIES:
            raise ContentError(f"{where} rules.{rule_id}: severity must be one of {SEVERITIES}")
        for key in ("forbidden", "ignore_sentences_with"):
            for p in cfg.get(key, []):
                _compile(p, f"{where} rules.{rule_id}.{key}")
    tech = data.get("technique")
    if tech:
        for p in _require(tech, "any_of", f"{where} technique"):
            _compile(p, f"{where} technique.any_of")
    return RulePack(
        exam=_require(data, "exam", where), label=data.get("label", data["exam"]),
        version=str(_require(data, "version", where)), modality=data.get("modality", "OT"),
        required_sections=list(data.get("required_sections", [])), technique=tech, rules=rules,
    )


@dataclass
class Content:
    lexicon: Lexicon
    packs: Dict[str, RulePack]

    def pack(self, exam: str) -> RulePack:
        if exam not in self.packs:
            raise ValueError(f"Unknown exam '{exam}'. Known: {', '.join(sorted(self.packs))}")
        return self.packs[exam]


@lru_cache(maxsize=1)
def get_content() -> Content:
    root = content_dir()
    lexicon = load_lexicon(root / "lexicon" / "fr.yaml")
    packs = {}
    for path in sorted((root / "rule-packs").glob("*.yaml")):
        pack = load_pack(path)
        if pack.exam in packs:
            raise ContentError(f"{path.name}: exam '{pack.exam}' defined twice")
        packs[pack.exam] = pack
    if not packs:
        raise ContentError("no rule packs found")
    return Content(lexicon, packs)


def reload_content() -> Content:
    """Re-read the YAML files (after an edit from the rules editor)."""
    get_content.cache_clear()
    return get_content()
