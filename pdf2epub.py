#!/usr/bin/env python3
"""pdf2epub.py - turn a design-heavy PDF into a reflowable EPUB 3 for a Kindle.

Usage:
    python pdf2epub.py input.pdf -o out/
    python pdf2epub.py input.pdf -o out/ -p profiles/my-book.toml

Needs: pdfminer.six (text, fonts, positions, link annotations) and poppler's
`pdftoppm` (cover and figure images). Optional: PyYAML, to parse YAML blocks.
The script was written for PyMuPDF; pdfminer.six was used because PyMuPDF could
not be installed in the build environment. Book-specific rules live in a TOML profile (-p); see profiles/example.toml.
"""
import argparse, collections, datetime, json, os, re, shutil, subprocess, sys
import tempfile, uuid, zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as esc, quoteattr

# =============================================================================
# PROFILE - book-specific rules come from a TOML file (-p/--profile). Nothing in
# this script is book-specific; DEFAULTS are neutral. See profiles/example.toml.
# =============================================================================
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
LIG = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
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


# =============================================================================
# EXTRACT - PDF -> pages of text segments (position, size, kind, styled chars)
# =============================================================================
def _fam(fontname):
    f = fontname.split("+")[-1]
    fam = "M" if "Mono" in f else "S" if "Sans" in f else "H" if any(k in f for k in P["heading_fonts"]) else "R"
    return fam, "Semibold" in f, "Italic" in f


def _chars(info):
    """Styled characters with spaces rebuilt from glyph gaps. info: raw tuples."""
    out, px = [], None
    for t, x0, x1, sz, fam, b, i, href in info:
        st = (fam == "M", b, i, href)
        if t.isspace():
            if out and out[-1][0] != " ":
                out.append((" ", out[-1][1]))
            px = x1
            continue
        if "" <= t <= "":          # icon-font glyphs are decoration
            px = x1
            continue
        if out and out[-1][0] != " " and px is not None and x0 - px > (0.2 if fam == "S" else P["space_em"]) * sz:
            out.append((" ", out[-1][1]))
        for ch in LIG.get(t, t):
            out.append((ch, st))
        px = x1
    while out and out[-1][0] == " ":
        out.pop()
    while out and out[0][0] == " ":
        out.pop(0)
    text = "".join(c for c, _ in out)
    drop = set()                                # collapse letter-spaced labels ("W O R K E D")
    for m in re.finditer(r"(?<!\w)(?:\w ){3,}\w(?!\w)", text):
        drop.update(k for k in range(m.start(), m.end()) if text[k] == " ")
    return [c for k, c in enumerate(out) if k not in drop], len(drop)


def _make_seg(cs, links, stats):
    info = []
    for c in cs:
        fam, b, i = _fam(c.fontname)
        cx, cy = (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2
        href = next((u for (a, b_, c_, d, u) in links if a - 1 <= cx <= c_ + 1 and b_ - 2 <= cy <= d + 2), None)
        info.append((c.get_text(), c.x0, c.x1, round(c.size, 1), fam, b, i, href))
    nonsp = [t for t in info if not t[0].isspace()]
    if not nonsp:
        return None
    nm = [t for t in nonsp if t[4] != "M"]
    pool = nm if nm and len(nm) >= 0.15 * len(nonsp) else nonsp
    fam = collections.Counter(t[4] for t in pool).most_common(1)[0][0]
    same = [t for t in pool if t[4] == fam]
    size = collections.Counter(t[3] for t in same).most_common(1)[0][0]
    kind = "B" if fam == "R" and sum(t[5] for t in same) > len(same) / 2 else fam
    chars, dropped = _chars(info)
    stats["despaced"] += dropped
    return dict(x0=min(t[1] for t in nonsp), x1=max(t[2] for t in nonsp),
                y0=min(c.y0 for c in cs), y1=max(c.y1 for c in cs), size=size, kind=kind,
                chars=chars, text="".join(c for c, _ in chars), raw=[(t[0], t[1]) for t in info],
                href=any(t[7] for t in info), uri=next((t[7] for t in info if t[7]), None))


def _split_line(chars):
    """Split one text line into segments at big horizontal gaps (columns, label/value)."""
    from pdfminer.layout import LTChar
    cs = sorted([c for c in chars if isinstance(c, LTChar)], key=lambda c: c.x0)
    if not cs:
        return []
    nonsp = [c for c in cs if not c.get_text().isspace()]
    mono = sum("Mono" in c.fontname for c in nonsp) > 0.6 * max(1, len(nonsp))
    groups, cur = [], [cs[0]]
    for c in cs[1:]:
        thr = (P["mono_split_em"] if mono else 1.5) * c.size
        if c.x0 - cur[-1].x1 > thr:
            groups.append(cur)
            cur = []
        cur.append(c)
    groups.append(cur)
    return groups


def _links(pdf_path):
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdftypes import resolve1
    out = {}
    with open(pdf_path, "rb") as fh:
        doc = PDFDocument(PDFParser(fh))
        for n, pg in enumerate(PDFPage.create_pages(doc), 1):
            for a in resolve1(pg.annots) or []:
                a = resolve1(a)
                act = resolve1(a.get("A")) if a.get("A") else None
                uri = resolve1(act.get("URI")) if act and act.get("URI") else None
                if uri:
                    r = [float(x) for x in resolve1(a["Rect"])]
                    out.setdefault(n, []).append((r[0], r[1], r[2], r[3], uri.decode("latin-1")))
    return out


def extract(pdf_path, only=None):
    """-> (pages, stats). page = dict(n, w, h, segs, gfx)."""
    import logging
    logging.disable(logging.CRITICAL)
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTCurve, LTFigure, LTTextLine
    links, stats, pages = _links(pdf_path), collections.Counter(), []

    def walk(items, segs, gfx, lk):
        for e in items:
            if isinstance(e, LTTextLine):
                for grp in _split_line(list(e)):
                    s = _make_seg(grp, lk, stats)
                    if s:
                        segs.append(s)
            elif isinstance(e, LTFigure):
                gfx.append((e.x0, e.y0, e.x1, e.y1))
                walk(e, segs, gfx, lk)
            elif isinstance(e, LTCurve):
                gfx.append((e.x0, e.y0, e.x1, e.y1))
            elif hasattr(e, "__iter__"):
                walk(e, segs, gfx, lk)

    for n, lp in enumerate(extract_pages(pdf_path, laparams=LAParams(char_margin=20, line_margin=0.3,
                                                                      boxes_flow=None, all_texts=True)), 1):
        if only and n not in only:
            continue
        segs, gfx = [], []
        walk(lp, segs, gfx, links.get(n, []))
        pages.append(dict(n=n, w=lp.width, h=lp.height, segs=segs, gfx=gfx))
    stats["links"] = sum(len(v) for v in links.values())
    return pages, stats


# =============================================================================
# STRUCTURE - segments -> chapters of HTML blocks (paragraphs, tables, code, figures)
# =============================================================================
def _label_case(t):
    if not t.isupper():
        return t
    t = t.capitalize()
    for a in P["acronyms"]:
        t = re.sub(r"\b%s\b" % a, a, t, flags=re.I)
    return t


def _runs(chars, bold=True):
    groups = []
    for ch, st in chars:
        if groups and groups[-1][1] == st:
            groups[-1][0].append(ch)
        else:
            groups.append(([ch], st))
    out = []
    for chs, (mono, b, it, href) in groups:
        t = "".join(chs)
        core = t.strip()
        if not core:
            out.append(" ")
            continue
        lead, trail = t[:len(t) - len(t.lstrip())], t[len(t.rstrip()):]
        s = esc(core)
        if mono:
            s = "<code>%s</code>" % s
        if b and bold:
            s = "<strong>%s</strong>" % s
        if it:
            s = "<em>%s</em>" % s
        if href:
            s = "<a href=%s>%s</a>" % (quoteattr(href), s)
        out.append(lead + s + trail)
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def _pchars(p):
    """Join the lines of a paragraph; no space after a wrap hyphen or inside one link."""
    out = []
    for ln in p["lines"]:
        c = ln["chars"]
        if out and c:
            prev, nxt = out[-1], c[0]
            joined = prev[0] == "-" and nxt[0].islower()
            samelink = prev[1][3] and prev[1][3] == nxt[1][3]
            if not (joined or samelink):
                out.append((" ", prev[1]))
        out.extend(c)
    return out


def _compat(a, b):
    if (a["kind"] == "S") != (b["kind"] == "S"):
        return False
    if abs(a["size"] - b["size"]) <= 0.6:
        return True
    return "M" in (a["kind"], b["kind"]) and abs(a["size"] - b["size"]) <= 2.5


def _paragraphs(segs):
    segs = sorted(segs, key=lambda s: (-round(s["y0"]), s["x0"]))
    paras = []
    for s in segs:
        best = None
        for p in paras:
            l = p["lines"][-1]
            dy = l["y0"] - s["y0"]
            reach = 3 * s["size"] if s["uri"] and s["uri"] == l["uri"] else 1.8 * max(s["size"], l["size"])
            if not (2 < dy <= reach and _compat(l, s)):
                continue
            dx = s["x0"] - l["x0"]
            hang = len(p["lines"]) == 1 and 0 < dx <= 22 and re.match(r"\d+\.\s", p["lines"][0]["text"])
            if abs(dx) <= 2.5 or hang:
                if best is None or dy < best[0]:
                    best = (dy, p)
        if best:
            best[1]["lines"].append(s)
        else:
            paras.append(dict(type="p", lines=[s]))
    for p in paras:
        _box(p)
    return paras


def _box(p):
    ls = p["lines"]
    p.update(x0=ls[0]["x0"], x1=max(l["x1"] for l in ls), ytop=ls[0]["y1"], ybot=ls[-1]["y0"],
             size=ls[0]["size"], kind=ls[0]["kind"])


def _code_blocks(code, others):
    code = sorted(code, key=lambda s: -s["y0"])
    blocks, cur = [], []
    for s in code:
        if cur:
            prev = cur[-1]
            between = any(s["y0"] < o["y0"] < prev["y0"] - 2 for o in others)
            if between or prev["y0"] - s["y0"] > 6 * prev["size"] * 1.7:
                blocks.append(cur)
                cur = []
        cur.append(s)
    if cur:
        blocks.append(cur)
    out = []
    for b in blocks:
        base, size = min(l["x0"] for l in b), b[0]["size"]
        cw = 0.6 * size
        steps = [b[i]["y0"] - b[i + 1]["y0"] for i in range(len(b) - 1)]
        pitch = min([d for d in steps if d > 2] or [size * 1.69])
        lines = []
        for i, l in enumerate(b):
            if i:
                lines.extend([""] * max(0, round((b[i - 1]["y0"] - l["y0"]) / pitch) - 1))
            buf = []
            for ch, x0 in l["raw"]:
                if "" <= ch <= "":
                    continue
                col = max(round((x0 - base) / cw), len(buf))
                buf.extend([" "] * (col - len(buf)))
                buf.append(ch)
            lines.append("".join(buf).rstrip())
        out.append(dict(type="code", lines=b, text="\n".join(lines), x0=base, x1=max(l["x1"] for l in b),
                        ytop=b[0]["y1"], ybot=b[-1]["y0"], size=size, kind="M"))
    return out


def _is_body(s):
    return s["kind"] in "RB" and 10 <= s["size"] <= 12.5 and len(s["text"]) >= 20 and s["x0"] <= 100


def _figures(pg, segs, ctx):
    """Replace figure regions by one pseudo item. Diagrams -> PNG request; dialogs -> text."""
    items, rest = [], list(segs)
    caps = [s for s in segs if re.match(r"FIGURE\s*\d", s["text"])]
    for cap in caps:
        m = re.match(r"FIGURE\s*(\d+)\s*(.*)", cap["text"])
        n, title = int(m.group(1)), m.group(2).strip()
        below = [s for s in rest if s is not cap and s["y1"] <= cap["y0"] + 3]
        if n in P["dialog_figures"]:
            inside = below
            html = _dialog(n, title, inside)
        elif n in P["diagram_figures"]:
            body = [s for s in below if _is_body(s)]
            limit = max([s["y1"] for s in body] or [P["footer_y"]])
            inside = [s for s in below if s["y0"] > limit]
            boxes = [g for g in pg["gfx"] if g[1] >= limit and g[3] <= cap["y0"] + 3
                     and not (g[2] - g[0] >= 590 and g[3] - g[1] >= 700) and not (g[3] - g[1] < 2 and g[2] - g[0] > 300)]
            xs = [s["x0"] for s in inside] + [g[0] for g in boxes]
            xe = [s["x1"] for s in inside] + [g[2] for g in boxes]
            ys = [s["y0"] for s in inside] + [g[1] for g in boxes]
            yt = [s["y1"] for s in inside] + [g[3] for g in boxes]
            bbox = (max(0, min(xs) - 8), max(0, min(ys) - 8), min(pg["w"], max(xe) + 8), min(max(yt) + 8, cap["y0"] - 2))
            labels = [s["text"] for s in sorted(inside, key=lambda s: (-round(s["y0"]), s["x0"])) if s["text"]]
            alt = "Figure %d: %s." % (n, title)
            if labels:
                alt += " Text in the diagram: " + " · ".join(dict.fromkeys(labels))[:700]
            alt += " " + P["figure_alt_extra"].get(n, "")
            name = "fig%d.png" % n
            ctx["figs"].append(dict(file=name, page=pg["n"], bbox=bbox, h=pg["h"]))
            html = ('<figure id="fig%d"><img src="images/%s" alt=%s/><figcaption>Figure %d. %s</figcaption></figure>'
                    % (n, name, quoteattr(alt), n, esc(title)))
        else:
            continue
        ids = {id(s) for s in inside}
        rest = [s for s in rest if id(s) not in ids and s is not cap]
        items.append(dict(type="fig", html=html, x0=72, x1=540, ytop=cap["y1"],
                          ybot=min([cap["y0"]] + [s["y0"] for s in inside]), size=8, kind="F"))
    return rest, items


def _dialog(n, title, inside):
    rows = collections.defaultdict(list)
    for s in inside:
        if s["kind"] == "S" and s["size"] <= 7.5 and len(s["text"]) <= 3:      # avatar initials
            continue
        rows[round(s["y0"] / 2)].append(s)
    head, msgs, note, cur = [], [], [], None
    for k in sorted(rows, reverse=True):
        row = sorted(rows[k], key=lambda s: s["x0"])
        text = " ".join(s["text"] for s in row)
        m = re.match(r"^(.+?)\s*(\d{1,2}:\d{2})$", text) if row[0]["kind"] == "S" else None
        if m:
            cur = dict(who=m.group(1), time=m.group(2), segs=[])
            msgs.append(cur)
        elif cur is None:
            head.extend(row)
        elif row[0]["kind"] == "S" and row[0]["x0"] < 100:
            note.extend(row)
        else:
            cur["segs"].extend(row)

    def paras(segs):
        return "".join("<p>%s</p>" % _runs(_pchars(p), False) for p in _paragraphs(segs))
    out = ['<figure class="dialog" id="fig%d"><figcaption>Figure %d. %s</figcaption>' % (n, n, esc(title))]
    if head:
        out.append('<div class="chan">%s</div>' % paras(head))
    for m in msgs:
        out.append('<div class="msg"><p class="who"><strong>%s</strong> <span class="time">%s</span></p>%s</div>'
                   % (esc(m["who"]), m["time"], paras(m["segs"])))
    if note:
        out.append('<div class="note">%s</div>' % paras(note))
    return "".join(out) + "</figure>"


def _bands(items):
    items = sorted(items, key=lambda p: -p["ytop"])
    bands = []
    for p in items:
        if bands and p["ytop"] > bands[-1]["ybot"] + 1:
            bands[-1]["items"].append(p)
            bands[-1]["ybot"] = min(bands[-1]["ybot"], p["ybot"])
        else:
            bands.append(dict(items=[p], ybot=p["ybot"]))
    return bands


def _columns(band):
    its = sorted(band["items"], key=lambda p: p["x0"])
    cols = [[its[0]]]
    for p in its[1:]:
        if p["x0"] - cols[-1][-1]["x0"] > 25:
            cols.append([p])
        else:
            cols[-1].append(p)
    return [sorted(c, key=lambda p: -p["ytop"]) for c in cols]


def _ptext(p):
    return "".join(c for c, _ in _pchars(p))


def _new_chapter(ctx, title):
    n = len(ctx["chapters"]) + 1
    ch = dict(title=title, id="c%d" % n, file="ch%02d.xhtml" % n, blocks=[], nav=[])
    ctx["chapters"].append(ch)
    ctx["target"] = ch["blocks"]
    return ch


def _heading(ctx, level, text):
    ctx["hid"] += 1
    hid = "h%d" % ctx["hid"]
    ch = ctx["chapters"][-1]
    if level == 2:
        ch["nav"].append((text, hid))
    else:
        ctx["h3"] = text
    ctx["target"].append(["h%d" % level, '<h%d id="%s">%s</h%d>' % (level, hid, esc(text), level), 0])


def _lang(ctx, text):
    m = re.search(r"\.(json|ya?ml|sh)\b", ctx["h3"])
    if m:
        return {"yml": "yaml"}.get(m.group(1), m.group(1))
    t = text.lstrip()
    if t.startswith(("{", "[")):
        return "json"
    if re.match(r"- \w[\w ]*:", t):
        return "yaml"
    if t.startswith("---"):
        return "markdown"
    return ""


def _emit(p, ctx, aside, multi):
    s = p["lines"][0] if "lines" in p else None
    out = ctx["aside"]["blocks"] if aside else ctx["target"]
    if p["type"] == "fig":
        out.append(["html", p["html"], p["x0"]])
        return
    if p["type"] == "code":
        lang = _lang(ctx, p["text"])
        cls = ' class="language-%s"' % lang if lang else ""
        out.append(["code", "<pre><code%s>%s</code></pre>" % (cls, esc(p["text"])), p["x0"]])
        ctx["stats"]["code"] += 1
        return
    chars = _pchars(p)
    txt = "".join(c for c, _ in chars)
    kind, size, x0 = p["kind"], p["size"], p["x0"]
    if kind == "S" and P["eyebrow_re"] and re.fullmatch(P["eyebrow_re"], txt):
        ctx["eyebrow"] = txt
        return
    if size >= 28:
        if ctx["eyebrow"] in P["opener_as_section"] and ctx["chapters"]:
            _heading(ctx, 2, txt)
        else:
            _new_chapter(ctx, txt)
            ctx["target"].append(["h1", '<h1 id="%s">%s</h1>' % (ctx["chapters"][-1]["id"], esc(txt)), 0])
        return
    if 22 <= size <= 25:
        _heading(ctx, 2, txt)
        return
    if 18 <= size < 22 and kind in "HR":
        _heading(ctx, 3 if txt in P["h19_as_h3"] else 2, txt)
        return
    if kind == "S" and size >= 10.5:
        _heading(ctx, 3, txt)
        return
    if kind == "S" and txt.isupper() and any(txt.lower().startswith(h) for h in P["h3_labels"]):
        _heading(ctx, 3, _label_case(txt))
        return
    if kind == "S":
        out.append(["p", '<p class="label"><strong>%s</strong></p>' % esc(_label_case(txt) if txt.isupper() else txt), x0])
        return
    if kind == "H" and 15.5 <= size < 18 and x0 < 85 and not aside:
        out.append(["p", '<p class="lead">%s</p>' % _runs(chars, False), x0])
        return
    if kind == "H" and 12 <= size < 18 and aside:
        out.append(["p", '<p class="boxtitle"><strong>%s</strong></p>' % esc(txt), x0])
        return
    html = _runs(chars, kind not in "H")
    if not aside and not multi and ((kind == "H" and 12 <= size < 18 and x0 >= 90) or (kind in "RB" and size >= 11.5 and x0 >= 190)):
        out.append(["quote", "<blockquote><p>%s</p></blockquote>" % html, x0])
        return
    if not aside and not multi and kind in "RBM" and size >= 11.5 and 88 <= x0 < 110:
        out.append(["li", html, x0])
        return
    m = re.match(r"(\d+)\.\s+", txt)
    if m and not aside and kind in "RB" and size >= 11.5:
        out.append(["oli", _runs(chars[len(m.group(0)):], True), x0])
        return
    if chars and all(st[3] for _, st in chars) and out and out[-1][0] == "p" and abs(out[-1][2] - x0) < 3 \
            and "<a " not in out[-1][1]:                  # a bare link joins the entry above it
        out[-1][1] = out[-1][1][:-4] + "<br/>" + html + "</p>"
        return
    out.append(["p", "<p>%s</p>" % html, x0])


def _close_aside(ctx):
    a = ctx["aside"]
    if not a:
        return
    prev = ctx["chapters"][-1]["blocks"][-1] if ctx["chapters"][-1]["blocks"] else None
    if prev and prev[0] == "aside" and a["label"].startswith(prev[3]):
        prev[1].extend(a["blocks"])
    else:
        ctx["chapters"][-1]["blocks"].append(["aside", a["blocks"], 0, a["label"]])
    ctx["aside"] = None


def _divider(segs, ctx):
    segs = sorted(segs, key=lambda s: -s["y0"])
    num = int(re.sub(r"\D", "", segs[0]["text"]))
    big = [s for s in segs[1:] if s["size"] == segs[1]["size"]]
    title = " ".join(s["text"] for s in big)
    tag = _paragraphs([s for s in segs[1:] if s not in big])
    ch = _new_chapter(ctx, P["stage_title"].format(n=num, title=title))
    ch["blocks"].append(["h1", '<h1 id="%s">%s</h1>' % (ch["id"], esc(ch["title"])), 0])
    for p in tag:
        ch["blocks"].append(["p", '<p class="tagline">%s</p>' % _runs(_pchars(p), False), 0])


def _is_header(cols):
    return len(cols) >= 2 and all(c[0]["type"] == "p" and c[0]["kind"] == "S" and len(c[0]["lines"]) == 1
                                  and _ptext(c[0]).isupper() for c in cols)


def _page(pg, ctx):
    segs = [s for s in pg["segs"] if s["y0"] >= P["footer_y"]
            and not (s["y0"] < 80 and any(re.match(r, s["text"]) for r in P["footer_res"]))]
    if not segs:
        return
    if max(s["size"] for s in segs) >= P["divider_size"]:
        return _divider(segs, ctx)
    rest, items = _figures(pg, segs, ctx)
    code = [s for s in rest if s["kind"] == "M" and s["size"] <= P["code_max_size"] and not s["href"]]
    text = [s for s in rest if s not in code]
    items += _paragraphs(text) + _code_blocks(code, text)
    ctx["eyebrow"] = None
    groups = [items]
    if pg["n"] in P["column_pages"]:                      # header, left column, right column, footer
        right = [p for p in items if p["x0"] >= pg["w"] / 2]
        top, low = max(p["ytop"] for p in right), min(p["ybot"] for p in right)
        head = [p for p in items if p["ytop"] > top + 2]
        foot = [p for p in items if p["ytop"] < low - 1 and p not in right]
        body = [p for p in items if p not in head and p not in foot]
        groups = [head, [p for p in body if p not in right], [p for p in body if p in right], foot]
    for group in groups:
        _flow(_bands(group), ctx)
    _close_aside(ctx)


def _flow(bands, ctx):
    i = 0
    while i < len(bands):
        cols = _columns(bands[i])
        first = cols[0][0]
        if (not ctx["aside"] and first["type"] == "p" and first["kind"] == "S"
                and _ptext(first).upper().startswith(P["aside_labels"])):
            ctx["aside"] = dict(label=_label_case(_ptext(first)), blocks=[])
        if _is_header(cols) and not ctx["aside"]:
            hx = [(c[0]["x0"], _label_case(_ptext(c[0]))) for c in cols]
            i += 1
            while i < len(bands) and len(_columns(bands[i])) >= 2:
                for col in _columns(bands[i]):
                    name = min(hx, key=lambda h: abs(h[0] - col[0]["x0"]))[1]
                    if col[0]["kind"] == "B" and len(col[0]["lines"]) == 1:
                        ctx["target"].append(["p", '<p class="rowlabel">%s</p>' % _runs(_pchars(col[0]), True), 0])
                        continue
                    ch = _pchars(col[0])
                    ctx["target"].append(["p", "<p><strong>%s:</strong> %s</p>" % (esc(name), _runs(ch, False)), 0])
                    for q in col[1:]:
                        _emit(q, ctx, False, True)
                i += 1
            continue
        for col in cols:
            for p in col:
                _emit(p, ctx, bool(ctx["aside"]), len(cols) > 1)
        i += 1


def structure(pages):
    ctx = dict(chapters=[], figs=[], eyebrow=None, hid=0, stats=collections.Counter(), aside=None,
               target=None, h3="")
    for pg in pages:
        if pg["n"] == P["cover_page"] or pg["n"] in P["skip_pages"]:
            continue
        _page(pg, ctx)
    return dict(chapters=ctx["chapters"], figs=ctx["figs"], stats=ctx["stats"])


def render_blocks(blocks):
    out, i = [], 0
    while i < len(blocks):
        t = blocks[i][0]
        if t in ("li", "oli"):
            tag, items = ("ul" if t == "li" else "ol"), []
            while i < len(blocks) and blocks[i][0] == t:
                items.append("<li>%s</li>" % blocks[i][1])
                i += 1
            out.append("<%s>%s</%s>" % (tag, "".join(items), tag))
            continue
        if t == "aside":
            out.append('<aside class="box">%s</aside>' % render_blocks(blocks[i][1]))
        else:
            out.append(blocks[i][1])
        i += 1
    return "\n".join(out)


# =============================================================================
# EPUB WRITER - chapters + images -> EPUB 3 zip
# =============================================================================
CSS = """body { margin: 0 0.4em; line-height: 1.45; }
h1 { font-size: 1.7em; margin: 1em 0 0.6em; line-height: 1.2; }
h2 { font-size: 1.35em; margin: 1.8em 0 0.5em; line-height: 1.25; page-break-after: avoid; }
h3 { font-size: 1.1em; margin: 1.4em 0 0.4em; page-break-after: avoid; }
p { margin: 0.6em 0; }
p.lead { font-size: 1.1em; font-style: italic; }
p.tagline { font-size: 1.1em; margin-top: 1em; }
p.label { font-size: 0.85em; margin: 1em 0 0.2em; }
p.rowlabel { margin: 1.2em 0 0.2em; }
p.boxtitle { font-size: 1.1em; margin: 0.4em 0; }
ul, ol { margin: 0.6em 0; padding-left: 1.4em; }
li { margin: 0.4em 0; }
blockquote { margin: 1em 1.2em; padding-left: 0.8em; border-left: 0.2em solid; font-style: italic; }
aside.box { margin: 1.2em 0; padding: 0.4em 0.8em; border: 1px solid; font-size: 0.92em; }
pre { margin: 0.8em 0; padding: 0.4em 0.5em; border: 1px solid; font-size: 0.78em; line-height: 1.3;
      white-space: pre-wrap; word-break: break-word; overflow-wrap: break-word; }
code { font-family: monospace; font-size: 0.9em; }
pre code { font-size: 1em; }
figure { margin: 1em 0; text-align: center; }
figure.dialog { text-align: left; }
figcaption { font-size: 0.85em; margin: 0.4em 0; }
img { max-width: 100%; height: auto; }
div.msg { margin: 0.6em 0; }
div.msg p { margin: 0.1em 0; }
span.time { font-size: 0.85em; }
div.note p, div.chan p { font-size: 0.9em; }
body.cover { margin: 0; text-align: center; }
body.title { text-align: center; margin-top: 25%; }
"""


def _xhtml(title, body, cls=""):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="%s" xml:lang="%s">'
            '<head><meta charset="utf-8"/><title>%s</title><link rel="stylesheet" type="text/css" href="styles.css"/></head>'
            '<body%s>\n%s\n</body></html>' % (P["lang"], P["lang"], esc(title), (' class="%s"' % cls) if cls else "", body))


def render_image(pdf, page, dest, dpi, fmt, bbox=None, page_h=792):
    base = dest.rsplit(".", 1)[0]
    cmd = ["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-singlefile", "-" + fmt]
    if fmt == "jpeg":
        cmd += ["-jpegopt", "quality=85"]
    if bbox:
        k = dpi / 72.0
        x0, y0, x1, y1 = bbox
        cmd += ["-x", str(int(x0 * k)), "-y", str(int((page_h - y1) * k)),
                "-W", str(int((x1 - x0) * k) + 1), "-H", str(int((y1 - y0) * k) + 1)]
    subprocess.run(cmd + [pdf, base], check=True, stderr=subprocess.DEVNULL)
    return dest


def write_epub(book, pdf, outdir):
    if not shutil.which("pdftoppm"):
        sys.exit("pdftoppm (poppler) is required for the cover and figures")
    os.makedirs(outdir, exist_ok=True)
    tmp = tempfile.mkdtemp()
    files = {}                                         # zip path -> bytes
    images = []                                        # (zip path, media type, id)
    cover = render_image(pdf, P["cover_page"], os.path.join(tmp, "cover.jpg"), 150, "jpeg")
    files["OEBPS/images/cover.jpg"] = open(cover, "rb").read()
    images.append(("images/cover.jpg", "image/jpeg", "img-cover"))
    for f in book["figs"]:
        png = render_image(pdf, f["page"], os.path.join(tmp, f["file"]), 216, "png", f["bbox"], f["h"])
        files["OEBPS/images/" + f["file"]] = open(png, "rb").read()
        images.append(("images/" + f["file"], "image/png", "img-" + f["file"].split(".")[0]))
    shutil.rmtree(tmp, ignore_errors=True)

    title, author = P["title"], P["author"]
    alt = "Cover of %s by %s" % (title, author)
    files["OEBPS/cover.xhtml"] = _xhtml("Cover", '<div><img src="images/cover.jpg" alt=%s/></div>' % quoteattr(alt), "cover").encode()
    files["OEBPS/title.xhtml"] = _xhtml("Title page", '<h1>%s</h1>\n%s<p>%s</p>' % (esc(title), ('<p>%s</p>\n' % esc(P["subtitle"])) if P["subtitle"] else "", esc(author)), "title").encode()
    for ch in book["chapters"]:
        body = '<section epub:type="chapter">\n%s\n</section>' % render_blocks(ch["blocks"])
        files["OEBPS/" + ch["file"]] = _xhtml(ch["title"], body).encode()

    items = "".join('<li><a href="%s#%s">%s</a>%s</li>' % (c["file"], h, esc(c["title"]),
                    ("<ol>%s</ol>" % "".join('<li><a href="%s#%s">%s</a></li>' % (c["file"], i, esc(t)) for t, i in c["nav"])) if c["nav"] else "")
                    for c in book["chapters"] for h in [c["id"]])
    front = ('<li><a href="cover.xhtml">Cover</a></li><li><a href="title.xhtml">Title page</a></li>'
             '<li><a href="nav.xhtml">Contents</a></li>')
    nav = ('<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>%s%s</ol></nav>'
           '<nav epub:type="landmarks" hidden="hidden"><ol><li><a epub:type="cover" href="cover.xhtml">Cover</a></li>'
           '<li><a epub:type="toc" href="nav.xhtml">Contents</a></li>'
           '<li><a epub:type="bodymatter" href="%s">Start of the book</a></li></ol></nav>'
           % (front, items, book["chapters"][0]["file"]))
    files["OEBPS/nav.xhtml"] = _xhtml("Contents", nav).encode()
    files["OEBPS/styles.css"] = CSS.encode()

    now = datetime.datetime.now(datetime.timezone.utc)
    uid = "urn:uuid:%s" % uuid.uuid5(uuid.NAMESPACE_URL, "pdf2epub:%s:%s" % (title, author))
    man = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
           '<item id="css" href="styles.css" media-type="text/css"/>',
           '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
           '<item id="titlepage" href="title.xhtml" media-type="application/xhtml+xml"/>']
    for href, mt, iid in images:
        man.append('<item id="%s" href="%s" media-type="%s"%s/>' % (iid, href, mt, ' properties="cover-image"' if iid == "img-cover" else ""))
    for c in book["chapters"]:
        man.append('<item id="%s" href="%s" media-type="application/xhtml+xml"/>' % (c["id"], c["file"]))
    spine = ["cover", "titlepage", "nav"] + [c["id"] for c in book["chapters"]]
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="%s">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="bookid">%s</dc:identifier>'
           '<dc:title>%s</dc:title><dc:creator>%s</dc:creator><dc:language>%s</dc:language><dc:date>%s</dc:date>'
           '<meta property="dcterms:modified">%s</meta><meta name="cover" content="img-cover"/></metadata>'
           '<manifest>%s</manifest><spine>%s</spine></package>'
           % (P["lang"], uid, esc(title), esc(author), P["lang"], now.strftime("%Y-%m-%d"),
              now.strftime("%Y-%m-%dT%H:%M:%SZ"), "".join(man), "".join('<itemref idref="%s"/>' % s for s in spine)))
    files["OEBPS/content.opf"] = opf.encode()
    files["META-INF/container.xml"] = ('<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                                       '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>').encode()
    path = os.path.join(outdir, P["out_name"])
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in files.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return path


# =============================================================================
# VALIDATE - checks on the finished EPUB file; returns [(name, ok, detail)]
# =============================================================================
XH = "{http://www.w3.org/1999/xhtml}"


def _text(el):
    return "".join(el.itertext())


def validate(path):
    res = []

    def add(name, ok, detail=""):
        res.append((name, bool(ok), detail))
    z = zipfile.ZipFile(path)
    infos = z.infolist()
    add("mimetype first, stored, exact", infos[0].filename == "mimetype" and infos[0].compress_type == 0
        and z.read("mimetype") == b"application/epub+zip")
    docs, bad = {}, []
    for n in z.namelist():
        if n.endswith((".xhtml", ".opf", ".xml")):
            try:
                docs[n] = ET.fromstring(z.read(n))
            except ET.ParseError as e:
                bad.append("%s: %s" % (n, e))
    add("XHTML/XML well-formed", not bad, "; ".join(bad))
    opf = docs["OEBPS/content.opf"]
    ns = {"o": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
    man = {i.get("id"): i.get("href") for i in opf.findall(".//o:item", ns)}
    missing = [h for h in man.values() if "OEBPS/" + h not in z.namelist()]
    unlisted = [n for n in z.namelist() if n.startswith("OEBPS/") and n != "OEBPS/content.opf" and n[6:] not in man.values()]
    add("manifest files exist", not missing and not unlisted, "missing %s unlisted %s" % (missing, unlisted))
    add("spine ids in manifest", all(r.get("idref") in man for r in opf.findall(".//o:itemref", ns)))
    meta = {k: (opf.find(".//dc:" + k, ns).text if opf.find(".//dc:" + k, ns) is not None else None)
            for k in ("title", "creator", "language", "identifier", "date")}
    add("metadata (title, author, language, id, date)", meta["title"] == P["title"] and meta["creator"] == P["author"]
        and meta["language"] == P["lang"] and meta["identifier"] and meta["date"])
    # links, images, ids
    ids = {n: {e.get("id") for e in d.iter() if e.get("id")} for n, d in docs.items() if n.endswith(".xhtml")}
    broken, noalt = [], []
    for n, d in docs.items():
        if not n.endswith(".xhtml"):
            continue
        for e in d.iter():
            tgt = e.get("href") if e.tag == XH + "a" else e.get("src") if e.tag == XH + "img" else None
            if e.tag == XH + "img" and not (e.get("alt") or "").strip():
                noalt.append(n)
            if not tgt or re.match(r"^[a-z]+:", tgt):
                continue
            f, _, frag = tgt.partition("#")
            full = os.path.normpath(os.path.join(os.path.dirname(n), f)) if f else n
            if full not in z.namelist() or (frag and full in ids and frag not in ids[full]):
                broken.append("%s -> %s" % (n, tgt))
    add("internal links resolve", not broken, "; ".join(broken[:5]))
    add("every image has alt text", not noalt, str(noalt))
    # navigation
    nav = docs["OEBPS/nav.xhtml"]
    toc = next(e for e in nav.iter(XH + "nav") if e.get("{http://www.idpf.org/2007/ops}type") == "toc")
    got, front = [], []
    for li in toc.find(XH + "ol"):
        a = li.find(XH + "a").text
        sub = [x.find(XH + "a").text for x in li.findall(XH + "ol/" + XH + "li")]
        (front if a in P["front_nav"] else got).append((a, sub))
    nav_ok = got == P["expected_nav"] if P["expected_nav"] else bool(got)
    add("navigation matches expected structure", nav_ok and [f[0] for f in front] == P["front_nav"],
        "" if nav_ok else "got %s" % [g[0] for g in got])
    # css
    css = z.read("OEBPS/styles.css").decode()
    add("stylesheet: no colors, relative sizes", not re.search(r"(?<![-\w])(color|background[-\w]*)\s*:", css)
        and not re.search(r"font-size:\s*[\d.]+(px|pt)", css))
    # code blocks
    fails, n_json, n_yaml = [], 0, 0
    try:
        import yaml
    except ImportError:
        yaml = None
    for n, d in docs.items():
        if not n.endswith(".xhtml"):
            continue
        for k, code in enumerate(d.iter(XH + "code")):
            lang = (code.get("class") or "").replace("language-", "")
            txt = _text(code)
            try:
                if lang == "json":
                    n_json += 1
                    json.loads(txt)
                elif lang == "yaml" and yaml:
                    n_yaml += 1
                    yaml.safe_load(txt)
                elif lang == "markdown" and yaml and txt.startswith("---"):
                    n_yaml += 1
                    yaml.safe_load(txt.split("---")[1])
            except Exception as e:
                fails.append("%s block %d (%s): %s" % (n, k, lang, str(e).splitlines()[0]))
    add("JSON/YAML blocks parse (%d json, %d yaml%s)" % (n_json, n_yaml, "" if yaml else ", yaml skipped: no PyYAML"),
        not fails, "; ".join(fails))
    # leftovers
    allt = "\n".join(_text(d) for n, d in docs.items() if n.startswith("OEBPS/ch"))
    pcs = [_text(p).strip() for n, d in docs.items() if n.startswith("OEBPS/ch") for p in d.iter(XH + "p")]
    foot = [t for t in pcs if any(re.match(r, t) for r in P["footer_res"])]
    add("no footers / letter-spaced labels / ligatures left", not foot and not re.search(r"(?<!\w)(?:\w ){4,}\w(?!\w)", allt)
        and not re.search("[ﬀ-ﬆ-]", allt), "footers %s" % foot[:3])
    if shutil.which("epubcheck"):
        r = subprocess.run(["epubcheck", path], capture_output=True, text=True)
        add("epubcheck", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    return res


# =============================================================================
# MAIN
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="PDF to Kindle-friendly EPUB 3")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default="out")
    ap.add_argument("-p", "--profile", help="TOML file with book-specific rules (see profiles/example.toml)")
    a = ap.parse_args()
    load_profile(a.profile, a.pdf)
    pages, st = extract(a.pdf)
    book = structure(pages)
    path = write_epub(book, a.pdf, a.out)
    print("pages %d, chapters %d, sections %d, figures %d, code blocks %d, link annotations %d, despaced labels %d"
          % (len(pages), len(book["chapters"]), sum(len(c["nav"]) for c in book["chapters"]), len(book["figs"]),
             book["stats"]["code"], st["links"], st["despaced"]))
    res = validate(path)
    for name, ok, detail in res:
        print(("PASS  " if ok else "FAIL  ") + name + ("  -> " + detail if detail and not ok else ""))
    print("wrote", path)
    sys.exit(0 if all(ok for _, ok, _ in res) else 1)


if __name__ == "__main__":
    main()
