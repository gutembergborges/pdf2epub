"""pdf2epub - turn a design-heavy PDF into a reflowable EPUB 3 for a Kindle.

Book-specific rules live in a TOML profile (-p); see profiles/example.toml.
Needs pdfminer.six (text, fonts, positions, link annotations) and poppler's
`pdftoppm` (cover and figure images). Optional: PyYAML, to parse YAML blocks.
"""
