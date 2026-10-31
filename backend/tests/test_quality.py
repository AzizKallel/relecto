import unittest

from relecto.quality import evaluate


class TestQuality(unittest.TestCase):
    def test_golden_set_has_no_regressions(self):
        n, scores, details = evaluate()
        self.assertGreaterEqual(n, 20)
        self.assertEqual(details, [], f"misses or false alarms: {details}")


if __name__ == "__main__":
    unittest.main()
