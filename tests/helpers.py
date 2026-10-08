"""Helpers for the tests and for the demo files.

Builds small synthetic PDFs with reportlab. Every word in them is invented;
no real book is used anywhere. reportlab is imported lazily so that this module
can be imported (and the tests skipped) when it is not installed.

Regenerate the demo input:   python tests/helpers.py input/sample.pdf
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

HAVE_REPORTLAB = importlib.util.find_spec("reportlab") is not None
HAVE_PDFTOPPM = shutil.which("pdftoppm") is not None

# Font names: pdf2epub tells font families apart by name fragments ("Mono",
# "Sans", and the profile's heading_fonts), so the synthetic PDFs embed reportlab's
# bundled Vera font under those names.
SERIF, SANS, HEAD, MONO = "DemoSerif", "DemoSans", "DemoHeadline", "DemoMono"
LEFT, RIGHT = 72, 540


def _fonts():
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    fonts = Path(reportlab.__file__).parent / "fonts"
    for name, ttf in ((SERIF, "Vera.ttf"), (SANS, "Vera.ttf"), (HEAD, "VeraBd.ttf"), (MONO, "Vera.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            font = TTFont(name, str(fonts / ttf))
            font.face.name = name.encode("ascii")      # the BaseFont name written to the PDF
            pdfmetrics.registerFont(font)


def _canvas(path, title):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen.canvas import Canvas
    _fonts()
    c = Canvas(str(path), pagesize=letter, invariant=1)     # invariant: reproducible bytes
    c.setTitle(title)
    c.setAuthor("Ana Fernsby")
    return c


def _text(c, x, y, s, font=SERIF, size=11):
    c.setFont(font, size)
    c.drawString(x, y, s)


def _lines(c, x, y, lines, size=11, leading=15, links=None):
    """Draw left-aligned lines; `links` maps a word to a URL (link annotation over the word)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    for s in lines:
        _text(c, x, y, s, SERIF, size)
        for word, url in (links or {}).items():
            if word in s:
                a = x + stringWidth(s[:s.index(word)], SERIF, size)
                c.linkURL(url, (a, y - 3, a + stringWidth(word, SERIF, size), y + size), relative=0)
        y -= leading
    return y


def _para(c, y, lines, **kw):
    """One paragraph at the left margin; returns the y for the next block."""
    return _lines(c, LEFT, y, lines, **kw) - 12


def _items(c, y, items, x=LEFT, size=12):
    for s in items:
        _text(c, x, y, s, SERIF, size)
        y -= 26                                    # far enough apart to stay separate paragraphs
    return y - 4


def _code(c, y, lines, size=8.5):
    """Monospace block: every glyph sits on a 0.6 em grid, as in a real monospace font."""
    cw = 0.6 * size
    for s in lines:
        indent = len(s) - len(s.lstrip(" "))
        for k, ch in enumerate(s.lstrip(" ")):
            if ch != " ":
                _text(c, LEFT + (indent + k) * cw, y, ch, MONO, size)
        y -= 14
    return y - 10


def _footer(c, label, n):
    _text(c, LEFT, 55, label, SANS, 8)
    c.setFont(SANS, 8)
    c.drawRightString(RIGHT, 55, str(n))


def _cover(c, title, sub):
    c.setFillColorRGB(0.1, 0.25, 0.4)
    c.rect(0, 0, 612, 792, stroke=0, fill=1)
    c.setStrokeColorRGB(0.8, 0.9, 1.0)
    c.setLineWidth(3)
    for k in range(4):
        c.line(60, 200 + 24 * k, 552, 200 + 24 * k)
    c.setFillColorRGB(1, 1, 1)
    _text(c, LEFT, 560, title, HEAD, 40)
    _text(c, LEFT, 520, sub, SERIF, 14)
    c.showPage()


def _page(c):
    c.showPage()
    c.setFillColorRGB(0, 0, 0)


def build_demo_pdf(path):
    """Synthetic PDF shaped for profiles/example.toml: cover, a skipped contents page,
    divider pages, headings, a code block, a TIP box, a repeating footer and one link."""
    c = _canvas(path, "The Harbour Almanac")
    _cover(c, "The Harbour Almanac", "Tides, moorings and weather for small boats.")
    c.setFillColorRGB(0, 0, 0)

    # 2 - the PDF's own contents page (skip_pages)
    _text(c, LEFT, 700, "Contents", HEAD, 30)
    _lines(c, LEFT, 650, ["Welcome aboard", "Quick reference", "Part 1: Harbour", "Part 2: Open water",
                          "Appendix"], 12, 22)
    _footer(c, "Harbour Almanac", 2)
    _page(c)

    # 3 - first chapter: lead line, two sections, a link, a bulleted list
    _text(c, LEFT, 700, "Welcome aboard", HEAD, 30)
    _text(c, LEFT, 665, "A small book for people who stay near the shore.", HEAD, 16)
    _text(c, LEFT, 615, "How to use this book", HEAD, 23)
    y = _para(c, 585, ["Each part of the almanac covers one kind of trip. Read the part that matches",
                       "your plan, then keep the quick reference in your pocket. Extra notes live in",
                       "the Harbour Almanac online notes."],
              links={"online notes": "https://example.org/harbour-almanac/notes"})
    _text(c, LEFT, y - 12, "Reading the tide table", HEAD, 23)
    y = _para(c, y - 42, ["The tide table lists the high and low water for the day. Three habits help:"])
    _items(c, y, ["Note the time of the next high water.", "Add an hour in summer.",
                  "Write the result on the chart."], x=90)
    _footer(c, "Harbour Almanac", 3)
    _page(c)

    # 4 - quick reference: label, numbered list, code block, TIP box (runs to page end)
    _text(c, LEFT, 700, "Quick reference", HEAD, 30)
    y = _para(c, 665, ["One page of habits for the start of every trip."])
    _text(c, LEFT, y, "BEFORE YOU SAIL", SANS, 9)
    y = _items(c, y - 26, ["1. Check the forecast.", "2. Check the tide.", "3. Tell someone your plan."])
    _text(c, LEFT, y, "MOORING LOG", SANS, 9)
    y = _code(c, y - 22, ['{', '  "boat": "Marigold",', '  "berth": "B12",', '  "tide_ft": 3.4', '}'])
    _text(c, LEFT, y - 6, "TIP", SANS, 9)
    _lines(c, LEFT, y - 28, ["Keep one spare line coiled near the bow."])
    _footer(c, "Quick reference", 4)
    _page(c)

    # 5 - divider page for part 1: the number is the biggest text
    _text(c, LEFT, 520, "1", HEAD, 120)
    _text(c, LEFT, 430, "Harbour", HEAD, 54)
    _text(c, LEFT, 395, "Where the boat sleeps.", SERIF, 14)
    _footer(c, "Part 1 · Harbour", 5)
    _page(c)

    # 6 - part 1 content, with a small label above the heading
    _text(c, LEFT, 720, "PART 1 · HARBOUR", SANS, 9)
    _text(c, LEFT, 690, "Choosing a mooring", HEAD, 23)
    _para(c, 660, ["A good mooring is sheltered from the wind that blows most often. Look at the",
                   "bottom, the swing room and the distance to the landing steps before you decide."])
    _footer(c, "Part 1 · Harbour", 6)
    _page(c)

    # 7 - divider page for part 2
    _text(c, LEFT, 520, "2", HEAD, 120)
    _text(c, LEFT, 430, "Open water", HEAD, 54)
    _text(c, LEFT, 395, "Further out, with a plan.", SERIF, 14)
    _footer(c, "Harbour Almanac", 7)
    _page(c)

    # 8 - part 2 content: two sections and a sub-section (h19_as_h3)
    _text(c, LEFT, 700, "Weather windows", HEAD, 23)
    y = _para(c, 670, ["A window is a stretch of hours when wind and swell stay below your limit."])
    _text(c, LEFT, y - 8, "Reading the sky", HEAD, 19)
    y = _para(c, y - 36, ["High thin cloud often comes before a change. Low dark cloud means rain soon."])
    _text(c, LEFT, y - 8, "Night passages", HEAD, 23)
    _para(c, y - 38, ["Keep the route simple at night. Choose marks that have a light you can name."])
    _footer(c, "Harbour Almanac", 8)
    _page(c)

    # 9 - appendix
    _text(c, LEFT, 700, "Appendix", HEAD, 30)
    _text(c, LEFT, 650, "Glossary", HEAD, 23)
    y = _para(c, 620, ["Berth: the place where a boat is tied up."])
    _para(c, y, ["Swing room: the circle a moored boat can turn in."])
    _footer(c, "Harbour Almanac", 9)
    c.showPage()
    c.save()


def build_mini_pdf(path):
    """Three pages: cover, a chapter opener with text, a page with a section heading."""
    c = _canvas(path, "Smoke Test Book")
    _cover(c, "Smoke Test Book", "A tiny synthetic book.")
    c.setFillColorRGB(0, 0, 0)
    _text(c, LEFT, 700, "First chapter", HEAD, 30)
    _para(c, 660, ["This chapter only exists to test the converter. The text is invented."])
    _footer(c, "Smoke Test Book", 2)
    _page(c)
    _text(c, LEFT, 700, "A section", HEAD, 23)
    _para(c, 670, ["Another short paragraph so that the second page has some body text."])
    _footer(c, "Smoke Test Book", 3)
    c.showPage()
    c.save()


def run_cli(args, cwd=None):
    """Run `python -m pdf2epub` on the source tree (works without installing the package)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "pdf2epub", *map(str, args)], cwd=cwd, env=env,
                          capture_output=True, text=True)


if __name__ == "__main__":
    if not HAVE_REPORTLAB:
        sys.exit("reportlab is required: pip install -e .[dev]")
    build_demo_pdf(sys.argv[1] if len(sys.argv) > 1 else ROOT / "input" / "sample.pdf")
