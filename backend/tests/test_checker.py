import unittest

from tests.conftest_env import DEMO
from relecto.checker import apply_suggestion, check_report, run_check


def ids(text, exam="ct_chest"):
    return [i.rule_id for i in check_report(text, exam, max_issues=50)]


class TestRules(unittest.TestCase):
    def test_demo_catches_all_planted_errors(self):
        got = ids(DEMO)
        for rule in ["LAT-001", "NEG-001", "MEAS-001", "CONC-001"]:
            self.assertIn(rule, got)

    def test_versions_stamped(self):
        v = run_check(DEMO).versions
        self.assertEqual(set(v), {"engine", "lexicon", "rule_pack"})
        self.assertTrue(v["rule_pack"].startswith("ct_chest@"))

    def test_laterality_fix_applies_cleanly(self):
        lat = next(i for i in check_report(DEMO) if i.rule_id == "LAT-001")
        fixed = apply_suggestion(DEMO, lat)
        self.assertIn("Nodule pulmonaire droit à surveiller", fixed)
        self.assertNotIn("LAT-001", ids(fixed))

    def test_feminine_side_correction(self):
        text = ("TECHNIQUE : Sans injection.\nRÉSULTATS :\nMasse du lobe inférieur droit de 4 cm.\n"
                "CONCLUSION :\nMasse pulmonaire gauche de 4 cm.")
        lat = next(i for i in check_report(text) if i.rule_id == "LAT-001")
        self.assertEqual(lat.suggestion.replacement, "droite")

    def test_never_proposes_a_measurement(self):
        meas = next(i for i in check_report(DEMO) if i.rule_id == "MEAS-001")
        self.assertIsNone(meas.suggestion)

    def test_side_specific_negation_is_not_a_conflict(self):
        text = ("TECHNIQUE : Avec injection.\nRÉSULTATS :\nÉpanchement pleural droit.\n"
                "Pas d'épanchement pleural gauche.\nCONCLUSION :\nÉpanchement pleural droit.")
        self.assertNotIn("NEG-001", ids(text))

    def test_enumerated_negation(self):
        text = ("INDICATION : Fièvre.\nTECHNIQUE : Sans injection.\nRÉSULTATS :\nPas d'épanchement pleural, "
                "de pneumothorax ni de condensation.\nCONCLUSION :\nNormal.")
        self.assertEqual(ids(text), [])

    def test_accents_and_case_do_not_matter(self):
        text = ("TECHNIQUE : SANS INJECTION.\nRESULTATS :\nNODULE DU LOBE SUPERIEUR DROIT DE 5 MM.\n"
                "CONCLUSION :\nNODULE PULMONAIRE GAUCHE.")
        self.assertIn("LAT-001", ids(text))

    def test_report_without_headers(self):
        self.assertIn("SEC-001", ids("Nodule de 5 mm du lobe supérieur droit."))

    def test_suggestion_refuses_stale_text(self):
        lat = next(i for i in check_report(DEMO) if i.rule_id == "LAT-001")
        with self.assertRaises(ValueError):
            apply_suggestion(DEMO.replace("gauche à surveiller", "xxxxxx à surveiller"), lat)

    def test_modality_vocabulary_ct(self):
        text = DEMO.replace("Emphysème centrolobulaire prédominant aux apex.", "Plage en hypersignal apicale.")
        self.assertIn("MOD-001", ids(text))

    def test_modality_vocabulary_ignores_mri_recommendation(self):
        text = DEMO.replace("Emphysème centrolobulaire.", "Complément par IRM avec séquence FLAIR proposé.")
        self.assertNotIn("MOD-001", ids(text))

    def test_disabled_rule_does_not_run(self):
        text = ("INDICATION : x.\nTECHNIQUE : T1, T2.\nRÉSULTATS :\nCondensation du lobe moyen gauche.\n"
                "CONCLUSION :\nCondensation du lobe moyen gauche.")
        self.assertIn("ANAT-001", ids(text, "ct_chest"))
        self.assertNotIn("ANAT-001", ids(text, "mri_brain"))  # disabled in the MRI pack


if __name__ == "__main__":
    unittest.main()
