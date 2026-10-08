"""MAIN - command line entry point."""
import argparse
import sys

from .epub import write_epub
from .extract import extract
from .profile import load_profile
from .structure import structure
from .validate import validate


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
