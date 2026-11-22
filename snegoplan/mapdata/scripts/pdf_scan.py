"""Scan all PDF pages for keywords, print page numbers with matching lines.
Usage: pdf_scan.py file.pdf kw1 kw2 ...
"""
import sys
import pdfplumber

path = sys.argv[1]
kws = [k.lower() for k in sys.argv[2:]]
with pdfplumber.open(path) as pdf:
    for i, page in enumerate(pdf.pages):
        txt = page.extract_text() or ''
        hits = []
        for line in txt.splitlines():
            low = line.lower()
            if any(k in low for k in kws):
                hits.append(line.strip())
        if hits:
            print(f'=== p{i+1}')
            for h in hits[:12]:
                print('   ', h[:180])
