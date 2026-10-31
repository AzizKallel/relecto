import tempfile
import unittest
from pathlib import Path

from relecto.checker.content import ContentError, get_content, load_lexicon, load_pack


class TestContent(unittest.TestCase):
    def test_real_content_loads(self):
        c = get_content()
        self.assertIn("ct_chest", c.packs)
        self.assertGreater(len(c.lexicon.findings), 10)

    def _write(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / "x.yaml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_bad_severity_is_reported_with_location(self):
        p = self._write("exam: x\nversion: '1'\nrules:\n  LAT-001: {enabled: true, severity: urgent}\n")
        with self.assertRaisesRegex(ContentError, "x.yaml rules.LAT-001"):
            load_pack(p)

    def test_bad_pattern_is_reported(self):
        p = self._write("exam: x\nversion: '1'\nrules:\n  MOD-001: {enabled: true, forbidden: ['(unclosed']}\n")
        with self.assertRaisesRegex(ContentError, "invalid pattern"):
            load_pack(p)

    def test_lexicon_requires_gender(self):
        p = self._write("version: '1'\nfindings:\n  - {key: a, forms: [a]}\nsides: {bilateral: [b], right: [r], left: [l]}\n"
                        "negation: {before: [x], after: [y]}\n")
        with self.assertRaisesRegex(ContentError, "gender"):
            load_lexicon(p)


if __name__ == "__main__":
    unittest.main()
