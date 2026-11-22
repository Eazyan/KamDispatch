import subprocess, sys
url = sys.argv[1]
out = sys.argv[2]
r = subprocess.run(
    ['curl', '-sSL', '--max-time', '240',
     '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
     '-o', out, '-w', 'HTTP:%{http_code} SIZE:%{size_download}',
     url],
    capture_output=True, text=True)
print(url, '->', r.stdout.strip(), r.stderr.strip()[-200:])
