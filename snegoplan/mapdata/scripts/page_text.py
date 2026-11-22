"""Extract main-content text from an HTML page: drop nav, print unique text lines.
Usage: page_text.py file.html
"""
import re, html, sys

data = open(sys.argv[1], 'rb').read()
t = None
for enc in ('utf-8', 'cp1251'):
    try:
        t = data.decode(enc)
        break
    except UnicodeDecodeError:
        continue
t = re.sub(r'<script.*?</script>', ' ', t, flags=re.S | re.I)
t = re.sub(r'<style.*?</style>', ' ', t, flags=re.S | re.I)
t = re.sub(r'<!--.*?-->', ' ', t, flags=re.S)
p = html.unescape(re.sub(r'<[^>]+>', '\n', t))
seen, out = set(), []
for line in p.splitlines():
    line = re.sub(r'\s+', ' ', line).strip()
    if line and line not in seen:
        seen.add(line)
        out.append(line)
print('\n'.join(out))
