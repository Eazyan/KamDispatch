"""Dump text of given PDF pages (1-based). Usage: pdf_pages.py file.pdf 42 43 44"""
import sys
import pdfplumber

path = sys.argv[1]
pages = [int(x) for x in sys.argv[2:]]
with pdfplumber.open(path) as pdf:
    for p in pages:
        page = pdf.pages[p - 1]
        print(f'\n########## PAGE {p} ##########')
        print(page.extract_text() or '[NO TEXT]')
