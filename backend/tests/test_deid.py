import unittest

from relecto.checker import check_report
from relecto.deid import deidentify, reidentify

HEADER = """Patient : BEN SALAH Mohamed
Né le 12/03/1968 — IPP : 20231145
Adressé par Dr Karim Trabelsi, tél. +216 98 123 456
Adresse : 12 rue de Marseille, Tunis
Examen du 5 mars 2026

INDICATION : Toux chronique chez Mme Leila Gharbi. Suivi d'un nodule.
TECHNIQUE : Sans injection de produit de contraste.
RÉSULTATS :
Nodule du lobe supérieur droit de 8 mm, stable par rapport au 12/09/2025.
CONCLUSION :
Nodule pulmonaire gauche de 8 mm.
Contact : imagerie.centre@example.tn
"""


class TestDeid(unittest.TestCase):
    def setUp(self):
        self.r = deidentify(HEADER)

    def test_identifiers_removed(self):
        for secret in ["BEN SALAH", "Mohamed", "12/03/1968", "20231145", "Karim Trabelsi",
                       "98 123 456", "rue de Marseille", "5 mars 2026", "Leila Gharbi",
                       "12/09/2025", "imagerie.centre@example.tn"]:
            self.assertNotIn(secret, self.r.text, secret)

    def test_clinical_content_kept(self):
        for keep in ["Nodule du lobe supérieur droit de 8 mm", "Nodule pulmonaire gauche de 8 mm",
                     "Sans injection de produit de contraste", "RÉSULTATS"]:
            self.assertIn(keep, self.r.text, keep)

    def test_measurements_not_masked(self):
        r = deidentify("Nodule de 12 mm. Masse de 3,5 cm. Coupes de 1,25 mm. IRM 1,5 T.")
        self.assertEqual(r.replacements, [])

    def test_checker_still_works_after_deid(self):
        self.assertIn("LAT-001", [i.rule_id for i in check_report(self.r.text, "ct_chest")])

    def test_reidentify_roundtrip(self):
        self.assertEqual(reidentify(self.r.text, self.r), HEADER)

    def test_inline_patient_name(self):
        r = deidentify("INDICATION : Toux. Patient : BEN SALAH Mohamed, né le 12/03/1968.")
        self.assertNotIn("BEN SALAH", r.text)
        self.assertNotIn("Mohamed", r.text)
        self.assertIn("INDICATION : Toux.", r.text)

    def test_lowercase_words_after_name_kept(self):
        r = deidentify("Patiente : Amira Ben Ali, adressée pour bilan.")
        self.assertIn("adressée pour bilan", r.text)

    def test_counts(self):
        c = self.r.counts()
        self.assertGreaterEqual(c.get("[NOM]", 0), 2)
        self.assertEqual(c.get("[MEDECIN]"), 1)


if __name__ == "__main__":
    unittest.main()
