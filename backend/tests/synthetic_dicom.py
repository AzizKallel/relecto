"""Write a small synthetic chest CT as a DICOM series (no patient data, known answer)."""

import io
import zipfile
from pathlib import Path

import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

CT_IMAGE = "1.2.840.10008.5.1.4.1.1.2"


def phantom(n_slices=60, size=128, px=2.5, nodule_side="L", radius_mm=7.0):
    """(slices, rows, cols) HU volume; returns volume and nodule mask. Column index runs to patient left."""
    k, i, j = np.mgrid[0:n_slices, 0:size, 0:size].astype(np.float32)
    x, y = (j - size / 2) * px, (i - size / 2) * px
    hu = np.full(k.shape, -1000, np.int16)
    body = (x / 150) ** 2 + (y / 110) ** 2 <= 1
    hu[body] = 40
    zl = (k > 4) & (k < n_slices - 4)
    for cx in (-70, 70):
        hu[(((x - cx) / 55) ** 2 + (y / 80) ** 2 <= 1) & zl] = -850
    cx = 70 if nodule_side == "L" else -70
    z = (k - n_slices / 2) * 2.5
    nod = (x - cx) ** 2 + y ** 2 + z ** 2 <= radius_mm ** 2
    hu[nod] = 10
    return hu, nod


def write_series(folder, hu, patient="TEST^PATIENT", study_uid=None, px=2.5, thickness=2.5, accession="ACC1"):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    study_uid = study_uid or generate_uid()
    series_uid = generate_uid()
    n, rows, cols = hu.shape
    for k in range(n):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = CT_IMAGE
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID, ds.SOPInstanceUID = CT_IMAGE, meta.MediaStorageSOPInstanceUID
        ds.Modality, ds.StudyInstanceUID, ds.SeriesInstanceUID = "CT", study_uid, series_uid
        ds.PatientName, ds.PatientID, ds.PatientSex, ds.PatientBirthDate = patient, "PID-0001", "F", "19700101"
        ds.AccessionNumber, ds.StudyDate, ds.StudyDescription = accession, "20261101", "TDM THORAX"
        ds.BodyPartExamined = "CHEST"
        ds.InstanceNumber = k + 1
        ds.ImagePositionPatient = [-(cols / 2) * px, -(rows / 2) * px, (k - n / 2) * thickness]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.PixelSpacing = [px, px]
        ds.SliceThickness = thickness
        ds.Rows, ds.Columns = rows, cols
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
        ds.RescaleIntercept, ds.RescaleSlope = 0, 1
        ds.PixelData = hu[k].astype("<i2").tobytes()
        ds.save_as(folder / f"IM{k:04d}.dcm", enforce_file_format=True)
    return study_uid


def zip_folder(folder) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in sorted(Path(folder).rglob("*.dcm")):
            z.write(p, p.relative_to(folder))
    return buf.getvalue()
