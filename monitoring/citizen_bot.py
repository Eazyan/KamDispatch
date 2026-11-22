import argparse
import asyncio
from base64 import b32decode, b32encode
from binascii import Error as EncodingError
from dataclasses import dataclass
import logging
import os
import re
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from telegram import BotCommand, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.error import Conflict, InvalidToken, TelegramError
from telegram.ext import Application, MessageHandler, filters


logger = logging.getLogger("resident_bot")

NEW = "Новое обращение"
HISTORY = "Мои обращения"
CANCEL = "Отменить"
MENU = ReplyKeyboardMarkup([[NEW, HISTORY]], resize_keyboard=True)
DESCRIPTION_LIMIT = 880
CAPTION_LIMIT = 1024
PHOTO = "Отправьте одно фото проблемы. Описание можно добавить в подпись."
DESCRIPTION = f"Опишите проблему одним сообщением (до {DESCRIPTION_LIMIT} символов)."
LOCATION = (
    "Отправьте точку, где находится проблема. Через меню вложений Telegram "
    "можно выбрать точку на карте. Кнопка ниже отправляет ваше текущее местоположение."
)
WELCOME = (
    "Здесь можно сообщить о местной проблеме. Приложите фото, кратко опишите "
    "проблему и укажите точку на карте. Обращения помогают увидеть, где проблемы "
    "возникают чаще всего. Свои сообщения можно открыть кнопкой «Мои обращения»."
)


@dataclass
class Draft:
    photo_id: str = ""
    description: str = ""
    request_id: UUID | None = None


def compact_id(value):
    return b32encode(UUID(value).bytes).decode("ascii").rstrip("=").lower()


def parse_id(value):
    try:
        if len(value) == 26:
            return str(UUID(bytes=b32decode(value.upper() + "=" * 6)))
        return str(UUID(value))
    except (ValueError, EncodingError):
        return None


def format_tags(report):
    tags = dict.fromkeys(report["tags"] + report["suggested_tags"])
    return " ".join(f"#{tag}" for tag in tags) if tags else "не определены"


class Bot:
    def __init__(self, client, key):
        self.client = client
        self.key = key
        self.drafts = {}

    async def request(self, method, path, user_id, *, allow_missing=False, **kwargs):
        response = await self.client.request(
            method, "/api/v1/citizens/reports" + path,
            headers={"X-Citizen-Bot-Key": self.key, "X-Telegram-User": str(user_id)}, **kwargs,
        )
        if not allow_missing or response.status_code != 404:
            response.raise_for_status()
        return response

    async def prompt(self, message, draft, text=None):
        if not draft.photo_id:
            instruction, keyboard = PHOTO, [[CANCEL]]
        elif not draft.description:
            instruction, keyboard = DESCRIPTION, [[CANCEL]]
        else:
            instruction = LOCATION
            keyboard = [[KeyboardButton("Отправить мою геопозицию", request_location=True)], [CANCEL]]
        await message.reply_text(
            text or instruction,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        )

    async def send_report(self, message, user_id, report):
        report_id = report["id"]
        description = report["description"]
        header = (
            f'Обращение №{report_id} · {report["created_at"]}\n'
            f'Координаты: {report["latitude"]:.6f}, {report["longitude"]:.6f}\n'
            f'Теги: {format_tags(report)}\n\n'
        )
        # Telegram counts UTF-16 code units in captions.
        available = CAPTION_LIMIT - len(header.encode("utf-16-le")) // 2
        if len(description.encode("utf-16-le")) // 2 > available:
            full_text = f"…\n\nПолное описание · /d_{compact_id(report_id)}"
            description = description.encode("utf-16-le")[:2 * (available - len(full_text))].decode(
                "utf-16-le", errors="ignore",
            ) + full_text
        photo = await self.request("GET", f"/{report_id}/photo", user_id, allow_missing=True)
        if photo.status_code == 404:
            await message.reply_text(header + description, reply_markup=MENU)
            return
        await message.reply_photo(photo.content, caption=header + description, reply_markup=MENU)

    async def show_history(self, message, user_id, before_id):
        params = {"limit": 6}
        if before_id:
            params["before"] = before_id
        reports = (await self.request("GET", "/mine", user_id, params=params)).json()
        if not reports:
            await message.reply_text("На этой странице обращений нет.", reply_markup=MENU)
            return
        for report in reports[:5]:
            await self.send_report(message, user_id, report)
        if len(reports) > 5:
            await message.reply_text(f'Более ранние обращения · /my_{compact_id(reports[4]["id"])}', reply_markup=MENU)

    async def handle(self, update, context):
        try:
            await self.handle_message(update, context)
        except httpx.HTTPError as error:
            logger.warning("Ошибка API обращений (%s)", type(error).__name__)
            if update.message:
                await update.message.reply_text("Не удалось выполнить запрос. Попробуйте ещё раз.")

    async def handle_message(self, update, _context):
        message = update.message
        if message is None or message.from_user is None:
            return
        user_id = message.from_user.id
        text = (message.text or "").strip()
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0] if command.startswith("/") else command
        draft = self.drafts.get(user_id)

        if command in {"/start", "/help"}:
            await message.reply_text(WELCOME, reply_markup=MENU)
            if draft:
                await self.prompt(message, draft)
        elif text == NEW or command == "/new":
            logger.debug("Открыт черновик обращения")
            await self.prompt(message, self.drafts.setdefault(user_id, Draft()))
        elif text == CANCEL or command == "/cancel":
            self.drafts.pop(user_id, None)
            if draft:
                logger.debug("Черновик обращения отменён")
            status = "Создание обращения отменено." if draft else "Нет черновика обращения."
            await message.reply_text(status, reply_markup=MENU)
        elif text == HISTORY or command == "/my" or command.startswith("/my_"):
            argument = command[4:] if command.startswith("/my_") else argument.strip()
            cursor = parse_id(argument) if argument else None
            if argument and cursor is None:
                await message.reply_text("Откройте список кнопкой «Мои обращения».", reply_markup=MENU)
            else:
                await self.show_history(message, user_id, cursor)
            if draft:
                await self.prompt(message, draft)
        elif command in {"/report", "/description"} or command.startswith(("/r_", "/d_")):
            report_id = parse_id(command[3:] if command.startswith(("/r_", "/d_")) else argument.strip())
            if report_id is None:
                await message.reply_text("Укажите UUID обращения из «Моих обращений».", reply_markup=MENU)
            else:
                response = await self.request("GET", f"/{report_id}", user_id, allow_missing=True)
                if response.status_code == 404:
                    await message.reply_text("Обращение не найдено.", reply_markup=MENU)
                elif command == "/description" or command.startswith("/d_"):
                    await message.reply_text(response.json()["description"], reply_markup=MENU)
                else:
                    await self.send_report(message, user_id, response.json())
            if draft:
                await self.prompt(message, draft)
        elif draft is not None or message.photo:
            await self.handle_draft(message, user_id, text, self.drafts.setdefault(user_id, Draft()))
        else:
            await message.reply_text(
                "Чтобы сообщить о проблеме, нажмите «Новое обращение» или отправьте фото.",
                reply_markup=MENU,
            )

    async def handle_draft(self, message, user_id, text, draft):
        if message.photo:
            draft.request_id = None
            draft.photo_id = message.photo[-1].file_id
            caption = (message.caption or "").strip()
            if caption:
                draft.description = caption if len(caption) <= DESCRIPTION_LIMIT else ""
        elif draft.photo_id and text and not text.startswith("/"):
            if len(text) > DESCRIPTION_LIMIT:
                await self.prompt(message, draft, DESCRIPTION)
                return
            draft.request_id = None
            draft.description = text
        elif draft.photo_id and draft.description and message.location:
            file = await message.get_bot().get_file(draft.photo_id)
            if file.file_size and file.file_size > 5 * 1024 * 1024:
                await self.prompt(message, draft, "Отправьте новое фото размером до 5 МиБ.")
                return
            photo = await file.download_as_bytearray()
            # Keep the UUID across retries of the same draft, even with a new location message.
            if draft.request_id is None:
                draft.request_id = uuid5(NAMESPACE_URL, f"kamdispatch:telegram:{user_id}:{message.message_id}")
            try:
                response = await self.request("POST", "", user_id, data={
                    "description": draft.description, "latitude": message.location.latitude,
                    "longitude": message.location.longitude, "request_id": str(draft.request_id),
                }, files={"photo": ("photo.jpg", bytes(photo), "image/jpeg")})
            except httpx.HTTPStatusError as error:
                if error.response.status_code in {413, 415}:
                    await self.prompt(message, draft, "Отправьте новое фото JPEG или PNG размером до 5 МиБ.")
                    return
                raise
            report = response.json()
            del self.drafts[user_id]
            await message.reply_text(
                f'Обращение №{report["id"]} принято. Открыть · /r_{compact_id(report["id"])}\n'
                f'Теги: {format_tags(report)}',
                reply_markup=MENU,
            )
            return
        await self.prompt(message, draft)


async def register_commands(application):
    await application.bot.set_my_commands([
        BotCommand("start", "О боте"),
        BotCommand("new", "Новое обращение"),
        BotCommand("my", "Мои обращения"),
        BotCommand("cancel", "Отменить создание обращения"),
    ])
    logger.info("Бот подключился к Telegram, команды зарегистрированы")


async def handle_error(update, context):
    if isinstance(context.error, (Conflict, InvalidToken)):
        logger.error("Опрос Telegram остановлен (%s). Проверьте токен, webhook и число экземпляров.",
                     type(context.error).__name__)
        context.application.stop_running()
        return
    if isinstance(update, Update):
        logger.error("Ошибка обработки обновления %d (%s)", update.update_id, type(context.error).__name__)
    else:
        logger.warning("Ошибка получения обновлений (%s)", type(context.error).__name__)


def configure_logging(level):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    # HTTP client logs can contain Telegram URLs with the token.
    handler.addFilter(logging.Filter(logger.name))
    logging.basicConfig(handlers=[handler])
    logger.setLevel(level)


def run_bot(token, api_url, key):
    client = httpx.AsyncClient(base_url=api_url, timeout=20)

    async def close_client(_application):
        await client.aclose()

    app = Application.builder().token(token).post_init(register_commands).post_shutdown(close_client).build()
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE, Bot(client, key).handle))
    app.add_error_handler(handle_error)
    # run_polling needs a current event loop, including on Python 3.14.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        app.run_polling(allowed_updates=[Update.MESSAGE], close_loop=False)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def main():
    parser = argparse.ArgumentParser(description="Бот для сбора обращений жителей.")
    parser.add_argument("--zones", action="store_true", help="показать зоны по числу обращений")
    parser.add_argument("--min-reports", type=int, default=2, help="минимум обращений в зоне")
    args = parser.parse_args()
    if args.min_reports < 1:
        parser.error("--min-reports должно быть положительным")
    token = os.getenv("BOT_TOKEN", "").strip()
    key = os.getenv("CITIZEN_BOT_API_KEY", "").strip()
    api_url = os.getenv("CITIZEN_API_URL", "http://127.0.0.1:8080")
    if not args.zones and not token:
        print("Telegram-бот отключён. Задайте BOT_TOKEN для запуска.")
        return
    if not args.zones and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        parser.exit(1, "Укажите токен Telegram в переменной BOT_TOKEN.\n")
    if not args.zones and not key:
        parser.exit(1, "Укажите CITIZEN_BOT_API_KEY.\n")
    level = os.getenv("BOT_LOG_LEVEL", "INFO").strip().upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        parser.error("BOT_LOG_LEVEL должен быть DEBUG, INFO, WARNING, ERROR или CRITICAL")
    configure_logging(level)

    try:
        if args.zones:
            headers = {}
            if staff_token := os.getenv("CITIZEN_STAFF_TOKEN", "").strip():
                headers["Authorization"] = f"Bearer {staff_token}"
            response = httpx.get(f'{api_url.rstrip("/")}/api/v1/citizens/reports/zones',
                                 params={"minimum": args.min_reports}, headers=headers, timeout=20)
            response.raise_for_status()
            zones = response.json()
            if not zones:
                print("Нет зон с указанным числом обращений.")
            for zone in zones:
                lat, lon, count = zone["latitude_cell"], zone["longitude_cell"], zone["count"]
                print(f"{lat / 100:.2f}…{(lat + 1) / 100:.2f}° широты, "
                      f"{lon / 100:.2f}…{(lon + 1) / 100:.2f}° долготы — обращений: {count}")
        else:
            run_bot(token, api_url, key)
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except InvalidToken:
        logger.error("Telegram отклонил токен")
        parser.exit(1)
    except TelegramError as error:
        logger.error("Не удалось запустить бота через Telegram (%s)", type(error).__name__)
        parser.exit(1)
    except httpx.HTTPError as error:
        logger.error("Ошибка API обращений (%s)", type(error).__name__)
        parser.exit(1)


if __name__ == "__main__":
    main()
