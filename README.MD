# pdf2epub

Turns a design-heavy PDF (cover, divider pages, running footers, code blocks, boxes) into a reflowable, Kindle-friendly EPUB 3 file. The book-specific rules, such as the footer pattern, which pages are dividers and which headings are sub-sections, live in a small TOML profile, so the converter itself contains nothing book-specific. When it finishes, it checks the EPUB (structure, links, navigation, alt text) and prints PASS or FAIL for each check.

No real books are included. The only tracked files in `input/` and `output/` are the synthetic demo files `sample.pdf` and `sample.epub`, whose text is invented.

## Requirements

- **Python 3.11** or newer
- [pdfminer.six](https://pypi.org/project/pdfminer.six/) (installed by pip)
- **poppler's `pdftoppm`**: a system tool and not a pip package, used for the cover and figure images:
  - macOS: `brew install poppler`
  - Debian/Ubuntu: `sudo apt install poppler-utils`
  - Windows: install the poppler binaries and put their `bin` folder on the `PATH`

## Install

```bash
pip install -e .
```

### Development
Add `[dev]` (`pip install -e ".[dev]"`) to also install reportlab, which the tests and the demo files use. Without installing, `pip install -r requirements.txt` gets the one runtime dependency and `python -m pdf2epub` works with `PYTHONPATH=src`.

```bash
pip install -e ".[dev]"
pytest
```

## Workflow

1. Put the PDF in `input/`.
2. Run `pdf2epub input/<book>.pdf -p profiles/<profile>.toml -o output/` (or `python -m pdf2epub ...`).
3. Find the EPUB in `output/`. The file name comes from `out_name` in the profile, or from the PDF name.

`input/` and `output/` are only a convention for keeping files tidy. The command accepts any paths, and `-o` still defaults to `out`.

## Try it

```bash
pdf2epub input/sample.pdf -p profiles/example.toml -o output/
```

This reads the synthetic demo PDF with the example profile and writes `output/Harbour-Almanac.epub` (the `out_name` in the example profile), then prints the checks. All of them should say PASS. The committed `output/sample.epub` is the same conversion saved under another name. When the converter's output changes, regenerate it:

```bash
pdf2epub input/sample.pdf -p profiles/example.toml -o output/
mv output/Harbour-Almanac.epub output/sample.epub
```

`input/sample.pdf` can be rebuilt with `python tests/helpers.py input/sample.pdf` (needs reportlab).

## Writing a profile

Copy `profiles/example.toml`, rename it and edit it. Every key is optional and the comments in that file explain each one. Keys that are not in the list are rejected with an error. A profile for a real book that you do not want to publish can be named `profiles/<name>.local.toml`: git ignores `profiles/*.local.toml`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests use only `unittest` and temporary directories; they never write into `input/` or `output/`. They are skipped, with a message, when reportlab or `pdftoppm` is missing.

## Folders and git

- `input/` holds the PDFs to convert and `output/` receives the EPUBs.
- Everything in those two folders is ignored by git except `input/sample.pdf` and `output/sample.epub`. Never commit anything else there. Check `git status` before each commit.
- `profiles/*.local.toml` is for private profiles and is ignored by git.

## Known limits

- The conversion depends on the layout of each PDF. Rules for headings, footers, boxes and code are heuristics driven by the profile, so a new book usually needs its own profile and some trial and error.
- Scanned PDFs without a text layer are not supported (there is no OCR).
- Figures are exported as PNG images from the page; text in diagrams is only kept in the alt text.
- The optional epubcheck and PyYAML checks run only if those tools are installed.
- The demo files exercise only part of the converter: there are no figures, two-column pages or tables.

## Legal

Use this tool only on documents you have the right to use. It comes with no warranty; see `LICENSE` (MIT).
