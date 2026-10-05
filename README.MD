# pdf2epub

Rebuild a **text-based PDF book** as a clean, **reflowable EPUB**: real headings, joined paragraphs, sidebars as boxes, tables as tables, code listings with their indentation, figures as crisp images, and a working table of contents and cross-references.

Generic converters (Calibre, online tools) treat a PDF as a pile of positioned lines, so
books with sidebars, definition lists and running page headers come out with one paragraph
per line, headings glued to body text, and "78 | Chapter 6: …" in the middle of sentences.
This project reads the PDF's *geometry and fonts* and rebuilds the structure instead.

It was written for an *design heavy-PDF book* and read well on a 6-inch Kindle (8th generation) via Send to Kindle. The layout rules live in a **profile**, so other books need a new profile rather than a new program (see below).

> Only convert files you have the right to convert. This repository contains no book
> content, and `.gitignore` keeps PDFs and EPUBs out of commits.

## Install

Python 3.9 or newer.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # or: pip install -r requirements.txt
```

Without installing, run it from the repository root with `PYTHONPATH=src python -m pdf2epub ...`.

## Use

```bash
pdf2epub book.pdf -o book.epub
```

| Option | Meaning |
|---|---|
| `-o, --output` | output file (default: next to the PDF) |
| `-p, --profile` | layout profile, default `oreilly-fsa` (`--list-profiles` shows all) |
| `--pages 97-103` | convert only these PDF pages, to tune a profile quickly |
| `--cache-dir DIR` | cache the slow PDF extraction between runs |
| `--work-dir DIR` | keep the unpacked EPUB (XHTML, images) for inspection |
| `-q` | quiet |

From Python:

```python
from pdf2epub import convert
convert("book.pdf", "book.epub", profile="oreilly-fsa")
```

### Getting it onto a Kindle

* **Send to Kindle** (app, web page or email) accepts EPUB directly.
* Over USB, open the EPUB in Calibre and convert it to **AZW3**; the clean input makes that conversion trivial.

### Check the result

```bash
python -m pdf2epub.validate book.epub
```

This verifies the zip layout, that all XML parses, that the manifest/spine/nav/links/images all resolve, and that no "on page N" references are left. It is a sanity check, not a substitute for [EpubCheck](https://www.w3.org/publishing/epubcheck/).

## How it works

```
PDF
 │  document.py   pdfplumber: every character with font, size, position; pypdf: links, destinations
 ▼
page model ──► layout.py     characters → text lines → page items (lines, figures, equations)
 │             blocks.py     lines → blocks (headings, paragraphs, list items, code, tables…)
 │             render.py     blocks → inline HTML, tables, code; figures cropped with pdfium
 │             assemble.py   blocks → chapter XHTML files, table-of-contents entries
 ▼             epub.py       OPF, nav, NCX, CSS, cover → .epub
EPUB
```

Details that matter for quality:

* **Classification by font and size.** `Profile.kind_of` maps a line to a kind (`h2`, `para`, `li`, `code`, `tablecell`…). Paragraph continuation uses line spacing and indentation, which is what fixes the "one line per paragraph" problem.
* **Page furniture is dropped** (running headers/footers), and **split words are rejoined**:
  a line-end hyphen is removed when the joined word appears elsewhere in the book, and kept for genuine compounds such as "trade-off".
* **Sidebars, definition lists, tip callouts** become `div`/`p` structures with CSS.
* **Tables** are rebuilt by clustering cell start positions into columns.
* **Figures and equations** are cropped from a 2.4× render of the page and stored as 64-colour PNGs; equations are trimmed to the formula.
* **Cross-references** keep their links; the print-only "on page 125" text is removed.
* **Omitted:** the back-of-book index (it is only page numbers) and blank advert pages.

## Adding a profile for another book

1. Inspect the fonts and sizes (`pdffonts book.pdf`, or print `Document(...).pages[n]["chars"]`).
2. Copy `src/pdf2epub/profiles/oreilly_fsa.py` and adjust:
   * `kind_of` — the font/size → kind rules;
   * `is_footer`, `is_icon`, `is_equation_caption`, `is_body_line`;
   * `skip_pages`, `raw_pages` (hand-written title/copyright XHTML), `file_starts` (pages where a new chapter file begins), `first_page`/`last_page`, `pitch`, metadata.
3. Register the class in `profiles/__init__.py`.
4. Iterate on a few pages: `pdf2epub book.pdf --pages 40-48 --cache-dir .cache --work-dir work` and open `work/OEBPS/*.xhtml`.

The kinds the pipeline understands are listed at the top of `profiles/base.py`.

## Tests

```bash
python -m unittest discover -s tests -v          # or: pytest
PDF2EPUB_TEST_PDF=/path/to/book.pdf python -m unittest discover -s tests   # adds a real-PDF test
```

The unit tests need no PDF. The integration test converts chapter 6 of the book and runs the validator.

## Limitations

* Needs a PDF with a real text layer. Scanned PDFs must be OCR'd first (for example with OCRmyPDF).
* Only one profile ships. The classification thresholds are specific to that book's fonts;
  expect to write a profile for other layouts. Other O'Reilly books of the same generation should need only small changes.
* Two-column layouts, footnotes and complex page furniture are not handled.
* Figures are raster images, so very wide diagrams may be small on e-ink screens.
* Output has been checked on a Kindle (8th generation) through Send to Kindle and with the structural validator above, not with EpubCheck or on other readers.