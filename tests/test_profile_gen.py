"""Unit tests for the profile generator. They use plain segment dicts, so no PDF,
reportlab or poppler is needed. One end-to-end test runs when reportlab is installed.

Run from the repository root:  python -m unittest discover -s tests -v
"""
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf2epub import profile_gen as pg          # noqa: E402
from pdf2epub.profile import DEFAULTS           # noqa: E402

import helpers                                   # noqa: E402

W, H = 612, 792


def seg(text, size=11, kind="R", x0=72, x1=None, y0=400):
    return dict(text=text, size=size, kind=kind, x0=x0, x1=x1 if x1 is not None else x0 + 6 * len(text),
                y0=y0, y1=y0 + size)


def page(n, segs):
    return dict(n=n, w=W, h=H, segs=segs, gfx=[])


def body_page(n, extra=()):
    lines = [seg("Body text line number %d of the page." % i, y0=700 - 15 * i) for i in range(10)]
    return page(n, lines + list(extra))


class BodySize(unittest.TestCase):
    def test_most_characters_win(self):
        pages = [page(1, [seg("x" * 200, 11), seg("Big title", 24), seg("note", 8)])]
        self.assertEqual(pg.body_size(pages), 11)

    def test_empty_book(self):
        self.assertIsNone(pg.body_size([]))


class Footers(unittest.TestCase):
    def test_repeated_footer_and_page_numbers(self):
        pages = [body_page(n, [seg("The Harbour Almanac", 8, y0=30), seg(str(n), 8, x0=300, y0=30)])
                 for n in range(1, 11)]
        fy, res = pg.footers(pages)
        self.assertIn("^\\d{1,4}$", res)
        self.assertIn("^The\\ Harbour\\ Almanac$", res)
        self.assertEqual(fy, 40)

    def test_numbered_footer_becomes_one_pattern(self):
        pages = [body_page(n, [seg("Chapter %d · Tides" % n, 8, y0=20)]) for n in range(1, 6)]
        _, res = pg.footers(pages)
        self.assertEqual(res, ["^Chapter\\ \\d{1,4}\\ ·\\ Tides$"])

    def test_text_low_on_one_page_only_is_not_a_footer(self):
        pages = [body_page(n) for n in range(1, 10)] + [body_page(10, [seg("A caption", 9, y0=30)])]
        self.assertEqual(pg.footers(pages), (0, []))


class Dividers(unittest.TestCase):
    def test_two_sparse_pages_with_huge_text(self):
        pages = [body_page(1), page(2, [seg("1", 90), seg("Harbour", 30)]), body_page(3),
                 page(4, [seg("2", 96), seg("Open water", 30)])]
        self.assertEqual(pg.dividers(pages, 11), (90, [2, 4]))

    def test_single_big_page_is_the_title_page(self):
        pages = [body_page(1), page(2, [seg("Title", 60)]), body_page(3)]
        self.assertEqual(pg.dividers(pages, 11), (None, []))


class Columns(unittest.TestCase):
    def test_two_column_page(self):
        left = [seg("left column text", x0=72, x1=290, y0=700 - 15 * i) for i in range(8)]
        right = [seg("right column text", x0=320, x1=540, y0=700 - 15 * i) for i in range(8)]
        self.assertEqual(pg.column_pages([page(7, left + right), body_page(8)]), [7])

    def test_full_width_page_is_not_columns(self):
        full = [seg("full width", x0=72, x1=540, y0=700 - 15 * i) for i in range(16)]
        self.assertEqual(pg.column_pages([page(1, full)]), [])


class Misc(unittest.TestCase):
    def test_code_size_ignores_mono_bigger_than_body(self):
        pages = [body_page(1, [seg("x = 1", 9, "M"), seg("BIG MONO", 20, "M")])]
        self.assertEqual(pg.code_max_size(pages, 11), 9)

    def test_no_code(self):
        self.assertIsNone(pg.code_max_size([body_page(1)], 11))

    def test_contents_page(self):
        pages = [page(1, [seg("Title", 40)]), page(2, [seg("Contents", 24), seg("Chapter 1 ..... 3")]), body_page(3)]
        self.assertEqual(pg.contents_pages(pages), [2])

    def test_heading_sizes_biggest_first(self):
        pages = [body_page(1, [seg("H1", 24), seg("H2", 16), seg("H2b", 16)])]
        self.assertEqual(pg.heading_sizes(pages, 11), [(24, 1), (16, 2)])


class Render(unittest.TestCase):
    def book(self):
        pages = [body_page(n, [seg("Almanac's Guide", 8, y0=30), seg(str(n), 8, x0=300, y0=30), seg("x = 1", 9, "M")])
                 for n in range(1, 9)]
        pages[2] = page(3, [seg("1", 90)])
        pages[5] = page(6, [seg("2", 90)])
        return pages

    def test_output_is_valid_toml_with_known_keys(self):
        text = pg.render(pg.analyze(self.book()), "book.pdf")
        data = tomllib.loads(text)
        self.assertLessEqual(set(data), set(DEFAULTS))
        self.assertEqual(data["divider_size"], 90)
        self.assertEqual(data["code_max_size"], 9)
        self.assertIn("^Almanac's\\ Guide$", data["footer_res"])     # quote forces a basic string

    def test_footer_regexes_still_match_after_round_trip(self):
        import re
        data = tomllib.loads(pg.render(pg.analyze(self.book()), "book.pdf"))
        self.assertTrue(any(re.match(r, "42") for r in data["footer_res"]))
        self.assertTrue(any(re.match(r, "Almanac's Guide") for r in data["footer_res"]))

    def test_empty_analysis_still_renders(self):
        data = tomllib.loads(pg.render(pg.analyze([body_page(1)]), "book.pdf"))
        self.assertEqual(data["skip_pages"], [])
        self.assertNotIn("footer_res", data)


class Cli(unittest.TestCase):
    def test_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf, out = Path(tmp) / "a.pdf", Path(tmp) / "a.toml"
            pdf.write_bytes(b"%PDF-1.4")
            out.write_text("keep me")
            with self.assertRaises(SystemExit):
                pg.main([str(pdf), "-o", str(out)])
            self.assertEqual(out.read_text(), "keep me")

    def test_missing_pdf(self):
        with self.assertRaises(SystemExit):
            pg.main(["/no/such/file.pdf"])

    @unittest.skipUnless(helpers.HAVE_REPORTLAB, "reportlab is not installed (pip install -e .[dev])")
    def test_end_to_end_on_synthetic_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf, out = Path(tmp) / "mini.pdf", Path(tmp) / "mini.toml"
            helpers.build_mini_pdf(pdf)
            pg.main([str(pdf), "-o", str(out)])
            data = tomllib.loads(out.read_text(encoding="utf-8"))
            self.assertLessEqual(set(data), set(DEFAULTS))


if __name__ == "__main__":
    unittest.main()
