"""VALIDATE - checks on the finished EPUB file; returns [(name, ok, detail)]."""
import json
import os
import re
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET

from .profile import P

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
