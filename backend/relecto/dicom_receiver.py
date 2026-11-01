"""DICOM receiver (C-STORE SCP): scanners and PACS send studies straight to Relecto.

Configure the modality / PACS with:  AE title RELECTO, this machine's address, port 11112.
Only AE titles listed in RELECTO_DICOM_ALLOWED_AES (comma-separated) are accepted when set.
Received files land in an inbox; when the sender closes the connection the study is
registered and queued for analysis.
"""

import logging
import os
import threading
from pathlib import Path

from pynetdicom import AE, AllStoragePresentationContexts, evt
from pynetdicom.sop_class import Verification

from .db import data_dir
from .studies import ingest_folder

log = logging.getLogger("relecto.dicom")


def _inbox() -> Path:
    d = data_dir() / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _allowed():
    v = os.environ.get("RELECTO_DICOM_ALLOWED_AES", "").strip()
    return {a.strip() for a in v.split(",") if a.strip()} or None


def handle_store(event):
    ds = event.dataset
    ds.file_meta = event.file_meta
    folder = _inbox() / str(event.assoc.native_id)
    folder.mkdir(parents=True, exist_ok=True)
    ds.save_as(folder / f"{ds.SOPInstanceUID}.dcm", write_like_original=False)
    return 0x0000


def handle_assoc_request(event):
    allowed = _allowed()
    calling = event.assoc.requestor.ae_title.strip() if isinstance(event.assoc.requestor.ae_title, str) \
        else event.assoc.requestor.ae_title.decode().strip()
    if allowed and calling not in allowed:
        log.warning("Rejected association from AE %s", calling)
        event.assoc.abort()


def handle_release(event):
    folder = _inbox() / str(event.assoc.native_id)
    if folder.exists():
        def work():
            try:
                ids = ingest_folder(folder, source="dicom", move=True)
                log.info("Received %d study(ies) from %s", len(ids), event.assoc.requestor.ae_title)
            finally:
                for p in sorted(folder.rglob("*"), reverse=True):
                    p.unlink() if p.is_file() else p.rmdir()
                folder.rmdir()
        threading.Thread(target=work, daemon=True).start()


def serve(port: int = 11112, ae_title: str = "RELECTO"):
    ae = AE(ae_title=ae_title)
    ae.supported_contexts = AllStoragePresentationContexts
    ae.add_supported_context(Verification)
    handlers = [(evt.EVT_C_STORE, handle_store), (evt.EVT_REQUESTED, handle_assoc_request),
                (evt.EVT_RELEASED, handle_release)]
    log.info("DICOM receiver %s listening on port %d", ae_title, port)
    ae.start_server(("0.0.0.0", port), block=True, evt_handlers=handlers)
