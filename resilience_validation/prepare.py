"""Create only synthetic assets and credentials for the isolated test stack."""
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".resilience-validation"


def main():
    for name in ("control", "certs", "runtime"):
        (STATE / name).mkdir(parents=True, exist_ok=True)
    for name in ("weather", "traffic", "buses"):
        (STATE / "control" / name).write_text("ok")
    # Camera collection is outside this follow-up. seed_demo still loads four synthetic frames.
    (STATE / "control/cameras.json").write_text("[]")
    env = STATE / "local.env"
    if not env.exists():
        with open(env, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
            file.write(f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n")
    certs = STATE / "certs"
    if not (certs / "server.pem").exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                        "-subj", "/CN=api.openweathermap.org",
                        "-addext", "subjectAltName=DNS:api.openweathermap.org,DNS:bus.kamgov.ru",
                        "-out", str(certs / "server.pem"), "-keyout", str(certs / "server.key")],
                       capture_output=True, check=True)
        (certs / "server.key").chmod(0o644)
    subprocess.run([sys.executable, "fixtures/prepare_demo.py", "--output", str(STATE / "runtime"),
                    *[f"fixtures/sample/{i}.png" for i in range(1, 5)]], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
