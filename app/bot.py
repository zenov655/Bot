import os
import sys

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

from app.config import settings
from app.storage import (
    create_order,
    get_order,
    list_orders_by_status,
    list_user_orders,
    set_order_status,
    set_order_comment,
    save_donate_comment,
    list_donate_comments,
    list_orders_with_comments,
)


bot = Bot(
    token=settings.bot.token,
    default=DefaultBotProperties(parse_mode="HTML"),
)
dp = Dispatcher()


# Администраторы бота (твои Telegram user_id)
ADMIN_IDS: set[int] = {2101722815}

VIP_MENU_TEXT = "Питти🥕𝗩𝗶𝗽 𝗰𝗹𝘂𝗯"

# Номер карты СберБанк, на которую пользователи переводят деньги
CARD_NUMBER = "4276300043627325"


# Ожидание квитанции по конкретному заказу: user_id -> order_id
PENDING_RECEIPTS: dict[int, int] = {}

# Ожидание текста комментария: user_id -> цель ("donate" или "order:<id>")
PENDING_COMMENT: dict[int, str] = {}

# Сообщение-приглашение написать комментарий: user_id -> message_id (чтобы убрать кнопку «Пропустить»)
COMMENT_PROMPT_MSG: dict[int, int] = {}

# Тексты кнопок главного меню — их не считаем комментарием
MENU_BUTTON_TEXTS: set[str] = {VIP_MENU_TEXT, "🍪Донатик🍪"}

# Ожидание ответа администратора клиенту: admin_id -> (user_id, order_id)
ADMIN_REPLY_TARGET: dict[int, tuple[int, int]] = {}


def format_order(order: dict) -> str:
    """Форматирование заказа для вывода в админ-панели."""
    username = f"@{order['username']}" if order.get("username") else "(нет username)"
    status_map = {
        "waiting_payment": "Ожидает оплаты",
        "waiting_confirmation": "Ожидает подтверждения",
        "paid": "Оплачен",
        "cancelled": "Отменён",
    }
    status_human = status_map.get(order["status"], order["status"])
    text = (
        f"Заказ #{order['id']}\n"
        f"Пользователь: {order['full_name']} {username}\n"
        f"Сумма: {order['amount']} ₽\n"
        f"Статус: {status_human}"
    )
    comment = (order.get("comment") or "").strip()
    if comment:
        text += f"\n💬 Комментарий: {comment}"
    return text

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=VIP_MENU_TEXT)],
            [KeyboardButton(text="🍪Донатик🍪")],
        ],
        resize_keyboard=True,
    )


async def soft_restart() -> None:
    """Мягкий сброс: очищает состояние и застрявшую очередь обновлений.

    Бот при этом не выключается — просто «разгружается», если интерфейс лагает.
    """
    PENDING_RECEIPTS.clear()
    PENDING_COMMENT.clear()
    COMMENT_PROMPT_MSG.clear()
    ADMIN_REPLY_TARGET.clear()

    # Если бот работает через вебхук — переустанавливаем его,
    # попутно отбрасывая накопившуюся очередь обновлений.
    base = (settings.webhook_url or "").strip()
    webhook_url = ""
    if base:
        if base.endswith("/telegram-webhook"):
            webhook_url = base
        elif base.endswith("/telegram-webhook/"):
            webhook_url = base.rstrip("/")
        else:
            webhook_url = base.rstrip("/") + "/telegram-webhook"

    if webhook_url:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.set_webhook(webhook_url)
        except Exception:
            pass


async def send_admin_comments(message: types.Message) -> None:
    """Показать администратору все комментарии (к донатам и к заказам)."""
    donate_rows = list_donate_comments()
    order_rows = list_orders_with_comments()

    blocks: list[str] = []

    if donate_rows:
        lines = []
        for r in donate_rows:
            username = f"@{r['username']}" if r.get("username") else "(нет username)"
            lines.append(
                f"#{r['id']} [{r['created_at']}] {r['full_name']} {username}\n"
                f"💬 {r['comment']}"
            )
        blocks.append("🍪 Комментарии к донатам:\n\n" + "\n\n".join(lines))

    if order_rows:
        lines = []
        for o in order_rows:
            username = f"@{o['username']}" if o.get("username") else "(нет username)"
            lines.append(
                f"Заказ #{o['id']} [{o['created_at']}] {o['full_name']} {username}\n"
                f"💬 {o['comment']}"
            )
        blocks.append("🥕 Комментарии к заказам:\n\n" + "\n\n".join(lines))

    if not blocks:
        await message.answer(
            "Комментариев пока нет.",
            reply_markup=admin_main_menu_keyboard(),
        )
        return

    await message.answer(
        "\n\n➖➖➖\n\n".join(blocks),
        reply_markup=admin_main_menu_keyboard(),
    )


def admin_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Админ: заказы в ожидании")],
            [KeyboardButton(text="Админ: комментарии")],
            [KeyboardButton(text="🔁 Перезапустить")],
        ],
        resize_keyboard=True,
    )


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    PENDING_COMMENT.pop(message.from_user.id, None)
    COMMENT_PROMPT_MSG.pop(message.from_user.id, None)
    if message.from_user.id in ADMIN_IDS:
        ADMIN_REPLY_TARGET.pop(message.from_user.id, None)
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "Привет, администратор!\n"
            "Используй меню ниже для управления заказами.",
            reply_markup=admin_main_menu_keyboard(),
        )
    else:
        await message.answer(
            "🍒Добро пожаловать🍒\n"
            "Выберите раздел снизу.",
            reply_markup=main_menu_keyboard(),
        )


@dp.message(
    lambda m: m.from_user.id in ADMIN_IDS
    and m.from_user.id in ADMIN_REPLY_TARGET
    and not (m.text and m.text.startswith("/"))
)
async def handle_admin_reply(message: types.Message):
    user_id, order_id = ADMIN_REPLY_TARGET[message.from_user.id]

    try:
        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except Exception:
        await message.answer(
            "Не удалось отправить сообщение пользователю. Возможно, пользователь заблокировал бота."
        )
        return

    ADMIN_REPLY_TARGET.pop(message.from_user.id, None)
    await message.answer(f"Отправлено пользователю по заказу #{order_id}.")


@dp.message(
    F.text,
    ~F.text.startswith("/"),
    lambda m: m.from_user.id in PENDING_COMMENT
    and (m.text or "").strip() not in MENU_BUTTON_TEXTS,
)
async def handle_pending_comment(message: types.Message):
    """Пользователь прислал текст комментария к донату или заказу."""
    if message.from_user.id in ADMIN_IDS and message.from_user.id in ADMIN_REPLY_TARGET:
        return

    target = PENDING_COMMENT.pop(message.from_user.id)
    comment = (message.text or "").strip()
    if not comment:
        await message.answer("Комментарий пуст. Отправь текст или нажми /start.")
        return

    # Убираем кнопку «Пропустить» с приглашения
    prompt_id = COMMENT_PROMPT_MSG.pop(message.from_user.id, None)
    if prompt_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=prompt_id,
                reply_markup=None,
            )
        except Exception:
            pass

    if target == "donate":
        record = save_donate_comment(
            user_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            comment=comment,
        )
        await message.answer(
            "💬 Спасибо! Комментарий сохранён и передан автору."
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    (
                        f"💬 Комментарий к донату #{record['id']}\n"
                        f"От: {message.from_user.full_name} "
                        f"(@{message.from_user.username or 'нет username'})\n"
                        f"Текст: {comment}"
                    ),
                )
            except Exception:
                pass
    elif target.startswith("order:"):
        try:
            order_id = int(target.split(":", 1)[1])
        except ValueError:
            return
        order = get_order(order_id)
        if not order or order["user_id"] != message.from_user.id:
            await message.answer("Заказ не найден.")
            return

        set_order_comment(order_id, comment)
        await message.answer(f"💬 Комментарий к заказу #{order_id} сохранён.")
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    (
                        f"💬 Комментарий к заказу #{order_id}\n"
                        f"От: {message.from_user.full_name} "
                        f"(@{message.from_user.username or 'нет username'})\n"
                        f"Текст: {comment}"
                    ),
                )
            except Exception:
                pass


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_menu(message: types.Message):
    """Обработка нажатий на кнопки главного меню (reply-клавиатура)."""
    if message.from_user.id in ADMIN_IDS and message.from_user.id in ADMIN_REPLY_TARGET:
        return

    raw_text = (message.text or "").strip()
    text = raw_text.lower()

    # Если пользователь был в режиме ввода комментария и нажал кнопку меню —
    # отменяем ввод, чтобы кнопка не попала в комментарий.
    if raw_text in MENU_BUTTON_TEXTS:
        PENDING_COMMENT.pop(message.from_user.id, None)
        COMMENT_PROMPT_MSG.pop(message.from_user.id, None)

    if raw_text == VIP_MENU_TEXT or text == "питти vip club":
        # Один тип заказа "Питти vip"
        orders_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Питти🥕𝗩𝗶𝗽 𝗰𝗹𝘂𝗯 — 399 Rub",
                        callback_data="order:vip:399",
                    )
                ],
            ]
        )
        await message.answer(
            "Питти🥕𝘃𝗶𝗽 𝗰𝗹𝘂𝗯\n\n"
            "Стоимость подписки 399 Rub (НАВСЕГДА)\n\n"
            "👇Ты получаешь доступ в:\n\n"
            "— Питти🥕𝘃𝗶𝗽 𝗰𝗹𝘂𝗯\n\n"
            "В этом канале будут все фуллы хентай артов, а так же доп. посты которые не вошли в основу.\n\n"
            "🎀 Заходя в этот канал у вас есть возможность заказывать арты по скидке для подписчиков.\n\n"
            "🎀 Голосовать за выбор персонажа/аниме/игры.\n\n"
            "🎀 Предлагать идеи для основного канала.\n\n"
            "🥕 По всем остальным вопросам и предложениям пишите в лс - @RabbitPitty",
            reply_markup=orders_keyboard,
        )
    elif text == "🍪донатик🍪":
        donate_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Оплата СберБанк",
                        callback_data="donate:sberbank",
                    )
                ],
            ]
        )
        await message.answer(
            "🍓 Любая сумма - это поддержка и вклад для улучшения контента! Коплю на новую видеокарту! 🍓",
            reply_markup=donate_keyboard,
        )
    elif (
        text == "админ: заказы в ожидании"
        and message.from_user.id in ADMIN_IDS
    ):
        waiting = list_orders_by_status("waiting_confirmation")
        if not waiting:
            await message.answer(
                "Нет заказов в ожидании подтверждения.",
                reply_markup=admin_main_menu_keyboard(),
            )
            return
        for order in waiting:
            text = format_order(order)
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить",
                            callback_data=f"admin_confirm:{order['id']}",
                        ),
                        InlineKeyboardButton(
                            text="❌ Отменить",
                            callback_data=f"admin_cancel:{order['id']}",
                        ),
                    ]
                ]
            )
            await message.answer(text, reply_markup=keyboard)
        await message.answer(
            "Это все заказы в ожидании.",
            reply_markup=admin_main_menu_keyboard(),
        )
    elif (
        text == "админ: комментарии"
        and message.from_user.id in ADMIN_IDS
    ):
        await send_admin_comments(message)
    elif (
        text == "🔁 перезапустить"
        and message.from_user.id in ADMIN_IDS
    ):
        await soft_restart()
        await message.answer(
            "✅ Бот перезапущен. Очередь и состояние сброшены.",
            reply_markup=admin_main_menu_keyboard(),
        )


@dp.callback_query()
async def handle_callback(query: CallbackQuery):
    """Обработка нажатий на inline-кнопки."""
    data = query.data or ""

    if data.startswith("order:"):
        # Выбор заказа "Питти vip" с фиксированной суммой
        try:
            _, variant_str, amount_str = data.split(":", 2)
            # variant_str ожидаем как "vip", amount_str — сумма
            variant_label = variant_str
            amount = int(amount_str)
        except Exception:
            await query.answer("Некорректные данные заказа", show_alert=True)
            return

        active = [
            o
            for o in list_user_orders(query.from_user.id)
            if o["status"] in {"waiting_payment", "waiting_confirmation"}
        ]
        if active:
            order = active[0]
            status = order.get("status")
            if status == "waiting_confirmation":
                await query.answer()
                await query.message.answer(
                    "🍰У тебя уже есть активный заказ. Он ожидает подтверждения администратора. "
                    "Дождитесь пожалуйста🍰"
                )
            else:
                guidance = "\n\nСтатус: ожидает оплаты. Оплати по инструкции ниже."
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Оплата СберБанк",
                                callback_data=f"paybank:sberbank:{order['id']}",
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                text="✅ Я оплатил",
                                callback_data=f"paid:{order['id']}",
                            )
                        ],
                    ]
                )
                await query.answer()
                await query.message.answer(
                    "У тебя уже есть активный заказ." + guidance,
                    reply_markup=keyboard,
                )
            return

        order = create_order(
            user_id=query.from_user.id,
            username=query.from_user.username,
            full_name=query.from_user.full_name,
            amount=amount,
        )
        order_id = order["id"]

        pay_text = (
            f"Сумма к оплате: {amount}₽\n"
            f"И Вы получаете доступ к: {VIP_MENU_TEXT}\n"
            f"Если не подходит способ оплаты, то пишите в лс @RabbitPitty"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Оплата СберБанк",
                        callback_data=f"paybank:sberbank:{order_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="✅ Я оплатил",
                        callback_data=f"paid:{order_id}",
                    )
                ],
            ]
        )

        await query.message.edit_text(pay_text, reply_markup=keyboard)
        await query.answer()
    elif data == "donate:sberbank":
        text = (
            f"🍓Карта СберБанк: {CARD_NUMBER}\n"
            "🍓Получатель: Максим З."
        )
        await query.message.answer(text)

        # Сразу предлагаем оставить комментарий (необязательно)
        PENDING_COMMENT[query.from_user.id] = "donate"
        skip_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Пропустить",
                        callback_data="skip_comment",
                    )
                ],
            ]
        )
        prompt = await query.message.answer(
            "✍️ Напиши комментарий к донату одним сообщением — он сразу уйдёт автору.\n"
            "Если не хочешь, нажми «Пропустить».",
            reply_markup=skip_kb,
        )
        COMMENT_PROMPT_MSG[query.from_user.id] = prompt.message_id
        await query.answer()
    elif data == "skip_comment":
        PENDING_COMMENT.pop(query.from_user.id, None)
        COMMENT_PROMPT_MSG.pop(query.from_user.id, None)
        try:
            await query.message.edit_text("Хорошо, комментарий пропущен.")
        except Exception:
            pass
        await query.answer()
    elif data.startswith("paybank:"):
        # Пользователь выбрал банк для оплаты конкретного заказа
        try:
            _, bank, order_id_str = data.split(":", 2)
            order_id = int(order_id_str)
        except Exception:
            await query.answer("Некорректные данные заказа", show_alert=True)
            return

        order = get_order(order_id)
        if not order or order["user_id"] != query.from_user.id:
            await query.answer("Заказ не найден", show_alert=True)
            return

        if bank == "sberbank":
            text = (
                f"🥕 Карта СберБанк: {CARD_NUMBER}\n"
                "🥕 Получатель: Максим З."
            )
        else:
            await query.answer("Неизвестный банк", show_alert=True)
            return

        await query.message.answer(text)
        await query.answer()
    elif data.startswith("paid:"):
        # Пользователь сообщает, что оплатил заказ
        try:
            order_id = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный номер заказа", show_alert=True)
            return

        order = get_order(order_id)
        if not order or order["user_id"] != query.from_user.id:
            await query.answer("Заказ не найден", show_alert=True)
            return

        set_order_status(order_id, "waiting_confirmation")
        # Запрашиваем у пользователя квитанцию
        PENDING_RECEIPTS[query.from_user.id] = order_id
        await query.answer(
            "Мы получили информацию об оплате. Пришли, пожалуйста, квитанцию об оплате в виде фото или файла.",
            show_alert=True,
        )
        # Убираем кнопки под сообщением
        await query.message.edit_reply_markup(reply_markup=None)

        # Уведомляем администраторов о том, что пользователь сообщил об оплате
        order_comment = (order.get("comment") or "").strip()
        comment_line = f"\n💬 Комментарий: {order_comment}" if order_comment else ""
        for admin_id in ADMIN_IDS:
            try:
                admin_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✉️ Ответить пользователю",
                                callback_data=f"admin_reply:{order_id}",
                            )
                        ]
                    ]
                )
                await bot.send_message(
                    admin_id,
                    (
                        f"Пользователь {order['full_name']} (@{order.get('username') or 'нет username'}) "
                        f"сообщил об оплате заказа #{order_id}. Ожидается квитанция."
                        f"{comment_line}"
                    ),
                    reply_markup=admin_keyboard,
                )
            except Exception:
                pass
    elif data.startswith("admin_reply:") and query.from_user.id in ADMIN_IDS:
        try:
            order_id = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer("Некорректные данные", show_alert=True)
            return

        order = get_order(order_id)
        if not order:
            await query.answer("Заказ не найден", show_alert=True)
            return

        ADMIN_REPLY_TARGET[query.from_user.id] = (order["user_id"], order_id)
        await query.answer()
        await query.message.answer(
            f"Напиши сообщение для пользователя по заказу #{order_id} (текст/фото/файл и т.д.). Следующее твоё сообщение будет отправлено клиенту."
        )
    elif data.startswith("admin_confirm:") and query.from_user.id in ADMIN_IDS:
        try:
            order_id = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный ID заказа", show_alert=True)
            return

        order = get_order(order_id)
        if not order:
            await query.answer("Заказ не найден", show_alert=True)
            return

        set_order_status(order_id, "paid")
        order["status"] = "paid"
        await query.message.edit_text(format_order(order) + "\n\n✅ Оплата подтверждена администратором.")

        try:
            await bot.send_message(
                order["user_id"],
                f"Твоя оплата по заказу #{order_id} подтверждена. Спасибо!",
            )
        except Exception:
            pass

        await query.answer("Заказ подтверждён.", show_alert=False)
    elif data.startswith("admin_cancel:") and query.from_user.id in ADMIN_IDS:
        try:
            order_id = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный ID заказа", show_alert=True)
            return

        order = get_order(order_id)
        if not order:
            await query.answer("Заказ не найден", show_alert=True)
            return

        set_order_status(order_id, "cancelled")
        order["status"] = "cancelled"
        await query.message.edit_text(format_order(order) + "\n\n❌ Заказ отменён администратором.")

        try:
            await bot.send_message(
                order["user_id"],
                f"Твой заказ #{order_id} был отменён. Если это ошибка, свяжись с администратором.",
            )
        except Exception:
            pass

        await query.answer("Заказ отменён.", show_alert=False)
    else:
        await query.answer()


@dp.message(F.photo)
async def handle_receipt_photo(message: types.Message):
    """Получение квитанции в виде фото и пересылка администратору."""
    user_id = message.from_user.id
    order_id = PENDING_RECEIPTS.get(user_id)
    if not order_id:
        return

    caption = (
        f"Квитанция по заказу #{order_id} от {message.from_user.full_name}"
        f" (@{message.from_user.username or 'нет username'})."
    )

    photo = message.photo[-1]
    for admin_id in ADMIN_IDS:
        try:
            admin_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✉️ Ответить пользователю",
                            callback_data=f"admin_reply:{order_id}",
                        )
                    ]
                ]
            )
            await bot.send_photo(
                admin_id,
                photo.file_id,
                caption=caption,
                reply_markup=admin_keyboard,
            )
        except Exception:
            pass

    PENDING_RECEIPTS.pop(user_id, None)
    await message.answer("Спасибо! Квитанция отправлена администратору.")


@dp.message(F.document)
async def handle_receipt_document(message: types.Message):
    """Получение квитанции в виде файла и пересылка администратору."""
    user_id = message.from_user.id
    order_id = PENDING_RECEIPTS.get(user_id)
    if not order_id:
        return

    caption = (
        f"Квитанция по заказу #{order_id} от {message.from_user.full_name}"
        f" (@{message.from_user.username or 'нет username'})."
    )

    doc = message.document
    for admin_id in ADMIN_IDS:
        try:
            admin_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✉️ Ответить пользователю",
                            callback_data=f"admin_reply:{order_id}",
                        )
                    ]
                ]
            )
            await bot.send_document(
                admin_id,
                doc.file_id,
                caption=caption,
                reply_markup=admin_keyboard,
            )
        except Exception:
            pass

    PENDING_RECEIPTS.pop(user_id, None)
    await message.answer("Спасибо! Квитанция отправлена администратору.")


@dp.message(Command("admin_orders"))
async def cmd_admin_orders(message: types.Message):
    """Показать заказы, ожидающие подтверждения (для админа)."""
    if message.from_user.id not in ADMIN_IDS:
        return

    waiting = list_orders_by_status("waiting_confirmation")
    if not waiting:
        await message.answer("Нет заказов в ожидании подтверждения.")
        return

    lines = [format_order(o) for o in waiting]
    await message.answer("Заказы в ожидании:\n\n" + "\n\n".join(lines))


@dp.message(Command("comments"))
async def cmd_admin_comments(message: types.Message):
    """Показать комментарии (к донатам и к заказам) — для админа."""
    if message.from_user.id not in ADMIN_IDS:
        return

    await send_admin_comments(message)


@dp.message(Command("restart"))
async def cmd_admin_restart(message: types.Message):
    """Мягкий перезапуск бота — для админа."""
    if message.from_user.id not in ADMIN_IDS:
        return

    await soft_restart()
    await message.answer(
        "✅ Бот перезапущен. Очередь и состояние сброшены.",
        reply_markup=admin_main_menu_keyboard(),
    )


@dp.message(Command("confirm"))
async def cmd_confirm(message: types.Message):
    """Отметить заказ как оплаченный (для админа)."""
    if message.from_user.id not in ADMIN_IDS:
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /confirm <id_заказа>")
        return

    try:
        order_id = int(parts[1])
    except ValueError:
        await message.answer("ID заказа должен быть числом.")
        return

    order = get_order(order_id)
    if not order:
        await message.answer("Заказ не найден.")
        return

    set_order_status(order_id, "paid")
    await message.answer(f"Заказ #{order_id} отмечен как оплаченный.")

    # Пытаемся уведомить пользователя
    try:
        await bot.send_message(
            order["user_id"],
            f"Твоя оплата по заказу #{order_id} подтверждена. Спасибо!",
        )
    except Exception:
        # Не критично, если не удалось отправить сообщение
        pass


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    """Отменить заказ (для админа)."""
    if message.from_user.id not in ADMIN_IDS:
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /cancel <id_заказа>")
        return

    try:
        order_id = int(parts[1])
    except ValueError:
        await message.answer("ID заказа должен быть числом.")
        return

    order = get_order(order_id)
    if not order:
        await message.answer("Заказ не найден.")
        return

    set_order_status(order_id, "cancelled")
    await message.answer(f"Заказ #{order_id} отменён.")

    try:
        await bot.send_message(
            order["user_id"],
            f"Твой заказ #{order_id} был отменён. Если это ошибка, свяжись с администратором.",
        )
    except Exception:
        pass
