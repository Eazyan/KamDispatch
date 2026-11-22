"""Extract links (href + anchor text) from HTML files.
Usage: extract_links.py file.html [keyword_regex]
"""
import re, html, sys

kw = re.compile(sys.argv[2], re.I) if len(sys.argv) > 2 else None

for path in sys.argv[1:2] if kw else sys.argv[1:]:
    data = open(path, 'rb').read()
    t = None
    for enc in ('utf-8', 'cp1251'):
        try:
            t = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if t is None:
        t = data.decode('utf-8', errors='ignore')
    t = re.sub(r'<script.*?</script>', ' ', t, flags=re.S | re.I)
    t = re.sub(r'<style.*?</style>', ' ', t, flags=re.S | re.I)
    print('===', path)
    for m in re.finditer(r'href=["\']([^"\']+)["\'][^>]*>(.{0,200}?)</a>', t, flags=re.S | re.I):
        u, txt = m.group(1), re.sub(r'<[^>]+>', ' ', m.group(2))
        txt = html.unescape(re.sub(r'\s+', ' ', txt)).strip()
        if txt and (kw is None or kw.search(txt) or kw.search(u)):
            print(u, '|', txt[:150])
