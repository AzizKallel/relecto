"""Download public chest CT scans (DICOM) for development: LIDC-IDRI.

Source: NCI Imaging Data Commons (public cloud buckets, no account needed).
Licence: CC BY 3.0 — cite Armato et al. 2015, DOI 10.7937/K9/TCIA.2015.LO9QL9SX
(see docs/DATA_SOURCES.md). These are research scans with nodules annotated
by 4 thoracic radiologists (annotations come as DICOM SEG/SR series).

Usage (from the relecto folder):
    ./tools/get-test-ct.sh            # 5 patients, about 0.5 GB
    ./tools/get-test-ct.sh 10         # 10 patients

Files go OUTSIDE the repository, to ../dicom-test/, so they are never committed.
"""

import sys
from pathlib import Path

from idc_index import IDCClient

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
OUT = Path(__file__).resolve().parents[3] / "dicom-test"

client = IDCClient()
df = client.index
ct = df[(df.collection_id == "lidc_idri") & (df.Modality == "CT")].sort_values("PatientID")
patients = list(dict.fromkeys(ct.PatientID))[:N]
size = ct[ct.PatientID.isin(patients)].series_size_MB.sum()
print(f"Downloading {len(patients)} LIDC-IDRI patients (~{size:.0f} MB of CT) to {OUT}")
print(", ".join(patients))
OUT.mkdir(exist_ok=True)
client.download_from_selection(downloadDir=str(OUT), patientId=patients,
                               dirTemplate="%PatientID/%Modality_%SeriesInstanceUID")
print("Done. Each patient folder holds the CT series and the radiologists' annotation series (SEG/SR).")
