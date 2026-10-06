"""EXTRACT - PDF -> pages of text segments (position, size, kind, styled chars)."""
import collections
import re

from .profile import P

LIG = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}


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
