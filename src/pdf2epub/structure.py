"""STRUCTURE - segments -> chapters of HTML blocks (paragraphs, tables, code, figures)."""
import collections
import re
from xml.sax.saxutils import escape as esc, quoteattr

from .profile import P


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
