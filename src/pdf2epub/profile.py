"""PROFILE - book-specific rules come from a TOML file (-p/--profile). Nothing in
the converter is book-specific; DEFAULTS are neutral. See profiles/example.toml."""
import os
import sys

DEFAULTS = dict(
    title=None,                   # None: PDF metadata title, else the file name
    subtitle="",
    author=None,                  # None: PDF metadata author, else "Unknown"
    lang="en",
    out_name=None,                # None: <pdf file name>.epub
    cover_page=1,                 # rendered as the cover image
    skip_pages=set(),             # pages left out (e.g. the PDF's own contents page)
    footer_y=0,                   # anything whose baseline is below this is a running footer
    footer_res=[],                # regexes for running-footer text
    divider_size=float("inf"),    # first text on a divider page is this big (the number)
    stage_title="{title}",        # chapter title built from a divider page: {n}, {title}
    eyebrow_re="",                # regex for the small label above a chapter title
    opener_as_section=set(),      # eyebrow values whose opener stays inside the previous chapter
    h3_labels=[],                 # lower-case prefixes of upper-case labels that become h3
    h19_as_h3=set(),              # big headings that are really sub-sections
    aside_labels=(),              # upper-case prefixes that open a box running to page end
    acronyms=[],                  # kept upper-case when a label is re-cased
    column_pages=set(),           # pages laid out as two text columns (read left, then right)
    figure_alt_extra={},          # figure number -> extra alt text
    diagram_figures=set(),        # figure numbers exported as PNG
    dialog_figures=set(),         # figure numbers rebuilt as text (speaker, time, message)
    heading_fonts=[],             # font-name fragments that mark the display/heading font
    code_max_size=10.0,           # monospace text at or below this size is a code block
    mono_split_em=5.0,            # gap (in em) that splits two monospace columns on one line
    space_em=0.2,                 # gap (in em) that means "a space is missing"
    expected_nav=[],              # [(chapter, [sub-entries])]; empty: only check the TOC exists
    front_nav=["Cover", "Title page", "Contents"],
)
P = dict(DEFAULTS)

_SETS = ("skip_pages", "column_pages", "diagram_figures", "dialog_figures", "opener_as_section", "h19_as_h3")


def _meta(pdf_path):
    """Title and Author from the PDF information dictionary, when present."""
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdftypes import resolve1
    out = {}
    try:
        with open(pdf_path, "rb") as fh:
            info = resolve1(PDFDocument(PDFParser(fh)).info[0]) or {}
            for k in ("Title", "Author"):
                v = resolve1(info.get(k))
                if isinstance(v, bytes):
                    v = v[2:].decode("utf-16-be", "ignore") if v[:2] == b"\xfe\xff" else v.decode("latin-1")
                if v and v.strip():
                    out[k] = v.strip()
    except Exception:
        pass
    return out


def load_profile(path, pdf_path):
    """Fill P from DEFAULTS, then the TOML profile, then the PDF metadata / file name."""
    P.update(DEFAULTS)
    if path:
        import tomllib
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        bad = sorted(set(raw) - set(DEFAULTS))
        if bad:
            sys.exit("unknown profile keys: " + ", ".join(bad))
        for k, v in raw.items():
            if k in _SETS:
                v = set(v)
            elif k == "aside_labels":
                v = tuple(v)
            elif k == "figure_alt_extra":
                v = {int(n): t for n, t in v.items()}
            elif k == "expected_nav":
                v = [(t, list(sub)) for t, sub in v]
            P[k] = v
    meta, stem = _meta(pdf_path), os.path.splitext(os.path.basename(pdf_path))[0]
    P["title"] = P["title"] or meta.get("Title") or stem
    P["author"] = P["author"] or meta.get("Author") or "Unknown"
    P["out_name"] = P["out_name"] or stem + ".epub"
