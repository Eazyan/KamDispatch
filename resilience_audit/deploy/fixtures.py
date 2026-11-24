"""Local weather, traffic and camera fixtures with explicit failure switches."""
import io
import json
import ssl
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from PIL import Image, ImageDraw

sys.path.insert(0, "/app")
from monitoring.traffic_feed import graph


def mode(name):
    return (Path("/control") / name).read_text().strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def send(self, status, body, content_type="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        now = datetime.now(timezone.utc)
        if path == "/api/latest":
            state = mode("traffic")
            if state == "down":
                self.send(503, {"error": "synthetic upstream outage"})
                return
            roads = graph()
            road = next(row for row in roads["segments"] if row["length_m"] > 30)
            timestamp = now - timedelta(hours=2) if state == "stale" else now
            self.send(200, {
                "source": "yandex_maps_ui", "status": "complete",
                "graph_sha256": "0" * 64 if state == "wrong-graph" else roads["graph_sha256"],
                "published_at": timestamp.isoformat(),
                "segments": {road["id"]: [{"status": "slow", "interval_m": [0, 25],
                                           "timestamp": timestamp.isoformat()}]},
            })
        elif path.startswith("/camera/"):
            if mode("cameras") == "down":
                self.send(503, {"error": "synthetic camera outage"})
                return
            image = Image.new("RGB", (320, 180), (36, 57, 69))
            ImageDraw.Draw(image).text((12, 65), f"SYNTHETIC CAMERA\n{path[8:]}\n{now:%H:%M:%S UTC}", fill="white")
            output = io.BytesIO()
            image.save(output, format="JPEG")
            self.send(200, output.getvalue(), "image/jpeg")
        elif path in ("/data/3.0/onecall", "/data/2.5/forecast"):
            state = mode("weather")
            if state == "down" or state == "invalid":
                self.send(503 if state == "down" else 200, {"error": "synthetic response"})
                return
            item = {"dt": int((now + timedelta(hours=2)).timestamp()), "weather": [{"id": 601}], "pop": 0.8}
            if state == "fallback":
                if path == "/data/3.0/onecall":
                    self.send(403, {"error": "synthetic subscription restriction"})
                else:
                    self.send(200, {"list": [{**item, "main": {"temp": -6}, "snow": {"3h": 3}}]})
                return
            self.send(200, {"hourly": [{**item, "temp": -5, "snow": {"1h": 2}}], "daily": []})
        else:
            self.send(404, {"error": "unknown fixture route"})


http = ThreadingHTTPServer(("0.0.0.0", 8081), Handler)
https = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
tls.load_cert_chain("/certs/server.pem", "/certs/server.key")
https.socket = tls.wrap_socket(https.socket, server_side=True)
Thread(target=http.serve_forever, daemon=True).start()
https.serve_forever()
