# Data sources

| Dataset | Use | Licence | Citation |
| --- | --- | --- | --- |
| LIDC-IDRI (via NCI Imaging Data Commons) | Development and testing of the chest CT pipeline; nodule annotations by 4 thoracic radiologists | CC BY 3.0 (commercial use allowed with attribution) | Armato SG III et al. (2015). Data From LIDC-IDRI. The Cancer Imaging Archive. https://doi.org/10.7937/K9/TCIA.2015.LO9QL9SX — Armato et al., Medical Physics 38(2), 2011 — Clark K et al., J Digit Imaging 26(6), 2013 |

Rules:
- Test scans live in `../dicom-test/`, outside the repository. Never commit images.
- No dataset containing burned-in names or identifiers may be used.
- Tunisian data only under a signed data agreement, de-identified, stored in `data/private/` or on the centre's server.
