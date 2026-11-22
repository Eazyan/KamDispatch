"""Operational, read-only import of the previous resident service's SQLite snapshot."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO
import logging
import os
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, uuid5

import httpx

from .citizens import photo_jpeg, save_report
from .config import Settings
from .db import connect, migrate
from .districts import load_districts


LEGACY_TELEGRAM_BYTES = 20 * 1024 * 1024
LEGACY_WEB_BYTES = 8 * 1024 * 1024
LEGACY_PHOTO_PIXELS = 64_000_000


def telegram_photo(client, token, file_id):
    response = client.post(f"https://api.telegram.org/bot{token}/getFile", data={"file_id": file_id})
    response.raise_for_status()
    file_path = response.json()["result"]["file_path"]
    with client.stream("GET", f"https://api.telegram.org/file/bot{token}/{file_path}") as response:
        response.raise_for_status()
        contents = BytesIO()
        for chunk in response.iter_bytes():
            contents.write(chunk)
            if contents.tell() > LEGACY_TELEGRAM_BYTES:
                raise ValueError("Oversize photo")
        contents.seek(0)
        return photo_jpeg(contents, max_bytes=LEGACY_TELEGRAM_BYTES, max_pixels=LEGACY_PHOTO_PIXELS)


def import_reports(settings, source, token):
    districts = load_districts(settings.citizen_districts_path)
    migrate(settings)
    imported = skipped = 0
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old:
        old.row_factory = sqlite3.Row
        tables = {row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with httpx.Client(timeout=20) as client, connect(settings) as conn:
            for report in old.execute("SELECT * FROM reports ORDER BY id"):
                user_id = report["user_id"]
                if user_id:
                    actor = f"telegram:{user_id}"
                    key = f"kamdispatch:telegram:{user_id}:{report['message_id']}"
                else:
                    actor = "legacy-web" if settings.mode == "live" else "local-demo"
                    key = f"kamdispatch:legacy-web:{report['message_id']}"
                request_id = uuid5(NAMESPACE_URL, key)
                if conn.execute(
                    "SELECT 1 FROM citizen_reports WHERE mode=%s AND submitted_by=%s AND request_id=%s",
                    (settings.mode, actor, request_id),
                ).fetchone():
                    skipped += 1
                    continue
                try:
                    address = ""
                    if user_id:
                        if not token:
                            raise ValueError("Missing BOT_TOKEN")
                        image = telegram_photo(client, token, report["photo_id"])
                    else:
                        photo = old.execute("SELECT content FROM web_photos WHERE report_id=?",
                                            (report["id"],)).fetchone() if "web_photos" in tables else None
                        if photo is None:
                            raise ValueError("Missing web photo")
                        image = photo_jpeg(BytesIO(photo["content"]), max_bytes=LEGACY_WEB_BYTES,
                                           max_pixels=LEGACY_PHOTO_PIXELS)
                        if "web_addresses" in tables:
                            row = old.execute("SELECT address FROM web_addresses WHERE report_id=?",
                                              (report["id"],)).fetchone()
                            address = row["address"] if row else ""
                    save_report(
                        conn, mode=settings.mode, actor=actor, districts=districts, image=image,
                        description=report["description"], latitude=report["latitude"],
                        longitude=report["longitude"], address=address, request_id=request_id,
                        created_at=datetime.fromisoformat(report["created_at"]).replace(tzinfo=timezone.utc),
                    )
                except Exception as error:
                    # Telegram exception text can contain the bot token and PostgreSQL may include row data.
                    raise RuntimeError(f"Запись SQLite №{report['id']} ({type(error).__name__})") from None
                imported += 1
    return imported, skipped


def main():
    parser = argparse.ArgumentParser(description="Перенести снимок SQLite в PostgreSQL KamDispatch.")
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings()
    try:
        imported, skipped = import_reports(settings, args.source, os.getenv("BOT_TOKEN", "").strip())
    except RuntimeError as error:
        parser.exit(1, f"Импорт прерван, записи из этого запуска отменены. {error}\n")
    except Exception as error:
        parser.exit(1, f"Импорт прерван ({type(error).__name__}). Проверьте путь и подключение к базе.\n")
    print(f"Импортировано: {imported}. Уже в PostgreSQL: {skipped}.")


if __name__ == "__main__":
    main()
