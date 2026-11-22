"""Print hrefs in HTML matching a regex on the URL. Usage: grep_href.py file.html url_regex"""
import re, sys

t = open(sys.argv[1], encoding='utf-8', errors='ignore').read()
pat = re.compile(sys.argv[2], re.I)
seen = set()
for m in re.finditer(r'href=["\']([^"\']+)["\']', t):
    u = m.group(1)
    if pat.search(u) and u not in seen:
        seen.add(u)
        print(u)
