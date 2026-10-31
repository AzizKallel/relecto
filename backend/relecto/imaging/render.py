"""Render key slices with finding markers, in radiological display convention."""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .findings import NoduleFinding
from .volume import CTVolume


def window(hu: np.ndarray, level: float = -600, width: float = 1500) -> np.ndarray:
    lo, hi = level - width / 2, level + width / 2
    return (np.clip((hu - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)


def render_finding(ct: CTVolume, f: NoduleFinding, path: Path, scale: int = 2) -> Path:
    img2d = window(ct.hu[f.key_slice].astype(np.float32))
    # Radiological convention: patient's right on the image's left. With a standard
    # orientation (row direction = +x = patient left) the array is already displayed so;
    # otherwise flip so that the convention holds.
    flip = ct.row_dir[0] < 0
    if flip:
        img2d = img2d[:, ::-1]
    im = Image.fromarray(img2d).convert("RGB").resize((img2d.shape[1] * scale, img2d.shape[0] * scale), Image.BILINEAR)
    d = ImageDraw.Draw(im)
    v = ct.lps_to_voxel(np.array(f.centroid_lps))
    i, j = v[1], v[2]
    if flip:
        j = ct.hu.shape[2] - 1 - j
    r = max(f.long_axis_mm / min(ct.spacing[1:]) * 0.75, 8) * scale
    cx, cy = j * scale, i * scale
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(46, 196, 182), width=3)
    d.text((cx + r + 6, cy - r), f"{f.finding_id}  {f.long_axis_mm:.0f} mm", fill=(46, 196, 182))
    w, h = im.size
    d.text((12, h // 2), "R", fill=(255, 255, 255))
    d.text((w - 20, h // 2), "L", fill=(255, 255, 255))
    d.text((12, 12), f"Slice {f.key_slice + 1}/{ct.hu.shape[0]}  ·  {f.side}  ·  {f.zone}", fill=(200, 200, 200))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path
