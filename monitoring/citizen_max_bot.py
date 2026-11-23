import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path
import re
import ssl
from types import SimpleNamespace
from xml.etree.ElementTree import ParseError

import httpx
from telegram import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from .citizen_addresses import find_addresses, street_name
from .citizen_bot import Bot, CANCEL, DISPATCH_CALLBACK, PHOTO_LIMIT, crew_recipients, notify_crew_assignments


logger = logging.getLogger(__name__)
MAX_API_URL = "https://platform-api2.max.ru"
MAX_CA_BUNDLE = Path("/app/runtime/max/ca.pem")
ADDRESS_EXAMPLE = "Например: улица Ларина, 22."
CONFIRM_ADDRESS = "Подтвердить адрес"
HOUSE_NUMBER = r"[0-9]+(?:[а-яa-z])?(?:[-/][0-9]+[а-яa-z]?|к[0-9]+)?"
ADDRESS_PATTERN = re.compile(rf"(.+?)[,\s]+(?:д(?:ом)?\.?\s*)?({HOUSE_NUMBER})", re.IGNORECASE)


class MaxMessage:
    """Adapt a MAX dialog message to the shared resident bot workflow."""

    def __init__(self, max_client, media_client, user_id, body, callback_id=None):
        self.max_client = max_client
        self.media_client = media_client
        self.from_user = SimpleNamespace(id=user_id)
        self.message_id = body.get("mid")
        self.callback_id = callback_id
        self.caption = body.get("text") or ""
        self.photo = []
        self.location = None
        for attachment in body.get("attachments") or []:
            if attachment["type"] == "image":
                url = (attachment.get("payload") or {}).get("url")
                if url:
                    self.photo.append(SimpleNamespace(file_id=url))
            elif attachment["type"] == "location":
                self.location = SimpleNamespace(
                    latitude=attachment["latitude"], longitude=attachment["longitude"],
                )

        self.text = "" if self.photo or self.location else self.caption

    async def send(self, text, reply_markup, attachments):
        has_image = bool(attachments)
        if isinstance(reply_markup, InlineKeyboardMarkup):
            buttons = [[{"type": "callback", "text": button.text, "payload": button.callback_data}
                        for button in row] for row in reply_markup.inline_keyboard]
            attachments.append({"type": "inline_keyboard", "payload": {"buttons": buttons}})
        elif reply_markup:
            buttons = [
                [{
                    "type": "request_geo_location" if button.request_location else "message",
                    "text": "Моя геопозиция" if button.request_location else button.text,
                }]
                for row in reply_markup.keyboard for button in row
            ]
            attachments.append({"type": "inline_keyboard", "payload": {"buttons": buttons}})
        body = {"text": text, "attachments": attachments}
        if self.callback_id is not None:
            callback_id, self.callback_id = self.callback_id, None
            response = await self.max_client.post("/answers", params={"callback_id": callback_id}, json={"message": body})
            if response.is_success and response.json().get("success"):
                return
            # A callback can expire while the action is processed. Send the result as a new message.
        for delay in (0, 0.5, 1, 2):
            if delay:
                await asyncio.sleep(delay)
            response = await self.max_client.post(
                "/messages", params={"user_id": self.from_user.id},
                json=body,
            )
            if response.is_success:
                return
            if (not has_image
                or response.status_code != 400 or response.json().get("code") != "attachment.not.ready"):
                response.raise_for_status()
        response.raise_for_status()

    async def reply_text(self, text, reply_markup=None):
        await self.send(text, reply_markup, [])

    async def reply_photo(self, photo, caption, reply_markup=None):
        response = await self.max_client.post("/uploads", params={"type": "image"})
        response.raise_for_status()
        url = httpx.URL(response.json()["url"])
        if url.scheme != "https" or url.host != "iu.oneme.ru":
            raise ValueError("MAX вернул некорректный адрес загрузки")
        # The upload URL is signed by MAX. The media endpoint also expects MAX authorization.
        response = await self.media_client.post(
            url, headers={"Authorization": self.max_client.headers["Authorization"]},
            files={"data": ("report.jpg", photo, "image/jpeg")},
        )
        response.raise_for_status()
        uploaded = next(iter(response.json()["photos"].values()))
        await self.send(caption, reply_markup, [{"type": "image", "payload": {"token": uploaded["token"]}}])


class MaxBot(Bot):
    platform = "max"
    user_header = "X-Max-User"
    location_prompt = (
        f"Отправьте геометку или напишите улицу и номер дома в Петропавловске-Камчатском. {ADDRESS_EXAMPLE}"
    )
    caption_limit = 4000
    photo_content_type = "application/octet-stream"
    photo_filename = "photo"

    async def handle_draft(self, message, user_id, text, draft):
        if message.photo or message.location:
            draft.pending_address = None
        elif draft.photo_id and draft.description and text and not text.startswith("/"):
            if text == CONFIRM_ADDRESS:
                if draft.pending_address is None:
                    await self.prompt(message, draft)
                    return
                message.location = draft.pending_address
                await super().handle_draft(message, user_id, "", draft)
                return
            await self.check_address(message, text, draft)
            return
        await super().handle_draft(message, user_id, text, draft)

    async def check_address(self, message, text, draft):
        draft.pending_address = None
        match = ADDRESS_PATTERN.fullmatch(text) if len(text) <= 240 else None
        if not match or not street_name(match[1]):
            await self.prompt(message, draft, f"Укажите улицу и номер дома или отправьте геометку. {ADDRESS_EXAMPLE}")
            return
        try:
            matches = find_addresses(*match.groups())
        except (OSError, EOFError, ParseError, ValueError):
            await self.prompt(message, draft, "Справочник адресов временно недоступен. Повторите ввод или отправьте геометку.")
            return
        if not matches:
            await self.prompt(
                message, draft, "Дом по этому адресу не найден в справочнике. Проверьте улицу и номер или отправьте геометку.",
            )
        elif len(matches) > 1:
            addresses = "\n".join(point.address for point in matches[:3])
            await self.prompt(message, draft, f"Нашлось несколько адресов. Уточните улицу и номер:\n{addresses}")
        else:
            draft.pending_address = matches[0]
            await message.reply_text(
                f"Проверьте адрес: {matches[0].address}. Если он верный, подтвердите. "
                "Если нет, напишите адрес заново или отправьте геометку.",
                reply_markup=ReplyKeyboardMarkup([
                    [CONFIRM_ADDRESS], [KeyboardButton("Моя геопозиция", request_location=True)], [CANCEL],
                ], resize_keyboard=True),
            )

    async def download_photo(self, message, photo_id):
        url = httpx.URL(photo_id)
        if url.scheme != "https" or not url.host.endswith((".oneme.ru", ".max.ru", ".okcdn.ru")):
            return None
        photo = bytearray()
        # Incoming image URLs need no bot credentials.
        async with message.media_client.stream("GET", url) as response:
            response.raise_for_status()
            size = response.headers.get("Content-Length", "")
            if size.isdecimal() and int(size) > PHOTO_LIMIT:
                return None
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(photo) + len(chunk) > PHOTO_LIMIT:
                    return None
                photo.extend(chunk)
        return photo

    async def handle_update(self, update, max_client, media_client):
        callback_id = None
        if update["update_type"] == "bot_started":
            sender = update["user"]
            body = {"text": "/start"}
        elif update["update_type"] == "message_created":
            message = update["message"]
            if message["recipient"]["chat_type"] != "dialog":
                return
            sender = message.get("sender") or {}
            body = message["body"]
        elif update["update_type"] == "message_callback":
            message = update.get("message") or {}
            callback = update["callback"]
            payload = callback.get("payload")
            if (message.get("recipient", {}).get("chat_type") != "dialog"
                or not isinstance(payload, str) or not DISPATCH_CALLBACK.fullmatch(payload)):
                return
            sender = callback["user"]
            callback_id = callback["callback_id"]
            body = {"text": payload}
        else:
            return
        user_id = sender.get("user_id")
        if type(user_id) is not int or user_id <= 0 or sender.get("is_bot"):
            return
        message = MaxMessage(max_client, media_client, user_id, body, callback_id)
        await self.handle(SimpleNamespace(message=message), None)

    async def poll(self, max_client, media_client):
        params = {"timeout": 30, "types": "message_created,message_callback,bot_started"}
        while True:
            try:
                response = await max_client.get("/updates", params=params)
                response.raise_for_status()
                batch = response.json()
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500 and error.response.status_code != 429:
                    raise
                logger.warning("Ошибка опроса MAX (HTTP %d)", error.response.status_code)
                await asyncio.sleep(3)
                continue
            except httpx.RequestError as error:
                logger.warning("Ошибка опроса MAX (%s)", type(error).__name__)
                await asyncio.sleep(3)
                continue
            for update in batch["updates"]:
                try:
                    await self.handle_update(update, max_client, media_client)
                except (httpx.HTTPError, httpx.InvalidURL, KeyError, TypeError, ValueError) as error:
                    logger.warning("Ошибка обработки события MAX (%s)", type(error).__name__)
            if batch.get("marker") is not None:
                params["marker"] = batch["marker"]


async def run(token, api_url, key):
    verify = True
    if MAX_CA_BUNDLE.is_file():
        verify = ssl.create_default_context()
        verify.load_verify_locations(cafile=MAX_CA_BUNDLE)
    async with (
        httpx.AsyncClient(
            base_url=MAX_API_URL, headers={"Authorization": token}, timeout=40, verify=verify,
        ) as max_client,
        httpx.AsyncClient(timeout=20, verify=verify) as media_client,
        httpx.AsyncClient(base_url=api_url, timeout=20) as citizen_client,
    ):
        logger.info("Запущен опрос MAX")
        bot = MaxBot(citizen_client, key)

        async def send(user_id, text, keyboard):
            message = MaxMessage(max_client, media_client, int(user_id), {})
            await message.reply_text(text, reply_markup=keyboard)

        recipients = crew_recipients("max")
        notices = asyncio.create_task(notify_crew_assignments(bot, send, recipients)) if recipients else None
        try:
            await bot.poll(max_client, media_client)
        finally:
            if notices is not None:
                notices.cancel()
                with suppress(asyncio.CancelledError):
                    await notices


def main():
    token = os.getenv("MAX_BOT_TOKEN", "").strip()
    if not token:
        print("Бот MAX отключён. Задайте MAX_BOT_TOKEN для запуска.")
        return
    key = os.getenv("CITIZEN_BOT_API_KEY", "").strip()
    if not key:
        raise SystemExit("Укажите CITIZEN_BOT_API_KEY.")
    handler = logging.StreamHandler()
    handler.addFilter(lambda record: record.name in {__name__, "resident_bot"})
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    try:
        asyncio.run(run(token, os.getenv("CITIZEN_API_URL", "http://127.0.0.1:8080"), key))
    except KeyboardInterrupt:
        logger.info("Бот MAX остановлен")
    except ssl.SSLError:
        logger.error("Не удалось загрузить сертификаты MAX из %s", MAX_CA_BUNDLE)
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as error:
        logger.error("Опрос MAX остановлен (HTTP %d). Проверьте токен и способ получения событий.",
                     error.response.status_code)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
