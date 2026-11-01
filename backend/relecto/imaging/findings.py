"""Turn nodule masks (from reader annotations or an AI model) into structured findings.

A finding states only what is measured: size, position, side, lung zone.
Lobe-level location needs a lung-lobe segmentation model (next step); until
then the vertical position is reported as a third of the lung height.
"""

from dataclasses import asdict, dataclass, field
from typing import List, Optional

import numpy as np

from .seg import Annotation
from .volume import CTVolume

ZONES_FR = ["inférieur", "moyen", "supérieur"]


@dataclass
class NoduleFinding:
    finding_id: str
    kind: str                     # "nodule"
    side: str                     # "R" | "L"
    zone: str                     # supérieur | moyen | inférieur (third of lung height)
    long_axis_mm: float           # longest axial diameter
    short_axis_mm: float          # longest diameter perpendicular to it, same slice
    mean_diameter_mm: float       # (long + short) / 2, as Fleischner recommends for small nodules
    volume_mm3: float
    centroid_lps: List[float]
    key_slice: int                # slice index (0-based) with the largest cross-section
    mean_hu: float
    source: str                   # "reference_readers" | model name
    readers: int = 1              # how many readers outlined it (reference data)
    confidence: Optional[float] = None
    contributors: List[str] = field(default_factory=list)
    centroid_vox: Optional[List[float]] = None   # (slice, row, column) for the viewer
    lobe: Optional[str] = None     # e.g. "lobe supérieur droit", from the lobe segmentation model

    @property
    def location_fr(self) -> str:
        side = "droit" if self.side == "R" else "gauche"
        return self.lobe if self.lobe else f"tiers {self.zone} du poumon {side}"

    def to_dict(self):
        return asdict(self)


def _axial_diameters(mask2d: np.ndarray, dr: float, dc: float):
    ii, jj = np.nonzero(mask2d)
    if len(ii) == 0:
        return 0.0, 0.0
    pts = np.stack([ii * dr, jj * dc], 1)
    if len(pts) > 1500:
        pts = pts[np.random.default_rng(0).choice(len(pts), 1500, replace=False)]
    d2 = ((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1)
    a, b = np.unravel_index(np.argmax(d2), d2.shape)
    long_ = float(np.sqrt(d2[a, b]))
    if long_ == 0:
        return max(dr, dc), max(dr, dc)
    u = (pts[b] - pts[a]) / long_
    perp = pts @ np.array([-u[1], u[0]])
    short = float(perp.max() - perp.min())
    # add one pixel: extents are measured centre-to-centre
    return long_ + (dr + dc) / 2, short + (dr + dc) / 2


def _body_and_lungs(ct: CTVolume):
    """Body = tissue denser than -500 HU with its holes filled; lungs = air inside the body,
    keeping only the two largest air regions (excludes air outside the patient and bowel gas)."""
    from scipy import ndimage

    body = np.zeros(ct.hu.shape, bool)
    tissue = ct.hu > -500
    for k in range(len(tissue)):
        body[k] = ndimage.binary_fill_holes(tissue[k])
    air = body & (ct.hu < -400)
    lab, n = ndimage.label(air)
    if n:
        sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
        keep = 1 + np.argsort(sizes)[::-1][:2]
        air = np.isin(lab, keep[sizes[keep - 1] > 50_000 / ct.voxel_volume_mm3])
    return body, air


def lung_z_range(ct: CTVolume, lungs: np.ndarray):
    area = lungs.reshape(len(lungs), -1).sum(1) * ct.spacing[1] * ct.spacing[2]
    rows = np.nonzero(area > 2000)[0]  # > 20 cm² of lung on the slice
    if len(rows) == 0:
        return 0, len(lungs) - 1
    return int(rows.min()), int(rows.max())


def measure(mask: np.ndarray, ct: CTVolume, source: str, fid: str, body=None, lungs=None) -> NoduleFinding:
    if body is None or lungs is None:
        body, lungs = _body_and_lungs(ct)
    ks, ii, jj = np.nonzero(mask)
    cen_vox = np.array([ks.mean(), ii.mean(), jj.mean()])
    cen = ct.voxel_to_lps(*cen_vox)
    areas = mask.reshape(len(mask), -1).sum(1)
    key = int(np.argmax(areas))
    long_, short = _axial_diameters(mask[key], ct.spacing[1], ct.spacing[2])

    # Side: compare with the body midline on the key slice, in patient coordinates.
    bj = np.nonzero(body[key].any(0))[0]
    mid_j = (bj.min() + bj.max()) / 2 if len(bj) else ct.hu.shape[2] / 2
    mid_x = ct.voxel_to_lps(key, cen_vox[1], mid_j)[0]
    side = "L" if cen[0] > mid_x else "R"  # LPS: +x is the patient's left

    lo, hi = lung_z_range(ct, lungs)
    sup_is_high_k = (ct.normal @ np.array([0, 0, 1.0])) > 0
    frac = (cen_vox[0] - lo) / max(hi - lo, 1)
    frac = frac if sup_is_high_k else 1 - frac
    zone = ZONES_FR[int(np.clip(frac * 3, 0, 2))]

    return NoduleFinding(
        finding_id=fid, kind="nodule", side=side, zone=zone,
        long_axis_mm=round(long_, 1), short_axis_mm=round(short, 1), mean_diameter_mm=round((long_ + short) / 2, 1),
        volume_mm3=round(float(mask.sum()) * ct.voxel_volume_mm3, 1),
        centroid_lps=[round(float(v), 1) for v in cen], key_slice=key,
        centroid_vox=[round(float(v), 1) for v in cen_vox],
        mean_hu=round(float(ct.hu[mask].mean()), 0), source=source,
    )


def findings_from_reader_annotations(anns: List[Annotation], ct: CTVolume, merge_mm: float = 10.0,
                                     with_masks: bool = False):
    """One finding per nodule: annotations whose centroids are close are the same nodule seen by several readers."""
    body, lungs = _body_and_lungs(ct)
    measured = [(a, measure(a.mask, ct, "reference_readers", "tmp", body, lungs)) for a in anns]
    clusters: List[list] = []
    for a, m in measured:
        c = np.array(m.centroid_lps)
        for cl in clusters:
            if np.linalg.norm(np.array(cl[0][1].centroid_lps) - c) <= max(merge_mm, 0.5 * m.long_axis_mm):
                cl.append((a, m))
                break
        else:
            clusters.append([(a, m)])
    out = []
    for cl in clusters:
        union = np.logical_or.reduce([a.mask for a, _ in cl])
        votes = np.sum([a.mask for a, _ in cl], axis=0)
        # consensus = voxels outlined by at least half of the readers who saw it
        consensus = votes >= max(1, int(np.ceil(len(cl) / 2)))
        f = measure(consensus if consensus.any() else union, ct, "reference_readers", "", body, lungs)
        f.readers = len(cl)
        f.contributors = [a.source for a, _ in cl]
        out.append((f, consensus if consensus.any() else union))
    out.sort(key=lambda fm: -fm[0].long_axis_mm)
    for n, (f, _) in enumerate(out, 1):
        f.finding_id = f"N{n}"
    return out if with_masks else [f for f, _ in out]


def findings_from_mask(mask: np.ndarray, ct: CTVolume, source: str, min_mm: float = 3.0,
                       max_findings: int = 30) -> List[NoduleFinding]:
    """One finding per connected component of a model's nodule mask (tiny specks dropped)."""
    from scipy import ndimage

    lab, n = ndimage.label(mask, structure=np.ones((3, 3, 3)))
    if n == 0:
        return []
    body, lungs = _body_and_lungs(ct)
    out = []
    for c in range(1, n + 1):
        m = lab == c
        f = measure(m, ct, source, "", body, lungs)
        if f.long_axis_mm >= min_mm:
            out.append((f, m))
    out.sort(key=lambda fm: -fm[0].long_axis_mm)
    out = out[:max_findings]
    for k, (f, _) in enumerate(out, 1):
        f.finding_id = f"N{k}"
    return out
