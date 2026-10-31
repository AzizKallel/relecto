"""Load a CT series into a 3D volume with correct patient geometry.

Coordinates follow DICOM: patient LPS (x → patient Left, y → Posterior,
z → Superior), in millimetres. Getting this right is what makes right/left
and sizes trustworthy: we never assume array axes match anatomy.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
import pydicom

CT_IMAGE = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage


@dataclass
class CTVolume:
    hu: np.ndarray                 # (slices, rows, cols), Hounsfield units
    origin: np.ndarray             # LPS position of voxel (0, 0, 0), mm
    row_dir: np.ndarray            # unit vector along a row (column index increases)
    col_dir: np.ndarray            # unit vector down a column (row index increases)
    normal: np.ndarray             # slice stacking direction
    spacing: tuple                 # (slice, row, col) spacing in mm
    slice_positions: np.ndarray    # position of each slice along `normal`, mm
    sop_uids: List[str]
    series_uid: str
    meta: dict = field(default_factory=dict)
    qc: List[str] = field(default_factory=list)

    def voxel_to_lps(self, k: float, i: float, j: float) -> np.ndarray:
        """(slice k, row i, column j) -> patient LPS mm."""
        ds, dr, dc = self.spacing
        return self.origin + k * ds * self.normal + i * dr * self.col_dir + j * dc * self.row_dir

    def lps_to_voxel(self, p: np.ndarray) -> np.ndarray:
        d = np.asarray(p, float) - self.origin
        ds, dr, dc = self.spacing
        return np.array([d @ self.normal / ds, d @ self.col_dir / dr, d @ self.row_dir / dc])

    def slice_index_for(self, position_along_normal: float) -> Optional[int]:
        k = int(np.argmin(np.abs(self.slice_positions - position_along_normal)))
        return k if abs(self.slice_positions[k] - position_along_normal) <= 0.51 * self.spacing[0] else None

    @property
    def voxel_volume_mm3(self) -> float:
        return float(np.prod(self.spacing))


def _files(folder: Path) -> List[Path]:
    return [p for p in sorted(folder.rglob("*")) if p.is_file() and not p.name.startswith(".")]


def load_ct(folder) -> CTVolume:
    """Load the largest CT series found in `folder` (recursively)."""
    folder = Path(folder)
    headers = []
    for p in _files(folder):
        try:
            d = pydicom.dcmread(p, stop_before_pixels=True)
        except Exception:
            continue
        if getattr(d, "Modality", "") == "CT" and str(getattr(d, "SOPClassUID", "")).startswith(CT_IMAGE) \
                and hasattr(d, "ImagePositionPatient"):
            headers.append((p, d))
    if not headers:
        raise ValueError(f"No CT image series found in {folder}")

    by_series = {}
    for p, d in headers:
        by_series.setdefault(d.SeriesInstanceUID, []).append((p, d))
    series_uid, items = max(by_series.items(), key=lambda kv: len(kv[1]))

    iop = np.array(items[0][1].ImageOrientationPatient, float)
    row_dir, col_dir = iop[:3], iop[3:]
    normal = np.cross(row_dir, col_dir)
    normal /= np.linalg.norm(normal)
    qc = []
    for _, d in items:
        if not np.allclose(np.array(d.ImageOrientationPatient, float), iop, atol=1e-3):
            qc.append("Orientation differs between slices (tilted or mixed series).")
            break

    items.sort(key=lambda it: float(np.array(it[1].ImagePositionPatient, float) @ normal))
    positions = np.array([float(np.array(d.ImagePositionPatient, float) @ normal) for _, d in items])
    gaps = np.diff(positions)
    if len(gaps) and np.any(gaps <= 1e-3):
        qc.append("Duplicate slice positions found.")
    slice_spacing = float(np.median(gaps)) if len(gaps) else float(getattr(items[0][1], "SliceThickness", 1.0))
    if len(gaps) and np.any(np.abs(gaps - slice_spacing) > 0.1 * slice_spacing + 0.01):
        qc.append(f"Irregular slice spacing ({gaps.min():.2f}–{gaps.max():.2f} mm): possible missing slices.")

    first = items[0][1]
    dr, dc = (float(v) for v in first.PixelSpacing)  # PixelSpacing = [row spacing, column spacing]
    slices = []
    for p, _ in items:
        d = pydicom.dcmread(p)
        arr = d.pixel_array.astype(np.float32)
        slices.append(arr * float(getattr(d, "RescaleSlope", 1)) + float(getattr(d, "RescaleIntercept", 0)))
    hu = np.stack(slices).astype(np.int16)

    meta = {
        "patient_id": str(getattr(first, "PatientID", "")),
        "study_uid": str(getattr(first, "StudyInstanceUID", "")),
        "manufacturer": str(getattr(first, "Manufacturer", "")),
        "slice_thickness_mm": float(getattr(first, "SliceThickness", slice_spacing) or slice_spacing),
        "contrast": bool(str(getattr(first, "ContrastBolusAgent", "") or "").strip()),
        "kvp": str(getattr(first, "KVP", "")),
        "n_slices": len(items),
    }
    return CTVolume(hu=hu, origin=np.array(first.ImagePositionPatient, float), row_dir=row_dir, col_dir=col_dir,
                    normal=normal, spacing=(slice_spacing, dr, dc), slice_positions=positions,
                    sop_uids=[str(d.SOPInstanceUID) for _, d in items], series_uid=str(series_uid),
                    meta=meta, qc=qc)
