"""Image AI models, run locally (CPU or GPU). No image ever leaves the machine.

Models (open source, self-hosted):
  - Lung lobes:     TotalSegmentator "total" task, lobe classes   (Wasserthal et al., Radiology: AI 2023)
  - Lung nodules:   TotalSegmentator "lung_nodules" task          (nnU-Net, trained on public lung CT)

Weights are downloaded once into TOTALSEG_HOME_DIR (default ~/.totalsegmentator). Licence of
the weights must be checked for each model before commercial use (see docs/MODELS.md).

Everything here works in the CT volume's own voxel grid (slice, row, column). The conversion
to NIfTI writes the true patient geometry, so the models see correctly oriented anatomy and
their masks come back aligned voxel for voxel.
"""

import os
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np

from .volume import CTVolume

LOBES = {  # our code -> (TotalSegmentator class, French name, side)
    1: ("lung_upper_lobe_right", "lobe supérieur droit", "R"),
    2: ("lung_middle_lobe_right", "lobe moyen", "R"),
    3: ("lung_lower_lobe_right", "lobe inférieur droit", "R"),
    4: ("lung_upper_lobe_left", "lobe supérieur gauche", "L"),
    5: ("lung_lower_lobe_left", "lobe inférieur gauche", "L"),
}


@lru_cache(maxsize=1)
def available() -> bool:
    if os.environ.get("RELECTO_DISABLE_AI") == "1":
        return False
    try:
        import totalsegmentator  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def versions() -> dict:
    out = {}
    try:
        from importlib.metadata import version
        out["totalsegmentator"] = version("TotalSegmentator")
        out["torch"] = version("torch")
        lim = patch_limit()
        out["tile"] = "x".join(map(str, lim)) if lim else "native"
    except Exception:
        pass
    return out


def device() -> str:
    d = os.environ.get("RELECTO_AI_DEVICE")
    if d:
        return d
    try:
        import torch
        if torch.cuda.is_available():
            return "gpu"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _ram_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (ValueError, OSError, AttributeError):
        return 16.0


def patch_limit():
    """Largest sliding-window tile for the networks. The nodule network's native tile
    (128 x 192 x 192 voxels) needs ~10 GB of RAM on CPU; smaller tiles with overlap give
    the same model on ordinary machines, at some cost in speed. RELECTO_AI_MAX_PATCH="z,y,x"
    overrides; "none" disables the limit."""
    v = os.environ.get("RELECTO_AI_MAX_PATCH")
    if v:
        return None if v.lower() == "none" else [int(x) for x in v.split(",")]
    if device() != "cpu":
        return None
    ram = _ram_gb()
    if ram < 12:
        return [64, 96, 96]
    if ram < 24:
        return [96, 160, 160]
    return None


def _install_patch_limit():
    from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
    if getattr(nnUNetPredictor, "_relecto_patched", False):
        return
    original = nnUNetPredictor.initialize_from_trained_model_folder

    def initialize(self, *a, **kw):
        original(self, *a, **kw)
        limit = patch_limit()
        if limit:
            cfg = self.configuration_manager.configuration
            cfg["patch_size"] = [min(p, l) for p, l in zip(cfg["patch_size"], limit)]

    nnUNetPredictor.initialize_from_trained_model_folder = initialize
    nnUNetPredictor._relecto_patched = True


def to_nifti(ct: CTVolume):
    """CT volume -> NIfTI image (RAS affine). Array axes become (column j, row i, slice k)."""
    import nibabel as nib
    ds, dr, dc = ct.spacing
    data = np.ascontiguousarray(np.transpose(ct.hu, (2, 1, 0)))   # (j, i, k)
    aff = np.eye(4)
    aff[:3, 0] = ct.row_dir * dc
    aff[:3, 1] = ct.col_dir * dr
    aff[:3, 2] = ct.normal * ds
    aff[:3, 3] = ct.origin
    aff[:2, :] *= -1            # LPS -> RAS
    img = nib.Nifti1Image(data.astype(np.int16), aff)
    img.header.set_xyzt_units("mm")
    return img


def from_nifti(img, ct: CTVolume) -> np.ndarray:
    """Model output (same grid as `to_nifti`) -> labels in (slice, row, column)."""
    arr = np.asarray(img.dataobj)
    if arr.shape != (ct.hu.shape[2], ct.hu.shape[1], ct.hu.shape[0]):
        raise ValueError(f"Model output grid {arr.shape} does not match the CT {ct.hu.shape}")
    return np.ascontiguousarray(np.transpose(arr, (2, 1, 0))).astype(np.uint8)


def _run(ct: CTVolume, task: str, **kw):
    from totalsegmentator.python_api import totalsegmentator
    _install_patch_limit()
    img = to_nifti(ct)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "ct.nii.gz"
        import nibabel as nib
        nib.save(img, src)
        out = totalsegmentator(str(src), None, task=task, ml=True, device=device(), quiet=True,
                               nr_thr_resamp=1, nr_thr_saving=1, **kw)
    return from_nifti(out, ct)


def _class_ids(task: str) -> dict:
    from totalsegmentator.map_to_binary import class_map
    return {name: idx for idx, name in class_map[task].items()}


def segment_lobes(ct: CTVolume, fast: bool | None = None) -> np.ndarray:
    """Label volume: 0 background, 1..5 lobes (see LOBES)."""
    fast = (device() == "cpu") if fast is None else fast
    names = [v[0] for v in LOBES.values()]
    raw = _run(ct, "total", fast=fast, roi_subset=names)
    ids = _class_ids("total")
    out = np.zeros_like(raw)
    for code, (name, _, _) in LOBES.items():
        out[raw == ids[name]] = code
    return out


def detect_nodules(ct: CTVolume, force_split: bool = False) -> np.ndarray:
    """Binary mask of lung nodules. force_split processes the scan in 3 parts (less memory)."""
    raw = _run(ct, "lung_nodules", force_split=force_split)
    return raw == _class_ids("lung_nodules")["lung_nodules"]


def lobe_at(lobes: np.ndarray, ct: CTVolume, mask: np.ndarray, max_mm: float = 15.0):
    """Lobe containing most of the finding; else the nearest lobe within max_mm (juxtapleural nodules)."""
    vals = lobes[mask]
    vals = vals[vals > 0]
    if len(vals):
        return int(np.bincount(vals).argmax())
    ks, ii, jj = np.nonzero(mask)
    k0, i0, j0 = int(ks.mean()), int(ii.mean()), int(jj.mean())
    ds, dr, dc = ct.spacing
    r = (int(max_mm / ds) + 1, int(max_mm / dr) + 1, int(max_mm / dc) + 1)
    sl = tuple(slice(max(0, c - rr), c + rr + 1) for c, rr in zip((k0, i0, j0), r))
    sub = lobes[sl]
    if not sub.any():
        return None
    kk, iii, jjj = np.nonzero(sub)
    d = ((kk + sl[0].start - k0) * ds) ** 2 + ((iii + sl[1].start - i0) * dr) ** 2 + ((jjj + sl[2].start - j0) * dc) ** 2
    n = int(np.argmin(d))
    return int(sub[kk[n], iii[n], jjj[n]]) if d[n] <= max_mm ** 2 else None
