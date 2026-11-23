"""Finite single-slot Linux capture pilot. No scheduler or network listeners."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import importlib.metadata
import shutil
import signal
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_traffic import capture, write_json, digest, ProviderBlocked

@contextmanager
def linux_page(_executable=None, viewport=(1440, 1000)):
    from playwright.sync_api import sync_playwright
    if os.geteuid() == 0:
        raise RuntimeError("Browser worker must run non-root")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                          device_scale_factor=1, locale="ru-RU")
            page = context.new_page()
            page.set_default_timeout(20000)
            try:
                yield page
            except BaseException:
                try:
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                    page.screenshot(path="/spool/failure-" + stamp + ".png", timeout=5000)
                except Exception:
                    pass
                raise
        finally:
            browser.close()

def validate_plan(plan):
    tiles = plan["tiles"]
    if not 1 <= len(tiles) <= 3:
        raise ValueError("Pilot requires 1–3 tiles")
    ids = set()
    for tile in tiles:
        name = tile["id"]
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in name) or name in ids:
            raise ValueError("Invalid/duplicate tile id")
        ids.add(name)
        from monitoring.traffic_image import mercator
        mercator(tile["center"])
    if not 14 <= plan["zoom"] <= 18 or plan["viewport"] != [4096, 4096]:
        raise ValueError("Pilot uses zoom14–18 and fixed 4096 viewport")
    return tiles

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["smoke", "capture"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    def cancel(*_):
        raise KeyboardInterrupt("cancelled")
    signal.signal(signal.SIGTERM, cancel)
    if args.command == "smoke":
        args.output.mkdir(parents=True, exist_ok=False)
        with linux_page() as page:
            page.set_content("<html><body><h1>Linux browser ready</h1></body></html>")
            page.screenshot(path=str(args.output / "browser.png"))
            print(json.dumps({"status":"ok", "uid":os.geteuid(), "browser":page.context.browser.version}), flush=True)
        return
    plan = json.loads(args.plan.read_text())
    tiles = validate_plan(plan)
    if shutil.disk_usage(args.output.parent).free < 5 * 1024**3:
        raise RuntimeError("Spool requires 5 GiB free reserve")
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    manifest = {"status":"running", "started_at":datetime.now(timezone.utc).isoformat(),
                "plan_sha256":digest(args.plan), "code_sha256":digest(Path(__file__)), "tiles":[],
                "validation":"experimental_unvalidated", "playwright_version":importlib.metadata.version("playwright"),
                "capture_code_sha256":digest(Path(__file__).with_name("capture_traffic.py")),
                "started_disk_free":shutil.disk_usage(args.output.parent).free}
    write_json(args.output / "run.json", manifest)
    try:
        for tile in tiles:
            tick = time.monotonic()
            print("START", tile["id"], flush=True)
            capture(args.output / tile["id"], None, tile["center"], plan["zoom"], tuple(plan["viewport"]), page_factory=linux_page)
            manifest["tiles"].append({"id":tile["id"], "duration_s":time.monotonic()-tick})
            write_json(args.output / "run.json", manifest)
        manifest["status"] = "captured_pending_review"
    except BaseException as exc:
        manifest.update(status="blocked_provider" if isinstance(exc, ProviderBlocked) else "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed", error=str(exc))
        raise
    finally:
        manifest.update(duration_s=time.monotonic()-started, finished_at=datetime.now(timezone.utc).isoformat(),
                        child_peak_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                        bytes=sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file()))
        write_json(args.output / "run.json", manifest)
        print(json.dumps(manifest), flush=True)

if __name__ == "__main__":
    main()
