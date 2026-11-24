import argparse
import asyncio
from base64 import b32decode, b32encode
from binascii import Error as EncodingError
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
import logging
import os
import re
from uuid import NAMESPACE_URL, UUID, uuid5
from xml.etree.ElementTree import ParseError

import httpx
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.error import Conflict, InvalidToken, TelegramError
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from .citizen_addresses import AddressPoint, address_query, lookup_addresses
from .config import LOCAL_TZ, messenger_crews, messenger_ids


logger = logging.getLogger("resident_bot")

NEW = "Новое обращение"
HISTORY = "Мои обращения"
CANCEL = "Отменить"
ROLE = "Моя роль"
MENU = ReplyKeyboardMarkup([[NEW, HISTORY], [ROLE]], resize_keyboard=True)
BOT_DISPATCH_PATH = "/api/v1/bot-dispatch"
TASK_STAGES = {
    "sent": "Ожидает принятия", "accepted": "Принято", "in_progress": "В работе",
    "returned": "Возвращено на доработку", "done": "Ожидает проверки",
}
TASK_ACTIONS = {"take": "accept", "work": "start", "finish": "finish", "verify": "close"}
TASK_COMMAND = re.compile(r"/(take|work|finish|verify)_([a-z2-7]{26})_([1-9][0-9]{0,9})")
ROLE_COMMAND = re.compile(r"/role_(resident|contractor|dispatcher)")
DISPATCH_CALLBACK = re.compile(
    r"/(?:jobs(?:_[0-9]{1,9})?|warnings)$|" + TASK_COMMAND.pattern + "$|" + ROLE_COMMAND.pattern + "$",
)


def dispatch_button(label, command):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=command)]])
ROLE_HELP = {
    "resident": "Житель. Отправьте обращение через «Новое обращение».",
    "contractor": "Подрядчик. Откройте /jobs, чтобы принять задание, начать работу и передать результат.",
    "dispatcher": "Диспетчер. Откройте /jobs, чтобы отметить принятие задания или подтвердить результат.",
    "administration": "Администрация. Бот пришлёт предупреждения о снегопадах. Активные предупреждения — /warnings.",
}
DESCRIPTION_LIMIT = 880
CAPTION_LIMIT = 1024
PHOTO_LIMIT = 5 * 1024 * 1024
PHOTO = "Отправьте одно фото проблемы. Описание можно добавить в подпись."
DESCRIPTION = f"Опишите проблему одним сообщением (до {DESCRIPTION_LIMIT} символов)."
ADDRESS_EXAMPLE = "Например: улица Ларина, 22."
CONFIRM_ADDRESS = "Подтвердить адрес"
LOCATION = (
    "Отправьте точку, где находится проблема. Через меню вложений Telegram "
    "можно выбрать точку на карте. Или укажите улицу и номер дома в Петропавловске-Камчатском. "
    + ADDRESS_EXAMPLE
)
WELCOME = (
    "Здесь можно сообщить о местной проблеме. Приложите фото, кратко опишите "
    "проблему и укажите точку на карте. Обращения помогают увидеть, где проблемы "
    "возникают чаще всего. Свои сообщения можно открыть кнопкой «Мои обращения». "
    "Доступ к заданиям и предупреждениям можно проверить кнопкой «Моя роль»."
)


@dataclass
class Draft:
    photo_id: str = ""
    description: str = ""
    request_id: UUID | None = None
    pending_address: AddressPoint | None = None


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
    platform = "telegram"
    user_header = "X-Telegram-User"
    location_prompt = LOCATION
    caption_limit = CAPTION_LIMIT
    photo_content_type = "image/jpeg"
    photo_filename = "photo.jpg"

    def __init__(self, client, key):
        self.client = client
        self.key = key
        self.drafts = {}
        self.task_notes = {}

    async def request(self, method, path, user_id, *, allow_missing=False, **kwargs):
        response = await self.client.request(
            method, "/api/v1/citizens/reports" + path,
            headers={"X-Citizen-Bot-Key": self.key, self.user_header: str(user_id)}, **kwargs,
        )
        if not allow_missing or response.status_code != 404:
            response.raise_for_status()
        return response

    async def dispatch_request(self, method, path, user_id, **kwargs):
        return await self.client.request(
            method, BOT_DISPATCH_PATH + path,
            headers={"X-Citizen-Bot-Key": self.key, self.user_header: str(user_id)}, **kwargs,
        )

    async def dispatch_result(self, message, response):
        if response.is_success:
            return response.json()
        if response.status_code in {403, 404, 409, 413, 415, 422}:
            detail = response.json().get("detail")
            await message.reply_text(detail if isinstance(detail, str) else "Проверьте данные и повторите действие.")
            return None
        response.raise_for_status()

    async def show_tasks(self, message, user_id, offset):
        response = await self.dispatch_request("GET", "/tasks", user_id, params={"offset": offset})
        page = await self.dispatch_result(message, response)
        if page is None:
            return
        if not page["items"]:
            await message.reply_text("На этой странице заданий нет.")
        for task in page["items"]:
            ref = f'{compact_id(task["id"])}_{task["assignment_version"]}'
            stage = task["stage"]
            action = {"sent": ("Принять", "take_"), "accepted": ("Начать работу", "work_"),
                      "returned": ("Начать доработку", "work_"),
                      "in_progress": ("Передать результат", "finish_")}.get(stage)
            if page["role"] == "contractor" and not task.get("claimed_by") and stage != "done":
                action = ("Принять", "take_")
            if stage == "done" and page["role"] == "dispatcher":
                action = ("Подтвердить выполнение", "verify_")
            kind = "Уборка снега" if task["work_kind"] == "clear" else "Проверка"
            lines = [f'{kind} · {task["address"]}', f'{task["district"]} · {TASK_STAGES[stage]}']
            if task["due_at"]:
                deadline = datetime.fromisoformat(task["due_at"]).astimezone(LOCAL_TZ)
                lines.append(f"Срок до {deadline:%d.%m.%Y %H:%M} (Камчатка)")
            for field, label in (("assignee", "Исполнитель"), ("instructions", "Задание"),
                                 ("result", "Результат"), ("return_note", "Причина возврата")):
                if task.get(field):
                    limit = 1500 if field == "result" else 700
                    lines.append(f"{label}: {task[field][:limit]}")
            keyboard = dispatch_button(action[0], f"/{action[1]}{ref}") if action else None
            text = "\n".join(lines)
            if page["role"] == "dispatcher" and task.get("has_result_photo"):
                photo = await self.dispatch_request("GET", f'/tasks/{task["id"]}/result-photo', user_id)
                if photo.is_success:
                    caption = text if len(text) <= self.caption_limit else text[:self.caption_limit - 1] + "…"
                    await message.reply_photo(photo.content, caption=caption, reply_markup=keyboard)
                    continue
            await message.reply_text(text, reply_markup=keyboard)
        if page["next_offset"] is not None:
            await message.reply_text(
                "Есть ещё задания.", reply_markup=dispatch_button("Следующая страница", f'/jobs_{page["next_offset"]}'),
            )

    async def change_task(self, message, user_id, task_id, version, action, note=""):
        response = await self.dispatch_request(
            "PUT", f"/tasks/{task_id}", user_id, json={"version": version, "action": action, "note": note},
        )
        if await self.dispatch_result(message, response) is not None:
            await message.reply_text({
                "accept": "Задание принято.", "start": "Работа начата.",
                "finish": "Результат передан диспетчеру.", "close": "Выполнение подтверждено. Задание закрыто.",
            }[action], reply_markup=dispatch_button("Задания", "/jobs"))
        return response.status_code

    async def show_role(self, message, user_id, data):
        role = data["role"]
        keyboard = {
            "contractor": dispatch_button("Задания", "/jobs"),
            "dispatcher": dispatch_button("Задания", "/jobs"),
            "administration": dispatch_button("Предупреждения", "/warnings"),
        }.get(role, MENU)
        platform = "MAX" if self.platform == "max" else "Telegram"
        text = f"Ваш ID в {platform}: {user_id}\n{ROLE_HELP[role]}"
        if assigned := data.get("assigned_role"):
            target = assigned if role == "resident" else "resident"
            labels = {"resident": "Житель", "contractor": "Подрядчик", "dispatcher": "Диспетчер"}
            text += f"\nМожно переключиться на роль «{labels[target]}»."
            rows = [list(row) for row in keyboard.inline_keyboard] if isinstance(keyboard, InlineKeyboardMarkup) else []
            rows.append([InlineKeyboardButton("Сменить роль", callback_data=f"/role_{target}")])
            keyboard = InlineKeyboardMarkup(rows)
        await message.reply_text(text, reply_markup=keyboard)

    async def dispatch_command(self, message, user_id, text, command):
        if text == ROLE or command == "/role":
            response = await self.dispatch_request("GET", "/role", user_id)
            data = await self.dispatch_result(message, response)
            if data is not None:
                await self.show_role(message, user_id, data)
        elif selection := ROLE_COMMAND.fullmatch(command):
            response = await self.dispatch_request("PUT", "/role", user_id, json={"role": selection[1]})
            data = await self.dispatch_result(message, response)
            if data is not None:
                self.task_notes.pop(user_id, None)
                await self.show_role(message, user_id, data)
        elif command == "/jobs" or re.fullmatch(r"/jobs_[0-9]{1,9}", command):
            offset = int(command[6:]) if command != "/jobs" else 0
            await self.show_tasks(message, user_id, offset)
        elif command == "/warnings":
            response = await self.dispatch_request("GET", "/warnings", user_id)
            warnings = await self.dispatch_result(message, response)
            if warnings is not None:
                for warning in warnings:
                    await message.reply_text(snow_warning_text(warning))
                if not warnings:
                    await message.reply_text("Активных предупреждений о снегопадах нет.")
        elif match := TASK_COMMAND.fullmatch(command):
            task_id = parse_id(match[2])
            version = int(match[3])
            if task_id is None or version > 2**31 - 1:
                await message.reply_text("Откройте актуальный список заданий · /jobs")
                return True
            action = TASK_ACTIONS[match[1]]
            if action in {"finish", "close"}:
                response = await self.dispatch_request("GET", "/role", user_id)
                data = await self.dispatch_result(message, response)
                required = "contractor" if action == "finish" else "dispatcher"
                if data is None or data["role"] != required:
                    if data is not None:
                        await message.reply_text("Действие недоступно для этой роли.")
                    return True
                self.task_notes[user_id] = (task_id, version, action)
                prompt = (
                    "Пришлите фото выполненной работы с описанием результата в подписи."
                    if action == "finish" else "Напишите, как проверили результат."
                )
                await message.reply_text(f"{prompt} Для отмены нажмите /cancel.")
            else:
                await self.change_task(message, user_id, task_id, version, action)
        elif user_id in self.task_notes and (text == CANCEL or command == "/cancel"):
            del self.task_notes[user_id]
            await message.reply_text("Ввод результата отменён.", reply_markup=MENU)
        elif user_id in self.task_notes and self.task_notes[user_id][2] == "finish" and (
            message.photo or (text and not command.startswith("/"))
        ):
            await self.send_task_result(message, user_id)
        elif user_id in self.task_notes and text and not command.startswith("/"):
            if len(text) > 1500:
                await message.reply_text("Сократите результат до 1500 символов.")
                return True
            status = await self.change_task(message, user_id, *self.task_notes[user_id], note=text)
            if status != 422:
                del self.task_notes[user_id]
        else:
            return False
        return True

    async def send_task_result(self, message, user_id):
        note = (message.caption or "").strip()
        if not message.photo or not note or len(note) > 1500:
            await message.reply_text("Пришлите фото с описанием результата в подписи, до 1500 символов.")
            return
        photo = await self.download_photo(message, message.photo[-1].file_id)
        if photo is None:
            await message.reply_text("Не удалось получить фото. Пришлите новое вложение до 5 МиБ.")
            return
        task_id, version, _ = self.task_notes[user_id]
        response = await self.dispatch_request(
            "POST", f"/tasks/{task_id}/result", user_id,
            data={"version": version, "note": note},
            files={"photo": (self.photo_filename, bytes(photo), self.photo_content_type)},
        )
        if await self.dispatch_result(message, response) is not None:
            await message.reply_text("Результат и фото переданы диспетчеру.", reply_markup=dispatch_button("Задания", "/jobs"))
        if response.status_code not in {413, 415, 422}:
            self.task_notes.pop(user_id, None)

    async def prompt(self, message, draft, text=None):
        if not draft.photo_id:
            instruction, keyboard = PHOTO, [[CANCEL]]
        elif not draft.description:
            instruction, keyboard = DESCRIPTION, [[CANCEL]]
        else:
            instruction = self.location_prompt
            keyboard = [[KeyboardButton("Отправить мою геопозицию", request_location=True)], [CANCEL]]
        await message.reply_text(
            text or instruction,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        )

    async def send_report(self, message, user_id, report):
        report_id = report["id"]
        description = report["description"]
        local_time = datetime.fromisoformat(report["created_at"]).astimezone(LOCAL_TZ)
        header = (
            f'Обращение №{report["report_number"]} · {local_time:%d.%m.%Y %H:%M} (Камчатка)\n'
            f'Координаты: {report["latitude"]:.6f}, {report["longitude"]:.6f}\n'
            f'Теги: {format_tags(report)}\n\n'
        )
        # Keep captions within the platform limit even for emoji.
        available = self.caption_limit - len(header.encode("utf-16-le")) // 2
        if len(description.encode("utf-16-le")) // 2 > available:
            full_text = f"…\n\nПолное описание · /d_{report['report_number']}"
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

    async def handle_task_button(self, update, _context):
        query = update.callback_query
        message = query.message
        await query.answer()
        if message is None or message.chat.type != "private" or not message.is_accessible:
            return
        try:
            await self.dispatch_command(message, query.from_user.id, query.data, query.data)
        except httpx.HTTPError as exc:
            logger.warning("Ошибка API диспетчерской (%s)", type(exc).__name__)
            await message.reply_text("Не удалось выполнить запрос. Попробуйте ещё раз.")

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
        if await self.dispatch_command(message, user_id, text, command):
            return
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
            if text == HISTORY:
                argument = ""
            elif command.startswith("/my_"):
                argument = command[4:]
            else:
                argument = argument.strip()
            cursor = parse_id(argument) if argument else None
            if argument and cursor is None:
                await message.reply_text("Откройте список кнопкой «Мои обращения».", reply_markup=MENU)
            else:
                await self.show_history(message, user_id, cursor)
            if draft:
                await self.prompt(message, draft)
        elif command in {"/report", "/description"} or command.startswith(("/r_", "/d_")):
            reference = command[3:] if command.startswith(("/r_", "/d_")) else argument.strip()
            report_id = parse_id(reference)
            if re.fullmatch(r"[1-9][0-9]{0,18}", reference) and int(reference) <= 2**63 - 1:
                report_id = reference
            if report_id is None:
                await message.reply_text("Укажите номер обращения из «Моих обращений».", reply_markup=MENU)
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

    async def download_photo(self, message, photo_id):
        file = await message.get_bot().get_file(photo_id)
        if file.file_size and file.file_size > PHOTO_LIMIT:
            return None
        return await file.download_as_bytearray()

    async def handle_draft(self, message, user_id, text, draft):
        location = message.location
        if message.photo or location:
            draft.pending_address = None
        elif draft.photo_id and draft.description and text and not text.startswith("/"):
            if text != CONFIRM_ADDRESS:
                await self.check_address(message, text, draft)
                return
            location = draft.pending_address
            text = ""
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
        elif draft.photo_id and draft.description and location:
            photo = await self.download_photo(message, draft.photo_id)
            if photo is None:
                draft.photo_id = ""
                draft.request_id = None
                await self.prompt(message, draft, "Не удалось получить фото до 5 МиБ. Пришлите новое фото.")
                return
            # Keep the UUID across retries of the same draft, even with a new location message.
            if draft.request_id is None:
                draft.request_id = uuid5(NAMESPACE_URL, f"kamdispatch:{self.platform}:{user_id}:{message.message_id}")
            try:
                data = {
                    "description": draft.description, "latitude": location.latitude,
                    "longitude": location.longitude, "request_id": str(draft.request_id),
                }
                if isinstance(location, AddressPoint):
                    data["address"] = location.address
                response = await self.request("POST", "", user_id, data=data, files={"photo": (self.photo_filename, bytes(photo), self.photo_content_type)})
            except httpx.HTTPStatusError as error:
                if error.response.status_code in {413, 415}:
                    draft.photo_id = ""
                    draft.request_id = None
                    try:
                        payload = error.response.json()
                    except ValueError:
                        payload = None
                    detail = payload.get("detail") if isinstance(payload, dict) else None
                    hint = detail if isinstance(detail, str) else "Сервис не смог принять фото."
                    await self.prompt(message, draft, f"{hint} Пришлите новое фото.")
                    return
                raise
            report = response.json()
            del self.drafts[user_id]
            await message.reply_text(
                f'Обращение №{report["report_number"]} принято. Открыть · /r_{report["report_number"]}\n'
                f'Теги: {format_tags(report)}',
                reply_markup=MENU,
            )
            return
        await self.prompt(message, draft)

    async def check_address(self, message, text, draft):
        draft.pending_address = None
        parsed = address_query(text) if len(text) <= 240 else None
        if not parsed:
            await self.prompt(message, draft, f"Укажите улицу и номер дома или отправьте геометку. {ADDRESS_EXAMPLE}")
            return
        try:
            matches = await asyncio.to_thread(lookup_addresses, *parsed)
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


async def register_commands(application):
    await application.bot.set_my_commands([
        BotCommand("start", "О боте"),
        BotCommand("new", "Новое обращение"),
        BotCommand("my", "Мои обращения"),
        BotCommand("role", "Роль и ID аккаунта"),
        BotCommand("jobs", "Задания подрядчика"),
        BotCommand("warnings", "Предупреждения о снегопадах"),
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


def snow_warning_text(warning):
    payload = warning["payload"]
    start, end = (datetime.fromisoformat(payload[field]).astimezone(LOCAL_TZ) for field in ("start", "end"))
    return (
        f'Предупреждение о снегопаде · {warning["point"]}\n'
        f'{start:%d.%m.%Y %H:%M} – {end:%d.%m.%Y %H:%M} (Камчатка)\n'
        f'Вероятность осадков: {payload["probability"]:.0%}'
    )


async def deliver_warnings(bot, telegram, user_id):
    response = await bot.dispatch_request("GET", "/warnings", user_id, params={"pending": True})
    response.raise_for_status()
    for warning in response.json():
        await telegram.send_message(chat_id=int(user_id), text=snow_warning_text(warning))
        receipt = {key: warning[key] for key in ("run_id", "point", "revision")}
        response = await bot.dispatch_request("PUT", "/warnings/receipt", user_id, json=receipt)
        response.raise_for_status()


async def notify_administration(bot, telegram, recipients):
    while True:
        for user_id in recipients:
            try:
                await deliver_warnings(bot, telegram, user_id)
            except (httpx.HTTPError, TelegramError) as exc:
                logger.warning("Не удалось доставить предупреждение (%s)", type(exc).__name__)
        await asyncio.sleep(60)


def crew_recipients(platform):
    return [actor.split(":", 1)[1] for actor in messenger_crews() if actor.startswith(platform + ":")]


async def deliver_task_notices(bot, send, user_id):
    response = await bot.dispatch_request("GET", "/task-notices", user_id)
    response.raise_for_status()
    for job in response.json():
        kind = "Уборка снега" if job["work_kind"] == "clear" else "Проверка"
        location = f'{job["district"]} · {job["address"]}'
        ref = f'{compact_id(job["id"])}_{job["assignment_version"]}'
        if job["rework"]:
            text = f"Задание возвращено на доработку · {kind}\n{location}"
            if job["return_note"]:
                text += f'\nПричина: {job["return_note"]}'
            text += "\nПосле доработки пришлите новое фото с описанием результата."
            keyboard = dispatch_button("Начать доработку", f"/work_{ref}")
        else:
            text = f"Новое задание бригаде · {kind}\n{location}"
            if job["claimed_by"] or job["stage"] not in {"sent", "accepted", "returned"}:
                text += "\nЗадание уже принято или находится в работе."
                keyboard = dispatch_button("Задания", "/jobs")
            else:
                keyboard = dispatch_button("Принять", f"/take_{ref}")
        await send(user_id, text, keyboard)
        response = await bot.dispatch_request("PUT", "/task-notices/receipt", user_id, json={
            "run_id": job["run_id"], "task_id": job["id"], "version": job["notice_version"],
        })
        response.raise_for_status()


async def notify_crew_assignments(bot, send, recipients):
    async def deliver(user_id):
        try:
            await deliver_task_notices(bot, send, user_id)
        except (httpx.HTTPError, TelegramError) as exc:
            logger.warning("Не удалось доставить задание бригаде (%s)", type(exc).__name__)

    while True:
        await asyncio.gather(*(deliver(user_id) for user_id in recipients))
        await asyncio.sleep(3)


def run_bot(token, api_url, key):
    client = httpx.AsyncClient(base_url=api_url, timeout=20)

    bot = Bot(client, key)
    notifications = []

    async def initialize(application):
        await register_commands(application)
        recipients = messenger_ids("DISPATCH_TELEGRAM_ADMINS")
        if recipients:
            notifications.append(asyncio.create_task(notify_administration(bot, application.bot, recipients)))

        async def send(user_id, text, keyboard):
            await application.bot.send_message(chat_id=int(user_id), text=text, reply_markup=keyboard)

        crew = crew_recipients("telegram")
        if crew:
            notifications.append(asyncio.create_task(notify_crew_assignments(bot, send, crew)))

    async def stop_alerts(_application):
        for job in notifications:
            job.cancel()
        for job in notifications:
            with suppress(asyncio.CancelledError):
                await job

    async def close_client(application):
        await stop_alerts(application)
        await client.aclose()

    app = (Application.builder().token(token).post_init(initialize).post_stop(stop_alerts)
           .post_shutdown(close_client).build())
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE, bot.handle))
    app.add_handler(CallbackQueryHandler(bot.handle_task_button, pattern=DISPATCH_CALLBACK))
    app.add_error_handler(handle_error)
    # run_polling needs a current event loop, including on Python 3.14.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY], close_loop=False)
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
