"""Smoke test: convert a 3-page synthetic PDF and inspect the EPUB.

Run from the repository root:  python -m unittest discover -s tests -v
"""
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import helpers

PROFILE = '''title = "Smoke Test Book"
author = "Ana Fernsby"
out_name = "smoke.epub"
footer_y = 40
footer_res = ['^Smoke Test Book$', '^\\d{1,4}$']
heading_fonts = ["Headline"]
'''

XH = "{http://www.w3.org/1999/xhtml}"


@unittest.skipUnless(helpers.HAVE_REPORTLAB, "reportlab is not installed (pip install -e .[dev])")
@unittest.skipUnless(helpers.HAVE_PDFTOPPM, "pdftoppm (poppler) is not on the PATH")
class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        pdf, profile, out = tmp / "mini.pdf", tmp / "mini.toml", tmp / "out"
        helpers.build_mini_pdf(pdf)
        profile.write_text(PROFILE, encoding="utf-8")
        cls.result = helpers.run_cli([pdf, "-p", profile, "-o", out])
        cls.epub = out / "smoke.epub"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_epub_was_written(self):
        self.assertTrue(self.epub.is_file(), self.result.stdout + self.result.stderr)

    def test_mimetype_is_first_and_stored(self):
        with zipfile.ZipFile(self.epub) as z:
            first = z.infolist()[0]
            self.assertEqual(first.filename, "mimetype")
            self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(z.read("mimetype"), b"application/epub+zip")

    def test_xhtml_is_well_formed(self):
        with zipfile.ZipFile(self.epub) as z:
            names = [n for n in z.namelist() if n.endswith(".xhtml")]
            self.assertTrue(names)
            for n in names:
                ET.fromstring(z.read(n))               # raises ParseError if not well-formed

    def test_navigation_is_present(self):
        with zipfile.ZipFile(self.epub) as z:
            nav = ET.fromstring(z.read("OEBPS/nav.xhtml"))
        types = {n.get("{http://www.idpf.org/2007/ops}type") for n in nav.iter(XH + "nav")}
        self.assertLessEqual({"toc", "landmarks"}, types)
        toc = next(n for n in nav.iter(XH + "nav") if n.get("{http://www.idpf.org/2007/ops}type") == "toc")
        entries = ["".join(a.itertext()) for a in toc.iter(XH + "a")]
        self.assertIn("Contents", entries)
        self.assertIn("First chapter", entries)


if __name__ == "__main__":
    unittest.main()
