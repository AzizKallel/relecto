"""Read DICOM SEG annotations (e.g. LIDC-IDRI reader outlines) onto a CTVolume grid."""

from dataclasses import dataclass
from pathlib import Path
from typing import List

import numpy as np
import pydicom

from .volume import CTVolume

SEG_STORAGE = "1.2.840.10008.5.1.4.1.1.66.4"


@dataclass
class Annotation:
    source: str          # file / series the annotation came from
    label: str
    mask: np.ndarray     # boolean, same shape as the CT volume (sparse use)


def load_segmentations(folder, ct: CTVolume) -> List[Annotation]:
    out = []
    for p in sorted(Path(folder).rglob("*")):
        if not p.is_file():
            continue
        try:
            d = pydicom.dcmread(p, stop_before_pixels=True)
        except Exception:
            continue
        if str(getattr(d, "SOPClassUID", "")) != SEG_STORAGE:
            continue
        out.extend(_read_seg(pydicom.dcmread(p), ct, p))
    return out


def _read_seg(seg, ct: CTVolume, path: Path) -> List[Annotation]:
    if int(seg.Rows) != ct.hu.shape[1] or int(seg.Columns) != ct.hu.shape[2]:
        return []  # different in-plane grid: not supported yet
    frames = seg.pixel_array
    if frames.ndim == 2:
        frames = frames[None]
    labels = {int(s.SegmentNumber): str(getattr(s, "SegmentLabel", f"segment {s.SegmentNumber}"))
              for s in seg.SegmentSequence}
    masks = {n: np.zeros(ct.hu.shape, bool) for n in labels}
    origin_xy = ct.origin
    for f, fg in enumerate(seg.PerFrameFunctionalGroupsSequence):
        n = int(fg.SegmentIdentificationSequence[0].ReferencedSegmentNumber)
        ipp = np.array(fg.PlanePositionSequence[0].ImagePositionPatient, float)
        # in-plane offset must match the CT grid (same rows/cols origin)
        inplane = (ipp - origin_xy) - ((ipp - origin_xy) @ ct.normal) * ct.normal
        if np.linalg.norm(inplane) > 0.5 * min(ct.spacing[1:]):
            continue
        k = ct.slice_index_for(float(ipp @ ct.normal))
        if k is None or n not in masks:
            continue
        masks[n][k] |= frames[f].astype(bool)
    return [Annotation(f"{path.parent.name}", labels[n], m) for n, m in masks.items() if m.any()]
