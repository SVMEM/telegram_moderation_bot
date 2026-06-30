from __future__ import annotations

import html
import logging
import re
from typing import Iterable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from .billing import CryptoPayClient, CryptoPayError, add_days, money_ok
from .config import Config
from .database import Database
from .keyboards import main_menu
from .ml import SpamModel
from .rules import ACTION_SEVERITY, evaluate_message
from .text import short_text

logger = logging.getLogger(__name__)
router = Router()

DB: Database | None = None
CONFIG: Config | None = None
BILLING: CryptoPayClient | None = None
SPAM_MODEL: SpamModel | None = None

RULE_TYPES = {"exact", "contains", "phrase", "mask", "regex"}
MODES = {"test", "active", "disabled"}
TELEGRAM_MESSAGE_LIMIT = 3900


def setup(
    database: Database,
    config: Config,
    billing_client: CryptoPayClient | None = None,
    spam_model: SpamModel | None = None,
) -> None:
    global DB, CONFIG, BILLING, SPAM_MODEL
    DB = database
    CONFIG = config
    BILLING = billing_client
    SPAM_MODEL = spam_model


def db() -> Database:
    if DB is None:
        raise RuntimeError("Database is not configured")
    return DB


def config() -> Config:
    if CONFIG is None:
        raise RuntimeError("Config is not configured")
    return CONFIG


def billing() -> CryptoPayClient:
    if BILLING is None:
        raise RuntimeError("Billing is not configured")
    return BILLING


def spam_model() -> SpamModel | None:
    return SPAM_MODEL


def user_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


def user_name(message: Message) -> str | None:
    if not message.from_user:
        return None
    return message.from_user.username


def full_name(message: Message) -> str | None:
    return message.from_user.full_name if message.from_user else None


def command_args(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def h(value: object) -> str:
    return html.escape(str(value))


async def remember_user(message: Message) -> None:
    uid = user_id(message)
    if uid is not None:
        db().ensure_user(uid, full_name(message), user_name(message))


async def ensure_access(message: Message, chat_id: int | None = None, owner_only: bool = False) -> bool:
    await remember_user(message)
    uid = user_id(message)
    if owner_only:
        allowed = db().is_owner(uid)
    else:
        allowed = db().can_manage_chat(uid, chat_id)
    if not allowed:
        await message.answer("Нет доступа.")
        return False
    if not subscription_ok_for_user(uid):
        await message.answer("Подписка не активна. Команда: /subscribe")
        return False
    return True


def subscription_ok_for_user(uid: int | None) -> bool:
    if not config().subscription_required:
        return True
    if uid is None:
        return False
    if config().subscription_bypass_owner and db().is_owner(uid):
        return True
    return db().subscription_active(uid)


def subscription_ok_for_chat(chat) -> bool:
    if not config().subscription_required:
        return True
    owner_id = chat["owner_id"]
    if owner_id is not None and config().subscription_bypass_owner and db().is_owner(owner_id):
        return True
    return db().subscription_active(owner_id)


async def is_sender_chat_admin(message: Message, bot: Bot) -> bool:
    uid = user_id(message)
    if uid is None:
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, uid)
    except Exception:
        return False
    return member.status in {"creator", "administrator"}


def format_chat(row) -> str:
    return (
        f"<b>{h(row['title'])}</b>\n"
        f"ID: <code>{row['chat_id']}</code>\n"
        f"Тип: {h(row['type'])}; режим: <b>{h(row['mode'])}</b>; статус: {h(row['status'])}\n"
        f"Удаление: {'есть' if row['can_delete_messages'] else 'нет'}"
    )


def format_rule(rule) -> str:
    return (
        f"#{rule.id} [{h(rule.rule_type)}] <code>{h(rule.pattern)}</code> "
        f"action={h(rule.action)} priority={rule.priority} status={h(rule.status)}"
    )


def help_text() -> str:
    return (
        "<b>Команды</b>\n"
        "/connect &lt;chat_id|@username&gt; — подключить чат. В группе можно /connect без аргумента.\n"
        "/channels — список чатов.\n"
        "/addword &lt;chat_id&gt; &lt;exact|contains|phrase|mask|regex&gt; &lt;слово&gt; — правило.\n"
        "/delword &lt;rule_id&gt; — удалить правило.\n"
        "/rules [chat_id] — список правил.\n"
        "/whitelist add_expr &lt;chat_id&gt; &lt;выражение&gt; — разрешённое выражение.\n"
        "/whitelist add_user &lt;chat_id&gt; &lt;user_id&gt; — разрешённый пользователь.\n"
        "/whitelist del &lt;id&gt; — удалить исключение.\n"
        "/mode &lt;chat_id&gt; &lt;test|active|disabled&gt; — режим.\n"
        "/test &lt;chat_id&gt; &lt;текст&gt; — проверка без удаления.\n"
        "/logs [chat_id] [limit] — журнал.\n"
        "/stats [chat_id] — статистика.\n"
        "/subscription — статус подписки.\n"
        "/subscribe — оплатить месяц криптой.\n"
        "/check_payment [invoice_id] — проверить оплату.\n"
        "/ml <chat_id> <on|off> [threshold] — ML-антиспам.\n"
        "/grant_admin &lt;user_id&gt; [chat_id] — выдать доступ владельцем.\n"
        "/status — состояние."
    )


def external_guide_text() -> str:
    return (
        "<b>Полный гайд подключения</b>\n\n"
        "<b>1. Оплата</b>\n"
        "Если бот просит подписку, открой личку с ботом и отправь:\n"
        "<code>/subscribe</code>\n"
        "Оплати счёт Crypto Pay, затем проверь оплату:\n"
        "<code>/check_payment</code>\n"
        "Статус подписки:\n"
        "<code>/subscription</code>\n\n"
        "<b>2. Добавление в группу</b>\n"
        "Добавь бота в группу или супергруппу. В настройках группы выдай боту права администратора:\n"
        "удаление сообщений, просмотр сообщений, управление сообщениями.\n"
        "После этого напиши в группе от своего личного аккаунта:\n"
        "<code>/connect</code>\n"
        "Если ты пишешь от имени канала, Telegram не передаёт нормальный user_id. Переключись на личный аккаунт и повтори.\n\n"
        "<b>3. Добавление в канал</b>\n"
        "Добавь бота администратором канала с правом удаления сообщений.\n"
        "Если канал публичный, в личке с ботом отправь:\n"
        "<code>/connect @channel_username</code>\n"
        "Для комментариев к каналу отдельно добавь бота в привязанную группу обсуждений и там отправь:\n"
        "<code>/connect</code>\n\n"
        "<b>4. Узнать chat_id</b>\n"
        "После подключения бот покажет ID. Посмотреть ещё раз:\n"
        "<code>/channels</code>\n\n"
        "<b>5. Настроить правила</b>\n"
        "Примеры:\n"
        "<code>/addword &lt;chat_id&gt; exact казино</code>\n"
        "<code>/addword &lt;chat_id&gt; contains крипто</code>\n"
        "<code>/addword &lt;chat_id&gt; phrase быстрый заработок</code>\n"
        "<code>/addword &lt;chat_id&gt; mask ставк*</code>\n"
        "Whitelist:\n"
        "<code>/whitelist add_expr &lt;chat_id&gt; ключевая ставка</code>\n"
        "<code>/whitelist add_user &lt;chat_id&gt; 123456789</code>\n\n"
        "<b>6. Включить ML-антиспам</b>\n"
        "<code>/ml &lt;chat_id&gt; on 0.55</code>\n"
        "Проверить текст без удаления:\n"
        "<code>/test &lt;chat_id&gt; казино бонус перейди по ссылке</code>\n\n"
        "<b>7. Режимы</b>\n"
        "Безопасный тестовый режим, ничего не удаляет:\n"
        "<code>/mode &lt;chat_id&gt; test</code>\n"
        "Боевой режим, удаляет сообщения:\n"
        "<code>/mode &lt;chat_id&gt; active</code>\n"
        "Полностью выключить проверку:\n"
        "<code>/mode &lt;chat_id&gt; disabled</code>\n\n"
        "<b>8. Проверка</b>\n"
        "<code>/status</code>\n"
        "<code>/rules &lt;chat_id&gt;</code>\n"
        "<code>/logs &lt;chat_id&gt;</code>\n"
        "<code>/stats &lt;chat_id&gt;</code>\n\n"
        "<b>Если бот не удаляет</b>\n"
        "Проверь: бот администратор, есть право удаления, чат подключён, режим active, правило или ML включены, подписка владельца чата активна."
    )


async def answer_long(message: Message, text: str) -> None:
    chunk = ""
    for line in text.splitlines(keepends=True):
        if len(chunk) + len(line) > TELEGRAM_MESSAGE_LIMIT:
            await message.answer(chunk)
            chunk = ""
        chunk += line
    if chunk:
        await message.answer(chunk)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await remember_user(message)
    await message.answer("Меню модерации.", reply_markup=main_menu())


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await answer_long(message, external_guide_text())


@router.callback_query(F.data.startswith("menu:"))
async def menu_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    db().ensure_user(callback.from_user.id, callback.from_user.full_name, callback.from_user.username)
    section = callback.data.split(":", 1)[1] if callback.data else ""
    uid = callback.from_user.id

    if section == "help":
        await answer_long(callback.message, external_guide_text())
        await callback.answer()
        return
    elif section == "channels":
        chats = db().list_chats(uid)
        text = "Чаты не подключены." if not chats else "\n\n".join(format_chat(row) for row in chats[:10])
    elif section == "rules":
        chats = db().list_chats(uid)
        if not chats:
            text = "Нет доступных чатов."
        else:
            lines: list[str] = []
            for chat in chats[:5]:
                rules = db().list_rules(chat["chat_id"])
                lines.append(f"<b>{h(chat['title'])}</b>: {len(rules)} правил")
            text = "\n".join(lines)
    elif section == "whitelist":
        text = "Whitelist: /whitelist add_expr, /whitelist add_user, /whitelist del."
    elif section == "logs":
        events = db().list_events(None, 10)
        text = format_events(events)
    elif section == "stats":
        text = format_stats(db().stats())
    elif section == "status":
        text = build_status()
    else:
        text = "Неизвестный раздел."

    await callback.message.answer(text, reply_markup=main_menu())
    await callback.answer()


@router.message(Command("connect"))
async def connect(message: Message, bot: Bot) -> None:
    await remember_user(message)
    args = command_args(message)
    target = args if args else message.chat.id

    if message.chat.type == "private":
        if not await ensure_access(message):
            return
        if not args:
            await message.answer("Укажите chat_id или @username: /connect -100123...")
            return
    else:
        allowed = db().can_manage_chat(user_id(message)) or await is_sender_chat_admin(message, bot)
        if not allowed:
            await message.answer("Подключать чат может владелец бота или администратор этого чата.")
            return

    try:
        chat = await bot.get_chat(target)
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        await message.answer(f"Не удалось проверить чат: <code>{h(exc)}</code>")
        return

    status = getattr(member, "status", "")
    is_admin = status in {"creator", "administrator"}
    can_delete = bool(is_admin and (status == "creator" or getattr(member, "can_delete_messages", False)))
    saved_status = "connected" if can_delete else "missing_rights"
    title = chat.title or chat.full_name or str(chat.id)

    db().upsert_chat(
        chat_id=chat.id,
        title=title,
        chat_type=chat.type,
        owner_id=user_id(message),
        can_delete_messages=can_delete,
        status=saved_status,
    )
    if user_id(message) is not None:
        db().grant_chat_access(user_id(message), chat.id, "admin")

    if can_delete:
        await message.answer(
            f"Чат подключён.\n{format_chat(db().get_chat(chat.id))}\n"
            f"Текущий режим: <b>{h(db().get_chat(chat.id)['mode'])}</b>."
        )
    else:
        await message.answer(
            "Чат сохранён, но удаление не включено: бот не администратор или нет права удаления сообщений. "
            "Выдайте право удаления и повторите /connect."
        )


@router.message(Command("channels"))
async def channels(message: Message) -> None:
    if not await ensure_access(message):
        return
    chats = db().list_chats(user_id(message))
    text = "Чаты не подключены." if not chats else "\n\n".join(format_chat(row) for row in chats)
    await message.answer(text)


@router.message(Command("subscription"))
async def subscription(message: Message) -> None:
    await remember_user(message)
    uid = user_id(message)
    row = db().get_subscription(uid)
    active = db().subscription_active(uid)
    latest = db().get_latest_payment(uid) if uid is not None else None
    valid_until = row["valid_until"] if row else "нет"
    lines = [
        "<b>Подписка</b>",
        f"Статус: <b>{'active' if active else 'inactive'}</b>",
        f"До: <code>{h(valid_until)}</code>",
        f"Тариф: {h(config().subscription_price)} {h(config().subscription_fiat)} / {config().subscription_days} дней",
    ]
    if latest:
        lines.append(f"Последний invoice: <code>{latest['provider_invoice_id']}</code>, статус: {h(latest['status'])}")
    await message.answer("\n".join(lines))


@router.message(Command("subscribe"))
async def subscribe(message: Message) -> None:
    await remember_user(message)
    uid = user_id(message)
    if uid is None:
        await message.answer("Не удалось определить Telegram user_id.")
        return
    if not billing().configured:
        await message.answer("Оплата не настроена: нужен CRYPTO_PAY_API_TOKEN.")
        return
    try:
        amount = money_ok(config().subscription_price)
        invoice = await billing().create_invoice(
            user_id=uid,
            amount=amount,
            fiat=config().subscription_fiat,
            accepted_assets=config().subscription_accepted_assets,
            expires_in=config().subscription_invoice_expires_seconds,
        )
        db().create_payment(
            user_id=uid,
            provider_invoice_id=invoice.invoice_id,
            amount=amount,
            fiat=config().subscription_fiat,
            invoice_url=invoice.url,
        )
    except (CryptoPayError, ValueError) as exc:
        await message.answer(f"Не удалось создать счёт: <code>{h(exc)}</code>")
        return
    await message.answer(
        f"Счёт на {h(amount)} {h(config().subscription_fiat)} создан.\n"
        f"Оплатить: {h(invoice.url)}\n"
        f"После оплаты: /check_payment {invoice.invoice_id}"
    )


@router.message(Command("check_payment"))
async def check_payment(message: Message) -> None:
    await remember_user(message)
    uid = user_id(message)
    if uid is None:
        await message.answer("Не удалось определить Telegram user_id.")
        return
    if not billing().configured:
        await message.answer("Оплата не настроена: нужен CRYPTO_PAY_API_TOKEN.")
        return
    args = command_args(message).split()
    if args:
        try:
            invoice_id = int(args[0])
        except ValueError:
            await message.answer("invoice_id должен быть числом.")
            return
        payment = db().get_payment_by_invoice(invoice_id)
    else:
        payment = db().get_latest_payment(uid)
    if not payment or payment["user_id"] != uid:
        await message.answer("Счёт не найден.")
        return
    try:
        invoice = await billing().get_invoice(payment["provider_invoice_id"])
    except CryptoPayError as exc:
        await message.answer(f"Не удалось проверить счёт: <code>{h(exc)}</code>")
        return
    if not invoice:
        await message.answer("Счёт не найден у провайдера.")
        return
    status = str(invoice.get("status"))
    db().update_payment_status(payment_id=payment["id"], status=status, raw_payload=invoice)
    if status == "paid":
        valid_until = add_days(db().get_subscription_until(uid), config().subscription_days)
        db().activate_subscription(user_id=uid, valid_until=valid_until, payment_id=payment["id"])
        await message.answer(f"Оплата подтверждена. Подписка активна до <code>{h(valid_until)}</code>.")
        return
    await message.answer(f"Статус счёта: <b>{h(status)}</b>.")


@router.message(Command("addword"))
async def addword(message: Message) -> None:
    args = command_args(message)
    parts = args.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /addword <chat_id> <exact|contains|phrase|mask|regex> <слово или выражение>")
        return

    try:
        chat_id = int(parts[0])
    except ValueError:
        await message.answer("chat_id должен быть числом.")
        return

    rule_type = parts[1].lower()
    pattern = parts[2].strip()
    if rule_type not in RULE_TYPES:
        await message.answer("Тип правила: exact, contains, phrase, mask, regex.")
        return
    if not pattern:
        await message.answer("Пустое правило не добавлено.")
        return
    if rule_type == "regex" and db().get_global_role(user_id(message)) not in {"owner", "admin"}:
        await message.answer("Regex доступен только владельцу или глобальному администратору.")
        return
    if rule_type == "regex":
        try:
            re.compile(pattern)
        except re.error as exc:
            await message.answer(f"Некорректное регулярное выражение: <code>{h(exc)}</code>")
            return
    if not await ensure_access(message, chat_id):
        return
    if db().get_chat(chat_id) is None:
        await message.answer("Чат не подключён. Сначала /connect.")
        return

    rule_id = db().add_rule(
        chat_id=chat_id,
        name=pattern,
        rule_type=rule_type,
        pattern=pattern,
        action="delete",
        priority=100,
        created_by=user_id(message),
    )
    await message.answer(f"Правило добавлено: #{rule_id}, тип {h(rule_type)}, шаблон <code>{h(pattern)}</code>.")


@router.message(Command("delword"))
async def delword(message: Message) -> None:
    args = command_args(message)
    if not args.isdigit():
        await message.answer("Формат: /delword <rule_id>")
        return
    rule_id = int(args)
    rule = db().get_rule(rule_id)
    if not rule:
        await message.answer("Правило не найдено.")
        return
    if not await ensure_access(message, rule["chat_id"]):
        return
    db().delete_rule(rule_id, user_id(message))
    await message.answer(f"Правило #{rule_id} удалено.")


@router.message(Command("rules"))
async def rules(message: Message) -> None:
    args = command_args(message)
    chat_id: int | None = None
    if args:
        try:
            chat_id = int(args.split()[0])
        except ValueError:
            await message.answer("chat_id должен быть числом.")
            return
    if chat_id is not None:
        if not await ensure_access(message, chat_id):
            return
        rules_list = db().list_rules(chat_id)
        text = "Правил нет." if not rules_list else "\n".join(format_rule(rule) for rule in rules_list)
        await message.answer(text)
        return

    if not await ensure_access(message):
        return
    chunks: list[str] = []
    for chat in db().list_chats(user_id(message)):
        rules_list = db().list_rules(chat["chat_id"])
        chunks.append(f"<b>{h(chat['title'])}</b> (<code>{chat['chat_id']}</code>): {len(rules_list)}")
    await message.answer("Нет доступных чатов." if not chunks else "\n".join(chunks))


@router.message(Command("whitelist"))
async def whitelist(message: Message) -> None:
    args = command_args(message)
    parts = args.split(maxsplit=2)
    if not parts:
        await message.answer(
            "Формат:\n"
            "/whitelist add_expr <chat_id> <выражение>\n"
            "/whitelist add_user <chat_id> <user_id>\n"
            "/whitelist del <id>"
        )
        return

    action = parts[0].lower()
    if action == "del":
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("Формат: /whitelist del <id>")
            return
        entry_id = int(parts[1])
        entry = db().get_whitelist_entry(entry_id)
        if not entry:
            await message.answer("Исключение не найдено.")
            return
        if not await ensure_access(message, entry["chat_id"]):
            return
        db().delete_whitelist(entry_id, user_id(message))
        await message.answer(f"Исключение #{entry_id} удалено.")
        return

    if action not in {"add_expr", "add_user"} or len(parts) < 3:
        await message.answer("Формат: /whitelist add_expr <chat_id> <выражение> или /whitelist add_user <chat_id> <user_id>")
        return

    chat_and_value = parts[1] + " " + parts[2]
    chat_parts = chat_and_value.split(maxsplit=1)
    if len(chat_parts) < 2:
        await message.answer("Укажите chat_id и значение.")
        return
    try:
        chat_id = int(chat_parts[0])
    except ValueError:
        await message.answer("chat_id должен быть числом.")
        return
    value = chat_parts[1].strip()
    if not await ensure_access(message, chat_id):
        return
    if action == "add_user" and not value.lstrip("-").isdigit():
        await message.answer("user_id должен быть числом.")
        return

    entry_type = "expr" if action == "add_expr" else "user"
    entry_id = db().add_whitelist(
        chat_id=chat_id,
        entry_type=entry_type,
        value=value,
        created_by=user_id(message),
    )
    await message.answer(f"Исключение #{entry_id} добавлено.")


@router.message(Command("mode"))
async def mode(message: Message) -> None:
    parts = command_args(message).split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /mode <chat_id> <test|active|disabled>")
        return
    try:
        chat_id = int(parts[0])
    except ValueError:
        await message.answer("chat_id должен быть числом.")
        return
    mode_value = parts[1].strip().lower()
    if mode_value not in MODES:
        await message.answer("Режим: test, active или disabled.")
        return
    if not await ensure_access(message, chat_id):
        return
    db().set_mode(chat_id, mode_value, user_id(message))
    await message.answer(f"Режим чата <code>{chat_id}</code>: <b>{h(mode_value)}</b>.")


@router.message(Command("ml"))
async def ml_settings(message: Message) -> None:
    parts = command_args(message).split()
    if len(parts) < 2:
        await message.answer("Формат: /ml <chat_id> <on|off> [threshold]")
        return
    try:
        chat_id = int(parts[0])
    except ValueError:
        await message.answer("chat_id должен быть числом.")
        return
    if not await ensure_access(message, chat_id):
        return
    state = parts[1].lower()
    if state not in {"on", "off"}:
        await message.answer("Используй on или off.")
        return
    threshold = config().ml_spam_threshold
    if len(parts) > 2:
        try:
            threshold = float(parts[2])
        except ValueError:
            await message.answer("threshold должен быть числом от 0 до 1.")
            return
    if threshold <= 0 or threshold >= 1:
        await message.answer("threshold должен быть между 0 и 1.")
        return
    db().set_ml(chat_id, state == "on", threshold, user_id(message))
    ready = spam_model().ready if spam_model() else False
    await message.answer(
        f"ML для <code>{chat_id}</code>: <b>{h(state)}</b>, threshold={threshold:.2f}, model_ready={ready}."
    )


@router.message(Command("test"))
async def test_text(message: Message) -> None:
    args = command_args(message)
    parts = args.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /test <chat_id> <текст>")
        return
    try:
        chat_id = int(parts[0])
    except ValueError:
        await message.answer("chat_id должен быть числом.")
        return
    if not await ensure_access(message, chat_id):
        return

    evaluation = evaluate_message(
        chat_id=chat_id,
        text=parts[1],
        author_id=user_id(message),
        sender_chat_id=None,
        rules=db().list_rules(chat_id, active_only=True),
        whitelist=db().list_whitelist(chat_id),
    )
    if evaluation.allowed and evaluation.whitelist:
        await message.answer(f"Разрешено whitelist #{evaluation.whitelist.id}: <code>{h(evaluation.whitelist.value)}</code>")
    elif evaluation.match:
        match = evaluation.match
        await message.answer(
            f"Сработало правило #{match.rule.id}: <code>{h(match.rule.pattern)}</code>\n"
            f"Найдено: <code>{h(match.matched)}</code>\n"
            f"Действие: <b>{h(match.rule.action)}</b>."
        )
    else:
        ml_decision = spam_model().predict(parts[1]) if spam_model() else None
        if ml_decision:
            await message.answer(
                f"Правила не сработали.\n"
                f"ML spam_score={ml_decision.score:.3f}, threshold={config().ml_spam_threshold:.3f}, "
                f"spam={ml_decision.spam}, model={h(ml_decision.model_version)}."
            )
        else:
            await message.answer("Срабатываний нет.")


@router.message(Command("logs"))
async def logs(message: Message) -> None:
    parts = command_args(message).split()
    chat_id: int | None = None
    limit = 20
    if parts:
        try:
            chat_id = int(parts[0])
        except ValueError:
            await message.answer("chat_id должен быть числом.")
            return
    if len(parts) > 1:
        try:
            limit = int(parts[1])
        except ValueError:
            await message.answer("limit должен быть числом.")
            return
    if chat_id is None:
        if not await ensure_access(message):
            return
    elif not await ensure_access(message, chat_id):
        return
    await message.answer(format_events(db().list_events(chat_id, limit)))


@router.message(Command("stats"))
async def stats(message: Message) -> None:
    args = command_args(message)
    chat_id: int | None = None
    if args:
        try:
            chat_id = int(args.split()[0])
        except ValueError:
            await message.answer("chat_id должен быть числом.")
            return
    if chat_id is None:
        if not await ensure_access(message):
            return
    elif not await ensure_access(message, chat_id):
        return
    await message.answer(format_stats(db().stats(chat_id)))


@router.message(Command("status"))
async def status(message: Message) -> None:
    if not await ensure_access(message):
        return
    await message.answer(build_status())


@router.message(Command("grant_admin"))
async def grant_admin(message: Message) -> None:
    if not await ensure_access(message, owner_only=True):
        return
    parts = command_args(message).split()
    if not parts or not parts[0].isdigit():
        await message.answer("Формат: /grant_admin <user_id> [chat_id]")
        return
    target_user = int(parts[0])
    db().ensure_user(target_user, None, None, "admin")
    if len(parts) == 1:
        db().set_global_role(target_user, "admin")
        await message.answer(f"Пользователь <code>{target_user}</code> стал глобальным администратором.")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.answer("chat_id должен быть числом.")
        return
    if db().get_chat(chat_id) is None:
        await message.answer("Чат не подключён. Сначала /connect.")
        return
    db().grant_chat_access(target_user, chat_id, "admin")
    await message.answer(f"Пользователь <code>{target_user}</code> получил доступ к <code>{chat_id}</code>.")


@router.message(Command("revoke_admin"))
async def revoke_admin(message: Message) -> None:
    if not await ensure_access(message, owner_only=True):
        return
    parts = command_args(message).split()
    if not parts or not parts[0].isdigit():
        await message.answer("Формат: /revoke_admin <user_id> [chat_id]")
        return
    target_user = int(parts[0])
    if len(parts) == 1:
        db().set_global_role(target_user, "moderator")
        await message.answer(f"Глобальные права пользователя <code>{target_user}</code> сняты.")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.answer("chat_id должен быть числом.")
        return
    db().revoke_chat_access(target_user, chat_id)
    await message.answer(f"Доступ пользователя <code>{target_user}</code> к <code>{chat_id}</code> снят.")


@router.message()
async def moderate_message(message: Message, bot: Bot) -> None:
    await moderate(message, bot, is_edited=False)


@router.edited_message()
async def moderate_edited_message(message: Message, bot: Bot) -> None:
    await moderate(message, bot, is_edited=True)


@router.channel_post()
async def moderate_channel_post(message: Message, bot: Bot) -> None:
    await moderate(message, bot, is_edited=False)


@router.edited_channel_post()
async def moderate_edited_channel_post(message: Message, bot: Bot) -> None:
    await moderate(message, bot, is_edited=True)


async def moderate(message: Message, bot: Bot, *, is_edited: bool) -> None:
    if message.chat.type == "private":
        return

    text = message.text or message.caption
    if not text:
        return

    chat = db().get_chat(message.chat.id)
    if not chat or chat["status"] != "connected" or chat["mode"] == "disabled":
        return
    if not subscription_ok_for_chat(chat):
        return

    author_id = message.from_user.id if message.from_user else None
    sender_chat_id = message.sender_chat.id if message.sender_chat else None
    evaluation = evaluate_message(
        chat_id=message.chat.id,
        text=text,
        author_id=author_id,
        sender_chat_id=sender_chat_id,
        rules=db().list_rules(message.chat.id, active_only=True),
        whitelist=db().list_whitelist(message.chat.id),
    )

    if evaluation.allowed:
        return

    match = evaluation.match
    ml_decision = None
    ml_triggered = False
    threshold = float(chat["ml_threshold"] if chat["ml_threshold"] is not None else config().ml_spam_threshold)
    model = spam_model()
    if not match and chat["ml_enabled"] and model and model.ready:
        ml_decision = model.predict(text, threshold=threshold)
        ml_triggered = bool(ml_decision and ml_decision.spam)

    if not match and not ml_triggered:
        return

    mode_value = chat["mode"]
    action = match.rule.action if match else config().ml_action
    rule_id = match.rule.id if match else None
    matched_value = match.matched if match else "ml_spam"
    trigger_title = (
        f"#{match.rule.id} <code>{h(match.rule.pattern)}</code>"
        if match
        else f"ML <code>{h(ml_decision.model_version if ml_decision else 'unknown')}</code>"
    )
    result = "test_hit" if mode_value == "test" else "matched"
    error: str | None = None

    if mode_value == "active" and ACTION_SEVERITY.get(action, 2) >= ACTION_SEVERITY["delete"]:
        try:
            await message.delete()
            result = "deleted"
        except TelegramRetryAfter as exc:
            result = "error"
            error = f"retry_after={exc.retry_after}"
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            result = "error"
            error = str(exc)
        except Exception as exc:
            logger.exception("Unexpected deletion error")
            result = "error"
            error = str(exc)
    elif action == "notify":
        result = "notified"

    stored_text = short_text(text) if config().store_message_text else None
    db().add_event(
        chat_id=message.chat.id,
        message_id=message.message_id,
        author_id=author_id or sender_chat_id,
        author_username=user_name(message),
        text_excerpt=stored_text,
        rule_id=rule_id,
        matched=matched_value,
        action=action,
        result=result,
        error=error,
        is_edited=is_edited,
        message_link=message.get_url(force_private=False) if hasattr(message, "get_url") else None,
        ml_score=ml_decision.score if ml_decision else None,
        ml_model_version=ml_decision.model_version if ml_decision else None,
    )

    if chat["notify_enabled"] or result == "error":
        await notify_admins(
            bot,
            chat_id=message.chat.id,
            text=(
                f"<b>Срабатывание</b>\n"
                f"Чат: <code>{message.chat.id}</code>\n"
                f"Источник: {trigger_title}\n"
                f"Найдено: <code>{h(matched_value)}</code>\n"
                f"{'ML score: ' + format(ml_decision.score, '.3f') + chr(10) if ml_decision else ''}"
                f"Режим: {h(mode_value)}; результат: <b>{h(result)}</b>\n"
                f"{'Ошибка: <code>' + h(error) + '</code>' if error else ''}"
            ),
        )


async def notify_admins(bot: Bot, *, chat_id: int, text: str) -> None:
    recipients = notification_recipients(chat_id)
    for recipient in recipients:
        try:
            await bot.send_message(recipient, text)
        except Exception:
            logger.exception("Failed to notify admin %s", recipient)


def notification_recipients(chat_id: int) -> Iterable[int]:
    rows = db().conn.execute(
        """
        SELECT user_id FROM system_users WHERE status = 'active' AND role IN ('owner', 'admin')
        UNION
        SELECT user_id FROM user_chat_access WHERE chat_id = ?
        """,
        (chat_id,),
    ).fetchall()
    return [int(row["user_id"]) for row in rows]


def format_events(events) -> str:
    if not events:
        return "Журнал пуст."
    lines: list[str] = []
    for row in events:
        err = f" error={h(row['error'])}" if row["error"] else ""
        edited = " edited" if row["is_edited"] else ""
        ml = f" ml={row['ml_score']:.3f}" if row["ml_score"] is not None else ""
        lines.append(
            f"#{row['id']} {h(row['created_at'])} chat=<code>{row['chat_id']}</code> "
            f"msg={row['message_id']} rule={row['rule_id']} result=<b>{h(row['result'])}</b>{edited}{ml}{err}"
        )
    return "\n".join(lines)


def format_stats(stats_data: dict[str, int]) -> str:
    return (
        "<b>Статистика</b>\n"
        f"События: {stats_data['events']}\n"
        f"Удалено: {stats_data['deleted']}\n"
        f"Тестовые срабатывания: {stats_data['test_hits']}\n"
        f"ML-срабатывания: {stats_data['ml_hits']}\n"
        f"Ошибки: {stats_data['errors']}"
    )


def build_status() -> str:
    chats = db().conn.execute("SELECT COUNT(*) AS count FROM chats").fetchone()["count"]
    rules_count = db().conn.execute("SELECT COUNT(*) AS count FROM rules").fetchone()["count"]
    events = db().conn.execute("SELECT COUNT(*) AS count FROM moderation_events").fetchone()["count"]
    model = spam_model()
    return (
        "<b>Статус</b>\n"
        f"Чатов: {chats}\n"
        f"Правил: {rules_count}\n"
        f"Событий: {events}\n"
        f"Подписка обязательна: {config().subscription_required}\n"
        f"Crypto Pay: {billing().configured if BILLING else False}\n"
        f"ML: {model.ready if model else False}"
        f"{' / ' + h(model.version) if model and model.ready else ''}\n"
        f"База: <code>{h(config().database_path)}</code>"
    )
