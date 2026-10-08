"""EPUB WRITER - chapters + images -> EPUB 3 zip."""
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from xml.sax.saxutils import escape as esc, quoteattr

from .profile import P
from .structure import render_blocks

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
