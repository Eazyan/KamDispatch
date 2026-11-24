"""Serve the worker's public read-only JSON over a separate HTTP listener."""

import argparse
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore

PUBLIC_PATHS = frozenset(("/health", "/api/graph", "/api/latest"))
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class FeedServer(ThreadingHTTPServer):
    def __init__(self, address, worker_port):
        self.worker_port = worker_port
        self.slots = BoundedSemaphore(8)
        super().__init__(address, FeedHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class FeedHandler(BaseHTTPRequestHandler):
    timeout = 5

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path not in PUBLIC_PATHS:
            self.send_error(404)
            return

        worker = HTTPConnection("127.0.0.1", self.server.worker_port, timeout=5)
        try:
            worker.request("GET", self.path)
            response = worker.getresponse()
            if response.status != 200:
                self.send_error(502)
                return
            payload = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, HTTPException):
            self.send_error(502)
            return
        finally:
            worker.close()

        if len(payload) > MAX_RESPONSE_BYTES:
            self.send_error(502)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True, help="IPv4 address of the Windows LAN interface")
    parser.add_argument("--port", default=8766, type=int)
    parser.add_argument("--worker-port", default=8765, type=int)
    args = parser.parse_args()
    with FeedServer((args.bind, args.port), args.worker_port) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
