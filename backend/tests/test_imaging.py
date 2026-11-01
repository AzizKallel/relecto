"""Imaging pipeline tests.

Synthetic scans have a known answer (side, size), so geometry errors — the
classic source of wrong-side reports — are caught without any patient data.
The LIDC test runs only if public test scans were downloaded to ../dicom-test.
"""

import unittest
from pathlib import Path

import numpy as np

from relecto.imaging.consistency import image_vs_report
from relecto.imaging.draft import draft_ct_chest
from relecto.imaging.findings import measure
from relecto.imaging.volume import CTVolume

REPO = Path(__file__).resolve().parents[2]
LIDC = REPO.parent / "dicom-test" / "LIDC-IDRI-0001"


def synthetic_ct(nodule_side="L", radius_mm=6.0, flip_rows=False):
    """Body 300 x 220 mm, two lungs, one spherical 0 HU nodule; 1 mm voxels, 120 slices."""
    k, i, j = np.mgrid[0:120, 0:256, 0:320]
    hu = np.full(k.shape, -1000, np.int16)
    x = j - 160.0            # mm from midline, + = patient left when rows point to +x
    y = i - 128.0
    body = (x / 150) ** 2 + (y / 110) ** 2 <= 1
    hu[body] = 40
    lung_r = ((x + 70) / 55) ** 2 + (y / 80) ** 2 <= 1
    lung_l = ((x - 70) / 55) ** 2 + (y / 80) ** 2 <= 1
    zl = (k > 10) & (k < 110)
    hu[(lung_r | lung_l) & zl] = -850
    cx = 70 if nodule_side == "L" else -70
    sphere = (x - cx) ** 2 + y ** 2 + (k - 60) ** 2 <= radius_mm ** 2
    hu[sphere] = 0
    mask = sphere.copy()
    row_dir = np.array([1.0, 0, 0])
    origin = np.array([-160.0, -128.0, -60.0])
    if flip_rows:  # same anatomy stored mirrored: column index now runs towards patient right
        hu, mask = hu[:, :, ::-1].copy(), mask[:, :, ::-1].copy()
        row_dir = np.array([-1.0, 0, 0])
        origin = np.array([159.0, -128.0, -60.0])
    ct = CTVolume(hu=hu, origin=origin, row_dir=row_dir, col_dir=np.array([0, 1.0, 0]),
                  normal=np.array([0, 0, 1.0]),
                  spacing=(1.0, 1.0, 1.0), slice_positions=np.arange(120) - 60.0, sop_uids=[], series_uid="synthetic",
                  meta={"contrast": False, "slice_thickness_mm": 1.0, "patient_id": "SYNTH"})
    return ct, mask


class TestGeometry(unittest.TestCase):
    def test_left_nodule_is_left(self):
        ct, m = synthetic_ct("L")
        self.assertEqual(measure(m, ct, "test", "N1").side, "L")

    def test_right_nodule_is_right(self):
        ct, m = synthetic_ct("R")
        self.assertEqual(measure(m, ct, "test", "N1").side, "R")

    def test_mirrored_storage_keeps_the_true_side(self):
        for side in ("L", "R"):
            ct, m = synthetic_ct(side, flip_rows=True)
            self.assertEqual(measure(m, ct, "test", "N1").side, side, side)

    def test_diameter_and_volume(self):
        ct, m = synthetic_ct("L", radius_mm=6.0)
        f = measure(m, ct, "test", "N1")
        self.assertAlmostEqual(f.long_axis_mm, 12.0, delta=1.5)
        self.assertAlmostEqual(f.volume_mm3, 4 / 3 * np.pi * 6 ** 3, delta=0.15 * 4 / 3 * np.pi * 6 ** 3)
        self.assertEqual(f.zone, "moyen")


class TestDraftAndChecks(unittest.TestCase):
    def setUp(self):
        ct, m = synthetic_ct("L", radius_mm=6.0)
        self.ct, self.f = ct, measure(m, ct, "test", "N1")

    def test_draft_states_only_measured_facts(self):
        d = draft_ct_chest(self.ct, [self.f])
        self.assertIn("poumon gauche", d)
        self.assertIn("sans injection", d)
        self.assertIn("[à compléter par le radiologue]", d)
        self.assertNotIn("Pas d'épanchement", d)  # never invents normal findings

    def test_draft_passes_the_checker(self):
        from relecto.checker import run_check
        d = draft_ct_chest(self.ct, [self.f])
        self.assertEqual([i.rule_id for i in run_check(d, "ct_chest").issues], [])
        self.assertEqual(image_vs_report(d, [self.f]), [])

    def test_finding_removed_by_radiologist_is_flagged(self):
        d = draft_ct_chest(self.ct, [self.f])
        edited = "\n".join(l for l in d.splitlines() if "poumon gauche, mesurant" not in l)
        ids = [i.rule_id for i in image_vs_report(edited, [self.f])]
        self.assertEqual(ids, ["IMG-001"])

    def test_wrong_side_written_by_radiologist_is_flagged(self):
        d = draft_ct_chest(self.ct, [self.f]).replace("du poumon gauche", "du poumon droit")
        issues = image_vs_report(d, [self.f])
        self.assertEqual({i.rule_id for i in issues}, {"IMG-002"})
        self.assertEqual(issues[0].severity, "critical")
        from relecto.checker import apply_suggestion
        fixed = d
        for i in sorted(issues, key=lambda i: -i.suggestion.start):
            fixed = apply_suggestion(fixed, i)
        self.assertEqual(fixed, draft_ct_chest(self.ct, [self.f]))
        self.assertEqual(image_vs_report(fixed, [self.f]), [])

    def test_text_fix_never_contradicts_the_image(self):
        from relecto.checker import run_check
        from relecto.imaging.consistency import reconcile_with_images
        d = draft_ct_chest(self.ct, [self.f])
        first = d.index("du poumon gauche")
        d = d[:first] + "du poumon droit" + d[first + len("du poumon gauche"):]   # error in Résultats only
        text_issues = run_check(d, "ct_chest").issues
        lat = [i for i in text_issues if i.rule_id == "LAT-001"]
        self.assertTrue(lat and lat[0].suggestion.replacement.startswith("droit"))   # text alone copies the error
        lat = [i for i in reconcile_with_images(text_issues, [], [self.f]) if i.rule_id == "LAT-001"]
        self.assertIsNone(lat[0].suggestion)
        self.assertEqual([i.rule_id for i in image_vs_report(d, [self.f])], ["IMG-002"])

    def test_other_size_on_other_side_is_not_called_wrong_side(self):
        d = ("RÉSULTATS :\nNodule pulmonaire du lobe inférieur droit, mesurant 40 x 35 mm.\n"
             "CONCLUSION :\nNodule pulmonaire droit de 40 mm.\n")
        self.assertEqual([i.rule_id for i in image_vs_report(d, [self.f])], ["IMG-001"])
        self.assertNotIn("IMG-002", [i.rule_id for i in image_vs_report(d, [self.f])])


@unittest.skipUnless(LIDC.is_dir(), "public test scans not downloaded (./tools/get-test-ct.sh)")
class TestLIDC(unittest.TestCase):
    def test_lidc_0001_matches_the_readers(self):
        from relecto.imaging.pipeline import analyze_study
        a = analyze_study(LIDC)
        self.assertEqual(len(a.findings), 1)
        f = a.findings[0]
        self.assertEqual((f.side, f.readers), ("L", 4))
        self.assertTrue(28 <= f.long_axis_mm <= 36, f.long_axis_mm)       # readers: 32.0–33.7 mm
        self.assertTrue(6000 <= f.volume_mm3 <= 8000, f.volume_mm3)       # readers: 6146–7869 mm³
        self.assertEqual(a.ct.qc, [])
        self.assertEqual(a.issues, [])


if __name__ == "__main__":
    unittest.main()
