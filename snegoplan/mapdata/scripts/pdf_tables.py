"""Try pdfplumber table extraction on given pages. Usage: pdf_tables.py file.pdf 35 43 65 73"""
import sys
import pdfplumber

path = sys.argv[1]
pages = [int(x) for x in sys.argv[2:]]
with pdfplumber.open(path) as pdf:
    for p in pages:
        page = pdf.pages[p - 1]
        tables = page.extract_tables()
        print(f'### page {p}: {len(tables)} tables')
        for ti, tbl in enumerate(tables):
            print(f'  -- table {ti}: {len(tbl)} rows')
            for row in tbl[:6]:
                cells = [(' '.join((c or '').split()))[:40] for c in row]
                print('   |', ' | '.join(cells))
