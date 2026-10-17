"""PROFILE GENERATOR - read a PDF and draft a TOML profile for it.

The draft is a starting point, not a finished profile: it fills in the keys that
can be measured from text statistics (body size, running footers, divider pages,
two-column pages, code size, contents page) and leaves the judgement calls
(eyebrows, h3 labels, figures, expected_nav) as commented hints.

Usage:  pdf2epub-profile book.pdf                 -> profiles/<book>.local.toml
        pdf2epub-profile book.pdf -o draft.toml --force

The analysis works on the segments produced by extract.extract(), so it can be
unit-tested with plain dicts and no PDF at all (see tests/test_profile_gen.py).
"""
import argparse
import collections
import os
import re
import sys

# Thresholds, kept together so they are easy to tune and to test against.
FOOTER_BAND = 0.12        # bottom fraction of the page searched for running footers
FOOTER_MIN_SHARE = 0.3    # a footer line must repeat on at least this share of pages
HEADING_RATIO = 1.15      # text this much bigger than body text is a heading candidate
DIVIDER_RATIO = 3.0       # a page whose biggest text is this much bigger is a divider candidate
DIVIDER_MAX_SEGS = 8      # ...and has at most this many text segments
COLUMN_MIN_SHARE = 0.3    # each half must hold this share of a page's segments to count as a column
COLUMN_MIN_SEGS = 12      # pages with fewer segments are never treated as two-column
CONTENTS_RE = re.compile(r"^(table of )?contents$", re.I)
NUMBER_RE = re.compile(r"\d+")


def _weighted_mode(pairs):
    """Most common value, weighted: pairs of (value, weight)."""
    c = collections.Counter()
    for v, w in pairs:
        c[v] += w
    return c.most_common(1)[0][0] if c else None


def body_size(pages):
    """Body text size: the size that carries the most characters of regular/bold text."""
    return _weighted_mode((s["size"], len(s["text"])) for p in pages for s in p["segs"]
                          if s["kind"] in ("R", "B", "S") and s["text"].strip())


def _footer_pattern(text):
    """Turn one footer line into a regex: numbers become \\d{1,4}, the rest is escaped."""
    parts = NUMBER_RE.split(text.strip())
    return "^" + r"\d{1,4}".join(re.escape(p) for p in parts) + "$"


def footers(pages):
    """-> (footer_y, [regex]) for lines that repeat in the bottom band of many pages."""
    if not pages:
        return 0, []
    seen, top = collections.defaultdict(set), collections.defaultdict(float)
    for p in pages:
        band = p["h"] * FOOTER_BAND
        for s in p["segs"]:
            if s["y1"] <= band and s["text"].strip():
                pat = _footer_pattern(s["text"])
                seen[pat].add(p["n"])
                top[pat] = max(top[pat], s["y1"])
    need = max(2, FOOTER_MIN_SHARE * len(pages))
    pats = sorted(k for k, v in seen.items() if len(v) >= need)
    if not pats:
        return 0, []
    return int(max(top[k] for k in pats)) + 2, pats


def heading_sizes(pages, body):
    """Distinct sizes above body text, biggest first, with how many segments use each."""
    if not body:
        return []
    c = collections.Counter(s["size"] for p in pages for s in p["segs"]
                            if s["size"] >= body * HEADING_RATIO and s["text"].strip())
    return sorted(c.items(), key=lambda kv: -kv[0])


def dividers(pages, body):
    """-> (divider_size or None, [page numbers]) for sparse pages with one huge text (page 1 excluded)."""
    if not body:
        return None, []
    hits = []
    for p in pages:
        if p["n"] == 1:                     # page 1 is the cover / title page
            continue
        segs = [s for s in p["segs"] if s["text"].strip()]
        if segs and len(segs) <= DIVIDER_MAX_SEGS:
            big = max(s["size"] for s in segs)
            if big >= body * DIVIDER_RATIO:
                hits.append((p["n"], big))
    if len(hits) < 2:                       # one big page is usually the title page, not a divider
        return None, []
    return int(min(b for _, b in hits)), [n for n, _ in hits]


def column_pages(pages):
    """Pages whose text sits in two halves (left column ends before, right starts after, the middle)."""
    out = []
    for p in pages:
        segs = [s for s in p["segs"] if s["text"].strip()]
        if len(segs) < COLUMN_MIN_SEGS:
            continue
        mid = p["w"] / 2
        left = sum(s["x1"] < mid for s in segs)
        right = sum(s["x0"] > mid for s in segs)
        if min(left, right) >= COLUMN_MIN_SHARE * len(segs):
            out.append(p["n"])
    return out


def code_max_size(pages, body):
    """Largest monospace size at or below body size, or None when the book has no code."""
    sizes = [s["size"] for p in pages for s in p["segs"] if s["kind"] == "M" and s["text"].strip()]
    sizes = [z for z in sizes if not body or z <= body]
    return max(sizes) if sizes else None


def contents_pages(pages, first=15):
    """Early pages whose biggest text reads "Contents": candidates for skip_pages."""
    out = []
    for p in pages:
        if p["n"] > first:
            break
        segs = [s for s in p["segs"] if s["text"].strip()]
        if segs and CONTENTS_RE.match(max(segs, key=lambda s: s["size"])["text"].strip()):
            out.append(p["n"])
    return out


def analyze(pages):
    """Every measurement the draft needs, as one dict."""
    body = body_size(pages)
    fy, fres = footers(pages)
    dsize, dpages = dividers(pages, body)
    return dict(pages=len(pages), body_size=body, footer_y=fy, footer_res=fres,
                heading_sizes=heading_sizes(pages, body), divider_size=dsize, divider_pages=dpages,
                column_pages=column_pages(pages), code_max_size=code_max_size(pages, body),
                skip_pages=contents_pages(pages))


def _toml_str(s):
    """A TOML string: literal ('...') when possible, so regex backslashes stay readable."""
    if "'" not in s and "\n" not in s:
        return "'" + s + "'"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _toml_list(xs):
    return "[" + ", ".join(_toml_str(x) if isinstance(x, str) else str(x) for x in xs) + "]"


def render(a, pdf_name):
    """The analysis as a commented TOML profile. Only keys known to profile.DEFAULTS are written."""
    L = [f"# Draft profile for {pdf_name}, generated by pdf2epub-profile.",
         "# Review every value, then convert:  pdf2epub book.pdf -p <this file>",
         f"# Measured on {a['pages']} pages. Body text size: {a['body_size']}.",
         "",
         "# title, author and out_name default to the PDF metadata and file name.",
         'lang = "en"',
         "cover_page = 1"]
    L.append(f"skip_pages = {_toml_list(a['skip_pages'])}"
             + ("   # looks like a contents page" if a["skip_pages"] else ""))
    L.append("")
    if a["footer_res"]:
        L.append(f"footer_y = {a['footer_y']}                  # running footers found below this baseline")
        L.append("footer_res = [")
        L += [f"  {_toml_str(r)}," for r in a["footer_res"]]
        L.append("]")
    else:
        L.append("# No repeated footer lines found. Set footer_y / footer_res if the EPUB shows page furniture.")
    L.append("")
    if a["divider_size"]:
        L.append(f"divider_size = {a['divider_size']}             # divider pages: {_toml_list(a['divider_pages'])}")
        L.append('# stage_title = "Part {n} — {title}"   # how a divider page becomes a chapter title')
    else:
        L.append("# No divider pages found (sparse pages with one very large text).")
    if a["column_pages"]:
        L.append(f"column_pages = {_toml_list(a['column_pages'])}   # two-column layout")
    if a["code_max_size"]:
        L.append(f"code_max_size = {a['code_max_size']}           # largest monospace size seen")
    L.append("")
    if a["heading_sizes"]:
        L.append("# Heading candidates (size: segments). Use them to decide heading_fonts,")
        L.append("# h19_as_h3 and eyebrow_re by looking at the pages that use them:")
        L += [f"#   {size}: {count}" for size, count in a["heading_sizes"][:8]]
    L += ["",
          "# Still manual (see profiles/example.toml):",
          "# heading_fonts, eyebrow_re, h3_labels, aside_labels, acronyms,",
          "# diagram_figures, dialog_figures, figure_alt_extra, expected_nav."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Draft a pdf2epub TOML profile from a PDF")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", help="output TOML (default: profiles/<pdf name>.local.toml, which git ignores)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing file")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.pdf):
        sys.exit(f"not a file: {a.pdf}")
    stem = os.path.splitext(os.path.basename(a.pdf))[0]
    out = a.out or os.path.join("profiles", re.sub(r"[^\w.-]+", "-", stem) + ".local.toml")
    if os.path.exists(out) and not a.force:
        sys.exit(f"{out} exists; use --force to overwrite it")

    from .extract import extract
    from .profile import load_profile
    load_profile(None, a.pdf)                 # neutral defaults for extraction
    pages, _ = extract(a.pdf)
    text = render(analyze(pages), os.path.basename(a.pdf))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote", out)


if __name__ == "__main__":
    main()
