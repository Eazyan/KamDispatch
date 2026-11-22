"""Survey a PDF: page count, text layer presence, first lines of each page.
Usage: pdf_survey.py file.pdf [max_pages]
"""
import sys
import pdfplumber

path = sys.argv[1]
max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 10**9
with pdfplumber.open(path) as pdf:
    print('pages:', len(pdf.pages))
    for i, page in enumerate(pdf.pages):
        if i >= max_pages:
            break
        txt = page.extract_text() or ''
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        joined = ' / '.join(lines[:4])[:220]
        print(f'--- p{i+1}: {len(txt)} chars | {joined}')
