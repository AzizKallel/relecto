# Image AI models

All models run locally on the edge node. No image is sent to any external service.

| Task | Model | Source | Licence (verify before commercial launch) |
|---|---|---|---|
| Lung lobes (5 lobes) | TotalSegmentator `total` (3 mm on CPU, 1.5 mm on GPU) | Wasserthal et al., *Radiology: AI* 2023 | Code Apache-2.0; open weights |
| Lung nodules | TotalSegmentator `lung_nodules` (nnU-Net ResEnc) | TotalSegmentator v2.5 weights | Open task (not in the licensed list); confirm terms with the authors |

How they are used:

1. The CT is converted to NIfTI with its true patient geometry (`imaging/ai.py: to_nifti`).
2. Lobes and nodules are segmented; masks come back on the CT grid voxel for voxel.
3. Each nodule component ≥ 3 mm becomes a finding, measured by our own code (long/short axis,
   volume, mean HU), located by lobe, checked against the geometric side.
4. The draft states only these measurements. The radiologist confirms or rejects each finding.

## Measured performance

Run on public LIDC-IDRI scans against the 4 radiologists' outlines:

```bash
./relecto evaluate-ai ../dicom-test/LIDC-IDRI-000* --out evaluation.json
```

Results are recorded in `docs/VALIDATION.md` for every model or hardware change. Public data
is not Tunisian data: before clinical use, validate on 100+ local scans annotated by the
centre's radiologists (sensitivity, false positives per scan, size error).

## Replacing a model

Models sit behind two functions (`segment_lobes`, `detect_nodules`). A new model must:
return masks on the CT grid, pass `tests/test_imaging.py`, and beat the current one on the
evaluation set before it is enabled. Its name and version are stamped on every report.
