import urllib.request, sys
url = 'https://pkgo.ru/upload/medialibrary/d1a/d1a47f280e3871b728a1a70a7508f5b5.pdf'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    open('/home/claude/mapdata/data/ksodd.pdf', 'wb').write(data)
    print('OK', len(data), 'bytes, head:', data[:8])
except Exception as e:
    print('FAIL', e)
