"""Study intake: from a folder, an uploaded zip, or the DICOM receiver (scanner / PACS).

Files are copied into the edge node's data directory, grouped by StudyInstanceUID,
registered in the database and queued for analysis. Nothing leaves the machine.
"""

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List

import pydicom

from .db import Job, Study, audit, data_dir, session_scope

MAX_ZIP_BYTES = 4 * 1024 ** 3        # 4 GB uncompressed
MAX_FILES = 20_000
ANALYZABLE = {"CT"}                  # modalities with an image analysis pipeline today


def studies_dir() -> Path:
    d = data_dir() / "studies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _exam_for(modality: str, body: str, desc: str) -> str:
    text = f"{body} {desc}".lower()
    if modality == "MR" and any(w in text for w in ("brain", "head", "crane", "cerv", "encéph", "encep")):
        return "mri_brain"
    if modality == "CT" and any(w in text for w in ("chest", "thorax", "lung", "poumon", "thorac")):
        return "ct_chest"
    return "ct_chest" if modality == "CT" else "generic"


def _txt(d, tag) -> str:
    v = getattr(d, tag, "")
    return str(v).strip() if v is not None else ""


def ingest_folder(folder, source: str = "folder", user_id: str | None = None, move: bool = False,
                  queue: bool = True) -> List[str]:
    """Register every DICOM study found under `folder`. Returns the study ids (new or updated)."""
    folder = Path(folder)
    groups: dict = {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            d = pydicom.dcmread(p, stop_before_pixels=True)
        except Exception:
            continue
        uid = _txt(d, "StudyInstanceUID")
        if uid:
            groups.setdefault(uid, []).append((p, d))
    ids = []
    for uid, items in groups.items():
        ids.append(_register(uid, items, source, user_id, move, queue))
    return ids


def _register(uid, items, source, user_id, move, queue) -> str:
    first = next((d for _, d in items if _txt(d, "Modality") in ("CT", "MR")), items[0][1])
    with session_scope() as s:
        st = s.query(Study).filter(Study.study_uid == uid).one_or_none()
        new = st is None
        if new:
            modality = _txt(first, "Modality")
            st = Study(study_uid=uid, storage_path="", source=source,
                       patient_id=_txt(first, "PatientID"), patient_name=_txt(first, "PatientName").replace("^", " ").strip(),
                       patient_sex=_txt(first, "PatientSex"), patient_birth_date=_txt(first, "PatientBirthDate"),
                       accession=_txt(first, "AccessionNumber"), modality=modality,
                       body_part=_txt(first, "BodyPartExamined"), description=_txt(first, "StudyDescription"),
                       study_date=_txt(first, "StudyDate"))
            st.exam = _exam_for(modality, st.body_part or "", st.description or "")
            s.add(st)
            s.flush()
            st.storage_path = str(studies_dir() / st.id / "dicom")
        dest = Path(st.storage_path)
        dest.mkdir(parents=True, exist_ok=True)
        added = 0
        for p, d in items:
            series = _txt(d, "SeriesInstanceUID") or "series"
            target = dest / f"{_txt(d, 'Modality') or 'XX'}_{series}" / f"{_txt(d, 'SOPInstanceUID') or p.name}.dcm"
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            (shutil.move if move else shutil.copy2)(p, target)
            added += 1
        st.n_images = sum(1 for _ in dest.rglob("*.dcm"))
        audit(s, "study.received" if new else "study.images_added", "study", st.id, user_id=user_id,
              source=source, images=added)
        if new and st.modality not in ANALYZABLE:
            st.status = "ready"
            st.analysis = {"source": "none", "notes": [
                f"Pas d'analyse automatique pour la modalité {st.modality or 'inconnue'} : compte rendu manuel."]}
        elif queue and (new or added) and st.status not in ("signed",):
            st.status = "queued"
            s.add(Job(study_id=st.id, kind="analyze"))
        return st.id


def ingest_zip(fileobj, user_id: str | None = None) -> List[str]:
    """Safely unpack an uploaded zip (no path traversal, size and count limits) and ingest it."""
    with tempfile.TemporaryDirectory(dir=data_dir()) as tmp:
        tmp = Path(tmp)
        with zipfile.ZipFile(fileobj) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len(infos) > MAX_FILES:
                raise ValueError(f"Archive trop grande ({len(infos)} fichiers, maximum {MAX_FILES}).")
            if sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
                raise ValueError("Archive trop volumineuse (plus de 4 Go décompressés).")
            for n, info in enumerate(infos):
                out = tmp / f"{n:06d}.dcm"   # never trust names inside the archive
                with z.open(info) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        ids = ingest_folder(tmp, source="upload", user_id=user_id, move=True)
    if not ids:
        raise ValueError("Aucun fichier DICOM trouvé dans l'archive.")
    return ids
