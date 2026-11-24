"""Synthetic HTTP feed on 8081 and TLS replicas of the two configured sources."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
from threading import Thread
from urllib.parse import urlsplit

from monitoring.traffic_feed import graph


def mode(name):
    return (Path("/control") / name).read_text().strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, code, payload):
        encoded = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if urlsplit(self.path).path != "/ajax/request":
            self.reply(404, {})
            return
        state = mode("buses")
        if state == "down":
            self.reply(503, {})
            return
        now = datetime.now(timezone.utc)
        measured = datetime(2020, 1, 1, tzinfo=timezone.utc) if state == "frozen" else now
        self.reply(200, {"success": True, "payload": {"vehicles": [
            {"VehicleType": "bus", "VehicleUUID": "synthetic", "Latitude": 53.04, "Longitude": 158.64,
             "DeviceTime": int(measured.timestamp()), "RouteNumber": "S1"}
        ]}})

    def do_GET(self):
        path = urlsplit(self.path).path
        now = datetime.now(timezone.utc)
        if path == "/api/latest":
            state = mode("traffic")
            if state == "down":
                self.reply(503, {})
                return
            roads = graph()
            road = next(row for row in roads["segments"] if row["length_m"] > 35)
            age = timedelta(hours=2) if state in {"stale", "mixed"} else timedelta(seconds=1)
            later = now if state == "mixed" else now - age
            intervals = [
                {"status": "slow", "interval_m": [0, 10], "timestamp": (now - age).isoformat()},
                {"status": "unknown", "interval_m": [10, 30], "timestamp": None},
                {"status": "congested", "interval_m": [30, 35], "timestamp": later.isoformat()},
            ]
            self.reply(200, {"source": "yandex_maps_ui", "published_at": now.isoformat(),
                             "status": "partial" if state == "mixed" else "complete",
                             "graph_sha256": "0" * 64 if state == "wrong-graph" else roads["graph_sha256"],
                             "segments": {road["id"]: intervals}})
        elif path in ("/data/3.0/onecall", "/data/2.5/forecast"):
            state = mode("weather")
            if state in {"down", "invalid"}:
                self.reply(503 if state == "down" else 200, {})
                return
            target = now + timedelta(hours=-48 if state == "stale" else 2)
            item = {"dt": int(target.timestamp()), "weather": [{"id": 601}], "pop": 0.75}
            if state == "fallback":
                if path == "/data/3.0/onecall":
                    self.reply(403, {})
                else:
                    self.reply(200, {"list": [{**item, "main": {"temp": -6}, "snow": {"3h": 3}}]})
                return
            self.reply(200, {"hourly": [{**item, "temp": -5, "snow": {"1h": 2}}], "daily": []})
        else:
            self.reply(404, {})


if __name__ == "__main__":
    http = ThreadingHTTPServer(("0.0.0.0", 8081), Handler)
    tls = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/certs/server.pem", "/certs/server.key")
    tls.socket = context.wrap_socket(tls.socket, server_side=True)
    Thread(target=http.serve_forever, daemon=True).start()
    tls.serve_forever()
