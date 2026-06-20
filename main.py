import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import CommandObject
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup
from sqlalchemy import delete, func, or_, select

import database
from config import load_config
from keyboards import (
    BACK_BUTTON,
    accepted_order_keyboard,
    admin_contacts_keyboard,
    admin_driver_keyboard,
    admin_driver_manage_keyboard,
    back_button,
    broadcast_target_keyboard,
    channel_order_keyboard,
    channel_trip_keyboard,
    city_keyboard,
    date_keyboard,
    DISTRICTS_BY_CITY,
    district_keyboard,
    language_keyboard,
    location_keyboard,
    main_menu,
    max_price_keyboard,
    my_order_cancel_keyboard,
    order_keyboard,
    phone_keyboard,
    price_keyboard,
    skip_keyboard,
    subscribe_keyboard,
    time_keyboard,
    trip_select_keyboard,
    yes_no_keyboard,
)
from models import Driver, DriverPhoto, DriverTrip, Order, OrderLocation, OrderMessage, User


router = Router()
config = load_config()


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if not config.channel_id:
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        if not user or is_admin(user.id):
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data == "check_sub":
            return await handler(event, data)

        bot: Bot = data["bot"]
        if await is_channel_member(bot, user.id):
            return await handler(event, data)

        text = (
            "Botdan foydalanish uchun avval kanalimizga a'zo bo'ling.\n\n"
            f"Kanal: {config.channel_id}\n"
            "A'zo bo'lgach, ✅ Tekshirish tugmasini bosing."
        )
        if isinstance(event, CallbackQuery):
            await event.answer("Avval kanalga a'zo bo'ling.", show_alert=True)
            await event.message.answer(text, reply_markup=subscribe_keyboard(config.channel_id))
        elif isinstance(event, Message):
            await event.answer(text, reply_markup=subscribe_keyboard(config.channel_id))
        return None


async def is_channel_member(bot: Bot, user_id: int) -> bool:
    if not config.channel_id:
        return True
    try:
        member = await bot.get_chat_member(config.channel_id, user_id)
    except Exception as exc:
        logging.warning("Kanal a'zoligi tekshirilmadi: %s", exc)
        return False
    return member.status in {"creator", "administrator", "member"}


class PassengerOrder(StatesGroup):
    phone = State()
    from_city = State()
    from_district = State()
    to_city = State()
    to_district = State()
    location = State()
    date = State()
    time = State()
    passengers_count = State()
    roof_luggage = State()
    max_price = State()
    comment = State()


class DriverRegister(StatesGroup):
    phone = State()
    full_name = State()
    car_model = State()
    car_color = State()
    car_number = State()
    seats_count = State()
    car_front_photo = State()
    car_back_photo = State()
    car_side_photo = State()
    driver_license_photo = State()
    tech_passport_photo = State()


class DriverTripCreate(StatesGroup):
    from_city = State()
    from_district = State()
    to_city = State()
    to_district = State()
    date = State()
    time = State()
    available_seats = State()
    price_per_person = State()
    roof_luggage = State()
    comment = State()


class AdminBroadcast(StatesGroup):
    target = State()
    text = State()


class AdminSearch(StatesGroup):
    query = State()


async def get_or_create_user(message: Message) -> User:
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()
        if user:
            return user

        full_name = message.from_user.full_name or message.from_user.username or "Foydalanuvchi"
        user = User(telegram_id=message.from_user.id, full_name=full_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def is_admin(telegram_id: int) -> bool:
    return telegram_id in config.admin_ids


async def get_user_language(telegram_id: int) -> str:
    async with database.SessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        return user.language if user and user.language in {"uz", "ru"} else "uz"


async def user_main_menu(telegram_id: int) -> ReplyKeyboardMarkup:
    lang = await get_user_language(telegram_id)
    return main_menu(is_admin(telegram_id), lang)


async def get_driver_status(telegram_id: int) -> str | None:
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(Driver)
            .join(User, Driver.user_id == User.id)
            .where(User.telegram_id == telegram_id)
        )
        driver = result.scalar_one_or_none()
        return driver.status if driver else None


async def answer_existing_driver_status(message: Message, telegram_id: int, lang: str) -> bool:
    status = await get_driver_status(telegram_id)
    if status == "active":
        text = (
            "Siz haydovchi sifatida tasdiqlangansiz.\n"
            "Yo'nalish qo'shish uchun menyudan foydalaning."
            if lang == "uz"
            else "Вы подтверждены как водитель.\n"
            "Используйте меню, чтобы добавить маршрут."
        )
        await message.answer(text, reply_markup=driver_menu(lang))
        return True
    if status == "pending":
        await message.answer(
            "Arizangiz adminga yuborilgan. Tasdiqlanishini kuting."
            if lang == "uz"
            else "Ваша заявка отправлена админу. Ожидайте подтверждения."
        )
        return True
    return False


def is_yes(text: str | None) -> bool:
    return text in {"Ha", "Да"}


def is_no(text: str | None) -> bool:
    return text in {"Yo'q", "Нет"}


def is_skip_comment(text: str | None) -> bool:
    return text in {"Izoh yo'q", "Без комментария"}


def is_any_price(text: str | None) -> bool:
    return text in {"Farqi yo'q", "Не важно"}


def clean_int(text: str) -> int | None:
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def maps_link(latitude: float, longitude: float) -> str:
    return f"https://maps.google.com/?q={latitude},{longitude}"


def needs_district(city: str | None) -> bool:
    return bool(city and city in DISTRICTS_BY_CITY)


def place_with_district(city: str, district: str) -> str:
    if district.startswith(f"{city} "):
        return district
    return f"{city}, {district}"


def intro_text(lang: str | None = None) -> str:
    uz = (
        f"Assalomu alaykum! {config.bot_name} botiga xush kelibsiz.\n\n"
        "Bu bot orqali Vodiy va Toshkent yo'nalishlarida qulay safar topishingiz mumkin.\n\n"
        "Yo'lovchilar buyurtma qoldiradi, haydovchilar esa o'z yo'nalishini joylaydi. "
        "Mos kelgan haydovchi va yo'lovchi bot orqali bir-birini topadi.\n\n"
        "Haydovchilar uchun yo'nalish joylash hozircha bepul."
    )
    ru = (
        f"Здравствуйте! Добро пожаловать в бот {config.bot_name}.\n\n"
        "Через этот бот можно найти удобную поездку по направлениям Водий и Ташкент.\n\n"
        "Пассажиры оставляют заказ, а водители размещают свои маршруты. "
        "Подходящий водитель и пассажир находят друг друга через бот.\n\n"
        "Для водителей размещение маршрута пока бесплатно."
    )
    if lang == "uz":
        return uz
    if lang == "ru":
        return ru
    return f"{uz}\n\n---\n\n{ru}"


def time_match_condition(order_time: str):
    return or_(
        DriverTrip.time == order_time,
        DriverTrip.time == "⚡ Srochniy",
        order_time == "⚡ Srochniy",
    )


def format_order_for_driver(order: Order, location: OrderLocation | None = None) -> str:
    location_text = ""
    if location:
        location_text = f"\n📍 Lokatsiya: {maps_link(location.latitude, location.longitude)}"
    return (
        f"🧾 Buyurtma #{order.id}\n\n"
        f"🛣 Yo'nalish: {order.from_city} -> {order.to_city}\n"
        f"📅 Sana: {order.date}\n"
        f"🕘 Vaqt: {order.time}\n"
        f"👥 Yo'lovchi soni: {order.passengers_count}\n"
        f"💰 Maks narx: {order.price_per_person or 'Farqi yoq'}\n"
        f"🧳 Tom bagaj kerak: {order.roof_luggage or '-'}\n"
        f"💬 Izoh: {order.comment or '-'}"
        f"{location_text}"
    )


def format_order_for_channel(order: Order) -> str:
    return (
        f"🧾 <b>SafarX — Yo'lovchi buyurtmasi</b>\n\n"
        f"🛣 <b>{order.from_city}  →  {order.to_city}</b>\n\n"
        f"📅 Sana: <b>{order.date}</b>\n"
        f"🕘 Vaqt: <b>{order.time}</b>\n"
        f"👥 Yo'lovchi soni: <b>{order.passengers_count}</b>\n"
        f"💰 Maks narx: <b>{order.price_per_person or 'Farqi yoq'}</b>\n"
        f"🧳 Tom bagaj kerak: <b>{order.roof_luggage or '-'}</b>\n\n"
        f"👇 Qabul qilish uchun tugmani bosing"
    )


def format_trip_for_passenger(trip: DriverTrip, driver: Driver) -> str:
    return (
        f"🚕 Haydovchi yo'nalishi #{trip.id}\n\n"
        f"🛣 Yo'nalish: {trip.from_city} -> {trip.to_city}\n"
        f"📅 Sana: {trip.date}\n"
        f"🕘 Vaqt: {trip.time}\n"
        f"👥 Bo'sh joy: {trip.available_seats}\n"
        f"💰 Narx: {trip.price_per_person:,} so'm\n"
        f"🧳 Tom bagaj: {trip.roof_luggage}\n"
        f"🚘 Mashina: {driver.car_model} {driver.car_color}\n"
        f"💬 Izoh: {trip.comment or '-'}"
    ).replace(",", " ")


def format_channel_trip(trip: DriverTrip, driver: Driver) -> str:
    status_line = "✅ <b>Joy mavjud</b>" if trip.status == "active" and trip.available_seats > 0 else "⛔ <b>Joy qolmadi</b>"
    price = f"{trip.price_per_person:,}".replace(",", " ")
    return (
        f"🚖 <b>SafarX — Haydovchi e'loni</b>\n\n"
        f"{status_line}\n\n"
        f"🛣 <b>{trip.from_city}  →  {trip.to_city}</b>\n\n"
        f"📅 Sana: <b>{trip.date}</b>\n"
        f"🕘 Vaqt: <b>{trip.time}</b>\n"
        f"💺 Bo'sh joy: <b>{trip.available_seats} ta</b>\n"
        f"💰 Narx: <b>{price} so'm</b>\n"
        f"🚘 Mashina: <b>{driver.car_model} • {driver.car_color}</b>\n"
        f"🧳 Tom bagaj: <b>{trip.roof_luggage}</b>\n\n"
        f"👇 <b>Joy band qilish uchun tugmani bosing</b>"
    )


async def refresh_channel_trip(bot: Bot, trip_id: int) -> None:
    if not config.channel_id:
        return
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(DriverTrip, Driver)
            .join(Driver, DriverTrip.driver_id == Driver.id)
            .where(DriverTrip.id == trip_id)
        )
        row = result.first()
        if not row:
            return
        trip, driver = row
        text = format_channel_trip(trip, driver)
        should_show = trip.status == "active" and trip.available_seats > 0
        try:
            if should_show and trip.channel_message_id:
                await bot.edit_message_text(
                    text,
                    chat_id=config.channel_id,
                    message_id=trip.channel_message_id,
                    reply_markup=channel_trip_keyboard(trip.id, config.bot_username),
                    parse_mode="HTML",
                )
            elif should_show:
                channel_message = await bot.send_message(
                    config.channel_id,
                    text,
                    reply_markup=channel_trip_keyboard(trip.id, config.bot_username),
                    parse_mode="HTML",
                )
                async with database.SessionLocal() as update_session:
                    db_trip = await update_session.get(DriverTrip, trip.id)
                    if db_trip:
                        db_trip.channel_message_id = channel_message.message_id
                        await update_session.commit()
            elif trip.channel_message_id:
                await bot.delete_message(config.channel_id, trip.channel_message_id)
                async with database.SessionLocal() as update_session:
                    db_trip = await update_session.get(DriverTrip, trip.id)
                    if db_trip:
                        db_trip.channel_message_id = None
                        await update_session.commit()
        except Exception as exc:
            logging.warning("Kanal e'loni yangilanmadi: %s", exc)


async def close_order_messages(bot: Bot, order_id: int, accepted_driver_user_id: int | None = None) -> None:
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(OrderMessage).where(OrderMessage.order_id == order_id).where(OrderMessage.status == "sent")
        )
        messages = result.scalars().all()
        for item in messages:
            item.status = "accepted" if item.driver_user_id == accepted_driver_user_id else "closed"
        await session.commit()

    for item in messages:
        try:
            if item.driver_user_id == accepted_driver_user_id:
                await bot.edit_message_text(
                    "✅ Buyurtmani siz qabul qildingiz.",
                    chat_id=item.chat_id,
                    message_id=item.message_id,
                )
            else:
                await bot.edit_message_text(
                    "⛔ Bu buyurtma boshqa haydovchi tomonidan qabul qilindi.",
                    chat_id=item.chat_id,
                    message_id=item.message_id,
                )
        except Exception as exc:
            logging.warning("Buyurtma xabari yangilanmadi: %s", exc)


async def broadcast_order_to_drivers(bot: Bot, order_id: int, exclude_driver_id: int | None = None) -> int:
    async with database.SessionLocal() as session:
        order = await session.get(Order, order_id)
        if not order or order.status != "searching_driver":
            return 0
        location_result = await session.execute(select(OrderLocation).where(OrderLocation.order_id == order.id))
        location = location_result.scalar_one_or_none()
        trips_query = (
            select(DriverTrip, Driver, User)
            .join(Driver, DriverTrip.driver_id == Driver.id)
            .join(User, Driver.user_id == User.id)
            .where(Driver.status == "active")
            .where(DriverTrip.status == "active")
            .where(DriverTrip.from_city == order.from_city)
            .where(DriverTrip.to_city == order.to_city)
            .where(DriverTrip.date == order.date)
            .where(time_match_condition(order.time))
            .where(DriverTrip.available_seats >= order.passengers_count)
        )
        if order.roof_luggage == "Ha":
            trips_query = trips_query.where(DriverTrip.roof_luggage == "Ha")
        if exclude_driver_id:
            trips_query = trips_query.where(Driver.id != exclude_driver_id)
        trips = await session.execute(trips_query)
        rows = trips.all()
        matched = []
        seen_driver_users = set()
        for row in rows:
            driver_user = row[2]
            if driver_user.id in seen_driver_users:
                continue
            seen_driver_users.add(driver_user.id)
            matched.append(row)

    text = format_order_for_driver(order, location)
    sent_count = 0
    for _trip, _driver, driver_user in matched:
        sent_message = await bot.send_message(
            driver_user.telegram_id,
            text,
            reply_markup=order_keyboard(order.id),
        )
        async with database.SessionLocal() as session:
            session.add(
                OrderMessage(
                    order_id=order.id,
                    driver_user_id=driver_user.id,
                    chat_id=driver_user.telegram_id,
                    message_id=sent_message.message_id,
                )
            )
            await session.commit()
        if location:
            await bot.send_location(driver_user.telegram_id, location.latitude, location.longitude)
        sent_count += 1
    return sent_count


async def restore_order_channel_post(bot: Bot, order_id: int) -> None:
    if not config.channel_id:
        return
    async with database.SessionLocal() as session:
        order = await session.get(Order, order_id)
        if not order or order.status != "searching_driver":
            return
        text = format_order_for_channel(order)
        try:
            if order.channel_message_id:
                await bot.edit_message_text(
                    text,
                    chat_id=config.channel_id,
                    message_id=order.channel_message_id,
                    reply_markup=channel_order_keyboard(order.id, config.bot_username),
                    parse_mode="HTML",
                )
            else:
                channel_message = await bot.send_message(
                    config.channel_id,
                    text,
                    reply_markup=channel_order_keyboard(order.id, config.bot_username),
                    parse_mode="HTML",
                )
                order.channel_message_id = channel_message.message_id
                await session.commit()
        except Exception as exc:
            logging.warning("Buyurtma kanalga qayta tiklanmadi: %s", exc)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, command: CommandObject | None = None) -> None:
    await state.clear()
    user = await get_or_create_user(message)
    if command and command.args and command.args.startswith("trip_"):
        trip_id = clean_int(command.args)
        if trip_id:
            await show_trip_to_passenger(message, trip_id)
            return
    if command and command.args and command.args.startswith("order_"):
        order_id = clean_int(command.args)
        if order_id:
            await show_channel_order_to_driver(message, order_id)
            return
    if not user.language:
        await message.answer(intro_text())
        await message.answer("Tilni tanlang / Выберите язык:", reply_markup=language_keyboard())
        return
    lang = user.language if user.language in {"uz", "ru"} else "uz"
    if await answer_existing_driver_status(message, message.from_user.id, lang):
        return
    if lang == "ru":
        text = f"{intro_text(lang)}\n\nВыберите нужный раздел:"
    else:
        text = f"{intro_text(lang)}\n\nKerakli bo'limni tanlang:"
    await message.answer(
        text,
        reply_markup=main_menu(is_admin(message.from_user.id), lang),
    )


async def show_trip_to_passenger(message: Message, trip_id: int) -> None:
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(DriverTrip, Driver)
            .join(Driver, DriverTrip.driver_id == Driver.id)
            .where(DriverTrip.id == trip_id)
            .where(DriverTrip.status == "active")
        )
        row = result.first()
    if not row:
        lang = await get_user_language(message.from_user.id)
        await message.answer(
            "Bu yo'nalish topilmadi yoki joy qolmagan."
            if lang == "uz"
            else "Этот маршрут не найден или мест уже нет.",
            reply_markup=main_menu(is_admin(message.from_user.id), lang),
        )
        return
    trip, driver = row
    await message.answer(
        format_trip_for_passenger(trip, driver),
        reply_markup=trip_select_keyboard(trip.id),
    )


async def show_channel_order_to_driver(message: Message, order_id: int) -> None:
    async with database.SessionLocal() as session:
        driver_row = await session.execute(
            select(Driver, User)
            .join(User, Driver.user_id == User.id)
            .where(User.telegram_id == message.from_user.id)
            .where(Driver.status == "active")
        )
        driver_match = driver_row.first()
        if not driver_match:
            lang = await get_user_language(message.from_user.id)
            await message.answer(
                "Bu buyurtmani qabul qilish uchun avval haydovchi sifatida ro'yxatdan o'ting va tasdiqlaning."
                if lang == "uz"
                else "Чтобы принять заказ, сначала зарегистрируйтесь как водитель.",
                reply_markup=main_menu(is_admin(message.from_user.id), lang),
            )
            return
        order = await session.get(Order, order_id)
        if not order or order.status != "searching_driver":
            await message.answer(
                "⛔ Bu buyurtma allaqachon band qilingan yoki bekor qilingan.",
                reply_markup=driver_menu(),
            )
            return
        location_result = await session.execute(select(OrderLocation).where(OrderLocation.order_id == order.id))
        location = location_result.scalar_one_or_none()
    await message.answer(
        format_order_for_driver(order, location),
        reply_markup=order_keyboard(order.id),
    )
    if location:
        await message.answer_location(location.latitude, location.longitude)


@router.message(F.text.in_({"Til", "🌐 Til", "Язык", "🌐 Язык"}))
async def choose_language(message: Message) -> None:
    await get_or_create_user(message)
    await message.answer("Tilni tanlang / Выберите язык:", reply_markup=language_keyboard())


@router.callback_query(F.data.startswith("lang:"))
async def set_language(callback: CallbackQuery) -> None:
    lang = callback.data.split(":", 1)[1]
    if lang not in {"uz", "ru"}:
        await callback.answer("Noto'g'ri til.", show_alert=True)
        return
    async with database.SessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
        user = result.scalar_one_or_none()
        if not user:
            full_name = callback.from_user.full_name or callback.from_user.username or "Foydalanuvchi"
            user = User(telegram_id=callback.from_user.id, full_name=full_name)
            session.add(user)
        user.language = lang
        await session.commit()
    if await answer_existing_driver_status(callback.message, callback.from_user.id, lang):
        await callback.answer("OK")
        return
    text = "Til saqlandi. Asosiy menyu:" if lang == "uz" else "Язык сохранен. Главное меню:"
    await callback.message.answer(text, reply_markup=main_menu(is_admin(callback.from_user.id), lang))
    await callback.answer("OK")


@router.message(F.text == "Yordam")
@router.message(F.text == "☎️ Yordam")
@router.message(F.text == "Помощь")
@router.message(F.text == "☎️ Помощь")
async def help_message(message: Message) -> None:
    lang = await get_user_language(message.from_user.id)
    if lang == "ru":
        text = (
            f"{config.bot_name} работает в тестовом режиме.\n\n"
            "Водители бесплатно размещают маршруты.\n"
            "Пассажиры выбирают подходящего водителя через бот.\n\n"
            "Если возникнет проблема, напишите админу:"
        )
    else:
        text = (
            f"{config.bot_name} test rejimida.\n\n"
            "Haydovchilar yo'nalishini bepul joylaydi.\n"
            "Yo'lovchilar narxi va vaqti ma'qul haydovchini bot orqali tanlaydi.\n\n"
            "Muammo bo'lsa admin bilan bog'laning:"
        )
    await message.answer(text, reply_markup=admin_contacts_keyboard(config.admin_ids, lang))


@router.callback_query(F.data == "check_sub")
async def check_subscription(callback: CallbackQuery, bot: Bot) -> None:
    if await is_channel_member(bot, callback.from_user.id):
        lang = await get_user_language(callback.from_user.id)
        await callback.answer("A'zolik tasdiqlandi." if lang == "uz" else "Подписка подтверждена.")
        await callback.message.answer(
            "Rahmat! Endi botdan foydalanishingiz mumkin." if lang == "uz" else "Спасибо! Теперь можно пользоваться ботом.",
            reply_markup=main_menu(is_admin(callback.from_user.id), lang),
        )
    else:
        await callback.answer("Hali kanalga a'zo emassiz.", show_alert=True)


@router.message(F.text == BACK_BUTTON)
@router.message(F.text == "⬅️ Назад")
@router.message(F.text == "Asosiy menyu")
@router.message(F.text == "Главное меню")
async def back_to_main_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    lang = await get_user_language(message.from_user.id)
    await message.answer(
        "Jarayon bekor qilindi. Asosiy menyu:" if lang == "uz" else "Действие отменено. Главное меню:",
        reply_markup=main_menu(is_admin(message.from_user.id), lang),
    )


@router.message(F.text == "Yo'lovchiman")
@router.message(F.text == "🚕 Yo'lovchiman")
@router.message(F.text == "Я пассажир")
@router.message(F.text == "🚕 Я пассажир")
async def passenger_start(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message)
    lang = await get_user_language(message.from_user.id)
    await state.clear()
    await state.update_data(lang=lang)
    async with database.SessionLocal() as session:
        db_user = await session.get(User, user.id)
        db_user.role = "passenger"
        await session.commit()

    if user.phone:
        await state.set_state(PassengerOrder.from_city)
        await message.answer("Qaysi shahardan ketasiz?" if lang == "uz" else "Из какого города выезжаете?", reply_markup=city_keyboard(lang))
    else:
        await state.set_state(PassengerOrder.phone)
        await message.answer("Telefon raqamingizni yuboring:" if lang == "uz" else "Отправьте номер телефона:", reply_markup=phone_keyboard(lang))


@router.message(PassengerOrder.phone)
async def passenger_phone(message: Message, state: FSMContext) -> None:
    phone = message.contact.phone_number if message.contact else message.text
    async with database.SessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = result.scalar_one()
        user.phone = phone
        user.role = "passenger"
        await session.commit()

    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    if data.get("prefill_trip_id"):
        await state.set_state(PassengerOrder.location)
        await message.answer("Aniq olib ketish lokatsiyangizni yuboring:" if lang == "uz" else "Отправьте точную локацию посадки:", reply_markup=location_keyboard(lang))
        return
    await state.set_state(PassengerOrder.from_city)
    await message.answer("Qaysi shahardan ketasiz?" if lang == "uz" else "Из какого города выезжаете?", reply_markup=city_keyboard(lang))


@router.message(PassengerOrder.from_city)
async def passenger_from_city(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    if needs_district(message.text):
        await state.update_data(from_city_base=message.text)
        await state.set_state(PassengerOrder.from_district)
        await message.answer(
            "Qaysi tumandan ketasiz?" if lang == "uz" else "Из какого района выезжаете?",
            reply_markup=district_keyboard(message.text, lang),
        )
        return
    await state.update_data(from_city=message.text, from_city_base=message.text)
    await state.set_state(PassengerOrder.to_city)
    await message.answer("Qayerga borasiz?" if lang == "uz" else "В какой город едете?", reply_markup=city_keyboard(lang))


@router.message(PassengerOrder.from_district)
async def passenger_from_district(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    city = data.get("from_city_base", "Andijon")
    await state.update_data(from_city=place_with_district(city, message.text))
    await state.set_state(PassengerOrder.to_city)
    await message.answer("Qayerga borasiz?" if lang == "uz" else "В какой город едете?", reply_markup=city_keyboard(lang))


@router.message(PassengerOrder.to_city)
async def passenger_to_city(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    if message.text == data.get("from_city") or (
        not needs_district(message.text) and message.text == data.get("from_city_base")
    ):
        await message.answer("Boradigan shahar ketadigan shahar bilan bir xil bo'lmasin." if lang == "uz" else "Город назначения не должен совпадать с городом отправления.")
        return
    if needs_district(message.text):
        await state.update_data(to_city_base=message.text)
        await state.set_state(PassengerOrder.to_district)
        await message.answer(
            "Qaysi tumanga borasiz?" if lang == "uz" else "В какой район едете?",
            reply_markup=district_keyboard(message.text, lang),
        )
        return
    await state.update_data(to_city=message.text, to_city_base=message.text)
    await state.set_state(PassengerOrder.location)
    await message.answer("Endi aniq olib ketish lokatsiyangizni yuboring:" if lang == "uz" else "Теперь отправьте точную локацию посадки:", reply_markup=location_keyboard(lang))


@router.message(PassengerOrder.to_district)
async def passenger_to_district(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    city = data.get("to_city_base", "Andijon")
    to_city = place_with_district(city, message.text)
    if to_city == data.get("from_city"):
        await message.answer(
            "Boradigan tuman ketadigan tuman bilan bir xil bo'lmasin."
            if lang == "uz"
            else "Район назначения не должен совпадать с районом отправления."
        )
        return
    await state.update_data(to_city=to_city)
    await state.set_state(PassengerOrder.location)
    await message.answer("Endi aniq olib ketish lokatsiyangizni yuboring:" if lang == "uz" else "Теперь отправьте точную локацию посадки:", reply_markup=location_keyboard(lang))


@router.message(PassengerOrder.location)
async def passenger_location(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    if not message.location:
        await message.answer("Iltimos, tugma orqali lokatsiyani yuboring." if lang == "uz" else "Пожалуйста, отправьте локацию через кнопку.", reply_markup=location_keyboard(lang))
        return
    await state.update_data(
        latitude=message.location.latitude,
        longitude=message.location.longitude,
    )
    if data.get("prefill_trip_id"):
        await state.set_state(PassengerOrder.passengers_count)
        await message.answer("Nechta yo'lovchi?" if lang == "uz" else "Сколько пассажиров?")
        return
    await state.set_state(PassengerOrder.date)
    await message.answer("Qaysi sana ketasiz?" if lang == "uz" else "На какую дату поездка?", reply_markup=date_keyboard(lang))


@router.message(PassengerOrder.date)
async def passenger_date(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    await state.update_data(date=message.text)
    await state.set_state(PassengerOrder.time)
    await message.answer("Soat nechida ketasiz?" if lang == "uz" else "Во сколько выезжаете?", reply_markup=time_keyboard(lang))


@router.message(PassengerOrder.time)
async def passenger_time(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    await state.update_data(time=message.text)
    await state.set_state(PassengerOrder.passengers_count)
    await message.answer("Nechta yo'lovchi?" if lang == "uz" else "Сколько пассажиров?")


@router.message(PassengerOrder.passengers_count)
async def passenger_count(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    count = clean_int(message.text)
    if not count or count < 1:
        await message.answer("Yo'lovchi sonini raqam bilan kiriting. Masalan: 2" if lang == "uz" else "Введите количество пассажиров цифрой. Например: 2")
        return
    await state.update_data(passengers_count=count)
    await state.set_state(PassengerOrder.roof_luggage)
    await message.answer("Tom bagaj kerakmi?" if lang == "uz" else "Нужен багажник на крыше?", reply_markup=yes_no_keyboard(lang))


@router.message(PassengerOrder.roof_luggage)
async def passenger_roof_luggage(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    if not is_yes(message.text) and not is_no(message.text):
        await message.answer("Iltimos, Ha yoki Yo'q tugmasini tanlang." if lang == "uz" else "Пожалуйста, выберите Да или Нет.", reply_markup=yes_no_keyboard(lang))
        return
    await state.update_data(roof_luggage="Ha" if is_yes(message.text) else "Yo'q")
    if data.get("prefill_trip_id"):
        await state.update_data(max_price=None)
        await state.set_state(PassengerOrder.comment)
        await message.answer("Qo'shimcha izoh bormi?" if lang == "uz" else "Есть дополнительный комментарий?", reply_markup=skip_keyboard(lang))
        return
    await state.set_state(PassengerOrder.max_price)
    await message.answer("Sizga maksimal qaysi narx ma'qul?" if lang == "uz" else "Какая максимальная цена вам подходит?", reply_markup=max_price_keyboard(lang))


@router.message(PassengerOrder.max_price)
async def passenger_max_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    if is_any_price(message.text):
        await state.update_data(max_price=None)
    else:
        price = clean_int(message.text)
        if not price or price < 180000 or price > 300000:
            await message.answer("Narxni tugmalardan tanlang." if lang == "uz" else "Выберите цену кнопкой.", reply_markup=max_price_keyboard(lang))
            return
        await state.update_data(max_price=price)
    await state.set_state(PassengerOrder.comment)
    await message.answer("Qo'shimcha izoh bormi?" if lang == "uz" else "Есть дополнительный комментарий?", reply_markup=skip_keyboard(lang))


@router.message(PassengerOrder.comment)
async def passenger_comment(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    comment = None if is_skip_comment(message.text) else message.text

    async with database.SessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = result.scalar_one()
        order = Order(
            passenger_id=user.id,
            from_city=data["from_city"],
            to_city=data["to_city"],
            date=data["date"],
            time=data["time"],
            passengers_count=data["passengers_count"],
            price_per_person=data["max_price"],
            roof_luggage=data["roof_luggage"],
            comment=comment,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        location = OrderLocation(
            order_id=order.id,
            latitude=data["latitude"],
            longitude=data["longitude"],
        )
        session.add(location)
        await session.commit()

        if data.get("prefill_trip_id"):
            trip_result = await session.execute(
                select(DriverTrip, Driver, User)
                .join(Driver, DriverTrip.driver_id == Driver.id)
                .join(User, Driver.user_id == User.id)
                .where(DriverTrip.id == data["prefill_trip_id"])
                .where(DriverTrip.status == "active")
                .where(Driver.status == "active")
            )
            selected = trip_result.first()
            if selected:
                selected_trip, selected_driver, selected_driver_user = selected
                if (
                    selected_trip.available_seats >= order.passengers_count
                    and (order.roof_luggage != "Ha" or selected_trip.roof_luggage == "Ha")
                ):
                    order.driver_id = selected_driver.id
                    order.status = "accepted"
                    selected_trip.available_seats -= order.passengers_count
                    if selected_trip.available_seats <= 0:
                        selected_trip.status = "full"
                    await session.commit()
                else:
                    selected = None

            await state.clear()
            if not selected:
                lang = data.get("lang", await get_user_language(message.from_user.id))
                await message.answer(
                    "Tanlangan haydovchida joy qolmagan yoki shartlar mos kelmadi."
                    if lang == "uz"
                    else "У выбранного водителя не осталось мест или условия не совпали.",
                    reply_markup=main_menu(is_admin(message.from_user.id), lang),
                )
                return

            passenger_text = (
                "✅ Haydovchi tanlandi!\n\n"
                f"Ism: {selected_driver_user.full_name}\n"
                f"Telefon: {selected_driver_user.phone}\n"
                f"Mashina: {selected_driver.car_model} {selected_driver.car_color}\n"
                f"Raqam: {selected_driver.car_number}\n"
                f"Narx: {selected_trip.price_per_person:,} so'm\n"
                f"Tom bagaj: {selected_trip.roof_luggage}"
            ).replace(",", " ")
            driver_text = (
                "✅ Yo'lovchi sizni tanladi.\n\n"
                f"Yo'lovchi: {user.full_name}\n"
                f"Telefon: {user.phone}\n"
                f"Yo'nalish: {order.from_city} -> {order.to_city}\n"
                f"Sana/vaqt: {order.date} {order.time}\n"
                f"Yo'lovchi soni: {order.passengers_count}\n"
                f"Tom bagaj kerak: {order.roof_luggage or '-'}"
            )
            await message.answer(passenger_text, reply_markup=main_menu(is_admin(message.from_user.id), data.get("lang", "uz")))
            await bot.send_message(
                selected_driver_user.telegram_id,
                driver_text,
                reply_markup=accepted_order_keyboard(order.id),
            )
            await bot.send_location(selected_driver_user.telegram_id, location.latitude, location.longitude)
            await refresh_channel_trip(bot, selected_trip.id)
            return

        trips_query = (
            select(DriverTrip, Driver, User)
            .join(Driver, DriverTrip.driver_id == Driver.id)
            .join(User, Driver.user_id == User.id)
            .where(Driver.status == "active")
            .where(DriverTrip.status == "active")
            .where(DriverTrip.from_city == order.from_city)
            .where(DriverTrip.to_city == order.to_city)
            .where(DriverTrip.date == order.date)
            .where(time_match_condition(order.time))
            .where(DriverTrip.available_seats >= order.passengers_count)
        )
        if order.roof_luggage == "Ha":
            trips_query = trips_query.where(DriverTrip.roof_luggage == "Ha")
        trips = await session.execute(trips_query)
        rows = trips.all()
        matched = []
        seen_driver_users = set()
        for row in rows:
            driver_user = row[2]
            if driver_user.id in seen_driver_users:
                continue
            seen_driver_users.add(driver_user.id)
            matched.append(row)
        passenger_matched = [
            row
            for row in matched
            if not order.price_per_person or row[0].price_per_person <= order.price_per_person
        ]

    text = format_order_for_driver(order, location)
    for _trip, _driver, driver_user in matched:
        sent_message = await bot.send_message(
            driver_user.telegram_id,
            text,
            reply_markup=order_keyboard(order.id),
        )
        async with database.SessionLocal() as session:
            session.add(
                OrderMessage(
                    order_id=order.id,
                    driver_user_id=driver_user.id,
                    chat_id=driver_user.telegram_id,
                    message_id=sent_message.message_id,
                )
            )
            await session.commit()
        await bot.send_location(driver_user.telegram_id, location.latitude, location.longitude)

    await state.clear()
    if passenger_matched:
        await message.answer(f"{len(passenger_matched)} ta narxingizga mos haydovchi topildi. O'zingizga ma'qulini tanlang:")
        for trip, driver, _driver_user in passenger_matched:
            await message.answer(format_trip_for_passenger(trip, driver), reply_markup=trip_select_keyboard(trip.id))
    else:
        if config.channel_id:
            try:
                channel_message = await bot.send_message(
                    config.channel_id,
                    format_order_for_channel(order),
                    reply_markup=channel_order_keyboard(order.id, config.bot_username),
                    parse_mode="HTML",
                )
                async with database.SessionLocal() as session:
                    db_order = await session.get(Order, order.id)
                    if db_order:
                        db_order.channel_message_id = channel_message.message_id
                        await session.commit()
            except Exception as exc:
                logging.warning("Buyurtma kanalga yuborilmadi: %s", exc)
        await message.answer(
            "Buyurtmangiz haydovchilarga yuborildi.\n"
            "Hozircha siz tanlagan maksimal narxga mos haydovchi topilmadi. "
            "E'loningiz tezroq haydovchiga yetishi uchun kanalga ham joylashtirildi."
        )
    await message.answer(
        "Asosiy menyu" if data.get("lang", "uz") == "uz" else "Главное меню",
        reply_markup=main_menu(is_admin(message.from_user.id), data.get("lang", "uz")),
    )


@router.message(F.text == "Haydovchiman")
@router.message(F.text == "🚘 Haydovchiman")
@router.message(F.text == "Я водитель")
@router.message(F.text == "🚘 Я водитель")
async def driver_start(message: Message, state: FSMContext) -> None:
    await get_or_create_user(message)
    lang = await get_user_language(message.from_user.id)
    await state.clear()
    await state.update_data(lang=lang)

    async with database.SessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = result.scalar_one()
        user.role = "driver"
        driver_result = await session.execute(select(Driver).where(Driver.user_id == user.id))
        driver = driver_result.scalar_one_or_none()
        await session.commit()

    if driver and driver.status == "active":
        if lang == "ru":
            text = (
                "Вы подтверждены как водитель.\n"
                "Добавление маршрута пока бесплатно.\n"
                "Ваше объявление выйдет в канал, пассажиры выберут вас через бот."
            )
        else:
            text = (
                "Siz haydovchi sifatida tasdiqlangansiz.\n"
                "Yo'nalish qo'shish hozircha bepul.\n"
                "E'loningiz kanalga chiqadi va yo'lovchilar bot orqali sizni tanlaydi."
            )
        await message.answer(text, reply_markup=driver_menu(lang))
        return
    if driver and driver.status == "pending":
        await message.answer("Arizangiz adminga yuborilgan. Tasdiqlanishini kuting." if lang == "uz" else "Ваша заявка отправлена админу. Ожидайте подтверждения.")
        return

    await state.set_state(DriverRegister.phone)
    text = (
        "Haydovchilar uchun test davrida e'lon joylash bepul.\n\n"
        "Telefon raqamingizni yuboring:"
        if lang == "uz"
        else "Для водителей размещение объявлений в тестовый период бесплатно.\n\nОтправьте номер телефона:"
    )
    await message.answer(text, reply_markup=phone_keyboard(lang))


def driver_menu(lang: str = "uz") -> ReplyKeyboardMarkup:
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    if lang == "ru":
        keyboard = [
            [KeyboardButton(text="Добавить маршрут")],
            [KeyboardButton(text="Подходящие заказы"), KeyboardButton(text="Мои маршруты")],
            [KeyboardButton(text="Мои заказы")],
            [KeyboardButton(text="Главное меню")],
        ]
    else:
        keyboard = [
            [KeyboardButton(text="Yo'nalish qo'shish")],
            [KeyboardButton(text="Mos buyurtmalar"), KeyboardButton(text="Mening yo'nalishlarim")],
            [KeyboardButton(text="Buyurtmalarim")],
            [KeyboardButton(text="Asosiy menyu")],
        ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
    )


@router.message(DriverRegister.phone)
async def driver_phone(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    phone = message.contact.phone_number if message.contact else message.text
    await state.update_data(phone=phone)
    await state.set_state(DriverRegister.full_name)
    await message.answer("Ism-familiyangizni kiriting:" if lang == "uz" else "Введите имя и фамилию:")


@router.message(DriverRegister.full_name)
async def driver_full_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    await state.update_data(full_name=message.text)
    await state.set_state(DriverRegister.car_model)
    await message.answer("Mashina modeli? Masalan: Cobalt" if lang == "uz" else "Модель машины? Например: Cobalt")


@router.message(DriverRegister.car_model)
async def driver_car_model(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    await state.update_data(car_model=message.text)
    await state.set_state(DriverRegister.car_color)
    await message.answer("Mashina rangi? Masalan: oq" if lang == "uz" else "Цвет машины? Например: белый")


@router.message(DriverRegister.car_color)
async def driver_car_color(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    await state.update_data(car_color=message.text)
    await state.set_state(DriverRegister.car_number)
    await message.answer("Davlat raqami? Masalan: 01 A 123 BC" if lang == "uz" else "Госномер? Например: 01 A 123 BC")


@router.message(DriverRegister.car_number)
async def driver_car_number(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    await state.update_data(car_number=message.text)
    await state.set_state(DriverRegister.seats_count)
    await message.answer(
        "Nechta bo'sh joy bilan ishlaysiz? Masalan: 4"
        if lang == "uz"
        else "Сколько свободных мест у вас есть? Например: 4"
    )


@router.message(DriverRegister.seats_count)
async def driver_seats(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    seats = clean_int(message.text)
    if not seats or seats < 1:
        await message.answer(
            "O'rindiqlar sonini raqam bilan kiriting."
            if lang == "uz"
            else "Введите количество мест цифрой."
        )
        return

    await state.update_data(seats_count=seats)
    await state.set_state(DriverRegister.car_front_photo)
    await message.answer(
        "Mashinaning OLD tomonidan rasmini yuboring."
        if lang == "uz"
        else "Отправьте фото машины спереди."
    )


@router.message(DriverRegister.car_front_photo)
async def driver_front_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    if not message.photo:
        await message.answer(
            "Iltimos, mashinaning OLD rasmini foto qilib yuboring."
            if lang == "uz"
            else "Пожалуйста, отправьте фото машины спереди."
        )
        return
    await state.update_data(car_front_photo=message.photo[-1].file_id)
    await state.set_state(DriverRegister.car_back_photo)
    await message.answer(
        "Endi mashinaning ORQA tomonidan rasmini yuboring."
        if lang == "uz"
        else "Теперь отправьте фото машины сзади."
    )


@router.message(DriverRegister.car_back_photo)
async def driver_back_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    if not message.photo:
        await message.answer(
            "Iltimos, mashinaning ORQA rasmini foto qilib yuboring."
            if lang == "uz"
            else "Пожалуйста, отправьте фото машины сзади."
        )
        return
    await state.update_data(car_back_photo=message.photo[-1].file_id)
    await state.set_state(DriverRegister.car_side_photo)
    await message.answer(
        "Endi mashinaning YON tomonidan rasmini yuboring."
        if lang == "uz"
        else "Теперь отправьте фото машины сбоку."
    )


@router.message(DriverRegister.car_side_photo)
async def driver_side_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    if not message.photo:
        await message.answer(
            "Iltimos, mashinaning YON rasmini foto qilib yuboring."
            if lang == "uz"
            else "Пожалуйста, отправьте фото машины сбоку."
        )
        return
    await state.update_data(car_side_photo=message.photo[-1].file_id)
    await state.set_state(DriverRegister.driver_license_photo)
    await message.answer(
        "Endi haydovchilik guvohnomangiz (prava) rasmini yuboring."
        if lang == "uz"
        else "Теперь отправьте фото водительского удостоверения."
    )


@router.message(DriverRegister.driver_license_photo)
async def driver_license_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    if not message.photo:
        await message.answer(
            "Iltimos, haydovchilik guvohnomangiz (prava) rasmini yuboring."
            if lang == "uz"
            else "Пожалуйста, отправьте фото водительского удостоверения."
        )
        return
    await state.update_data(driver_license_photo=message.photo[-1].file_id)
    await state.set_state(DriverRegister.tech_passport_photo)
    await message.answer(
        "Endi avtomobil tex passporti rasmini yuboring."
        if lang == "uz"
        else "Теперь отправьте фото техпаспорта автомобиля."
    )


@router.message(DriverRegister.tech_passport_photo)
async def driver_tech_passport_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    lang = data.get("lang", "uz")
    if not message.photo:
        await message.answer(
            "Iltimos, avtomobil tex passporti rasmini yuboring."
            if lang == "uz"
            else "Пожалуйста, отправьте фото техпаспорта автомобиля."
        )
        return
    await state.update_data(tech_passport_photo=message.photo[-1].file_id)
    data = await state.get_data()
    async with database.SessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = result.scalar_one()
        user.phone = data["phone"]
        user.full_name = data["full_name"]
        user.role = "driver"
        driver_result = await session.execute(select(Driver).where(Driver.user_id == user.id))
        driver = driver_result.scalar_one_or_none()
        if driver:
            driver.car_model = data["car_model"]
            driver.car_color = data["car_color"]
            driver.car_number = data["car_number"]
            driver.seats_count = data["seats_count"]
            driver.status = "pending"
            await session.execute(delete(DriverPhoto).where(DriverPhoto.driver_id == driver.id))
        else:
            driver = Driver(
                user_id=user.id,
                car_model=data["car_model"],
                car_color=data["car_color"],
                car_number=data["car_number"],
                seats_count=data["seats_count"],
                status="pending",
            )
            session.add(driver)
        await session.commit()
        await session.refresh(driver)
        session.add_all(
            [
                DriverPhoto(driver_id=driver.id, photo_type="front", file_id=data["car_front_photo"]),
                DriverPhoto(driver_id=driver.id, photo_type="back", file_id=data["car_back_photo"]),
                DriverPhoto(driver_id=driver.id, photo_type="side", file_id=data["car_side_photo"]),
                DriverPhoto(driver_id=driver.id, photo_type="driver_license", file_id=data["driver_license_photo"]),
                DriverPhoto(driver_id=driver.id, photo_type="tech_passport", file_id=data["tech_passport_photo"]),
            ]
        )
        await session.commit()

    admin_text = (
        f"✅ Yangi haydovchi #{driver.id}\n\n"
        f"👤 Ism: {data['full_name']}\n"
        f"📞 Telefon: {data['phone']}\n"
        f"🚗 Mashina: {data['car_model']} {data['car_color']}\n"
        f"🔢 Raqam: {data['car_number']}\n"
        f"💺 O'rindiq: {data['seats_count']}\n\n"
        f"Quyida fotolar keladi. Tasdiqlash yoki rad etish uchun tugmalardan foydalaning."
    )
    photos_to_send = [
        (data.get("car_front_photo"), "🚗 Mashina oldi"),
        (data.get("car_back_photo"), "🚗 Mashina orqasi"),
        (data.get("car_side_photo"), "🚗 Mashina yon tomoni"),
        (data.get("driver_license_photo"), "📄 Haydovchilik guvohnomasi (prava)"),
        (data.get("tech_passport_photo"), "📄 Avtomobil tex passporti"),
    ]
    for admin_id in config.admin_ids:
        logging.info("Adminga yuborilmoqda: admin_id=%s", admin_id)
        for file_id, caption in photos_to_send:
            if not file_id:
                logging.warning("Fayl topilmadi: caption=%s", caption)
                continue
            try:
                await bot.send_photo(admin_id, file_id, caption=caption)
            except Exception as exc:
                logging.error("Rasm yuborilmadi admin_id=%s caption=%s error=%s", admin_id, caption, exc)
        try:
            await bot.send_message(admin_id, admin_text, reply_markup=admin_driver_keyboard(driver.id))
            logging.info("Admin xabari yuborildi: admin_id=%s driver_id=%s", admin_id, driver.id)
        except Exception as exc:
            logging.error("Admin xabari yuborilmadi: admin_id=%s error=%s", admin_id, exc)

    await state.clear()
    await message.answer("Arizangiz adminga yuborildi. Tasdiqlanishini kuting.")


@router.message(F.text == "Yo'nalish qo'shish")
@router.message(F.text == "Добавить маршрут")
async def trip_start(message: Message, state: FSMContext) -> None:
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(Driver, User)
            .join(User, Driver.user_id == User.id)
            .where(User.telegram_id == message.from_user.id)
        )
        row = result.first()
    if not row or row[0].status != "active":
        await message.answer("Avval admin sizni haydovchi sifatida tasdiqlashi kerak.")
        return

    await state.set_state(DriverTripCreate.from_city)
    await message.answer("Qayerdan ketasiz?", reply_markup=city_keyboard())


@router.message(DriverTripCreate.from_city)
async def trip_from_city(message: Message, state: FSMContext) -> None:
    if needs_district(message.text):
        await state.update_data(from_city_base=message.text)
        await state.set_state(DriverTripCreate.from_district)
        await message.answer("Qaysi tumandan ketasiz?", reply_markup=district_keyboard(message.text))
        return
    await state.update_data(from_city=message.text, from_city_base=message.text)
    await state.set_state(DriverTripCreate.to_city)
    await message.answer("Qayerga borasiz?", reply_markup=city_keyboard())


@router.message(DriverTripCreate.from_district)
async def trip_from_district(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    city = data.get("from_city_base", "Andijon")
    await state.update_data(from_city=place_with_district(city, message.text))
    await state.set_state(DriverTripCreate.to_city)
    await message.answer("Qayerga borasiz?", reply_markup=city_keyboard())


@router.message(DriverTripCreate.to_city)
async def trip_to_city(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if message.text == data.get("from_city") or (
        not needs_district(message.text) and message.text == data.get("from_city_base")
    ):
        await message.answer("Boradigan shahar ketadigan shahar bilan bir xil bo'lmasin.")
        return
    if needs_district(message.text):
        await state.update_data(to_city_base=message.text)
        await state.set_state(DriverTripCreate.to_district)
        await message.answer("Qaysi tumanga borasiz?", reply_markup=district_keyboard(message.text))
        return
    await state.update_data(to_city=message.text, to_city_base=message.text)
    await state.set_state(DriverTripCreate.date)
    await message.answer("Qaysi sana ketasiz?", reply_markup=date_keyboard())


@router.message(DriverTripCreate.to_district)
async def trip_to_district(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    city = data.get("to_city_base", "Andijon")
    to_city = place_with_district(city, message.text)
    if to_city == data.get("from_city"):
        await message.answer("Boradigan tuman ketadigan tuman bilan bir xil bo'lmasin.")
        return
    await state.update_data(to_city=to_city)
    await state.set_state(DriverTripCreate.date)
    await message.answer("Qaysi sana ketasiz?", reply_markup=date_keyboard())


@router.message(DriverTripCreate.date)
async def trip_date(message: Message, state: FSMContext) -> None:
    await state.update_data(date=message.text)
    await state.set_state(DriverTripCreate.time)
    await message.answer("Soat nechida ketasiz?", reply_markup=time_keyboard())


@router.message(DriverTripCreate.time)
async def trip_time(message: Message, state: FSMContext) -> None:
    await state.update_data(time=message.text)
    await state.set_state(DriverTripCreate.available_seats)
    await message.answer("Bo'sh joy soni nechta?")


@router.message(DriverTripCreate.available_seats)
async def trip_seats(message: Message, state: FSMContext) -> None:
    seats = clean_int(message.text)
    if not seats or seats < 1:
        await message.answer("Bo'sh joy sonini raqam bilan kiriting.")
        return
    await state.update_data(available_seats=seats)
    await state.set_state(DriverTripCreate.price_per_person)
    await message.answer("Bir kishi uchun narxni tanlang:", reply_markup=price_keyboard())


@router.message(DriverTripCreate.price_per_person)
async def trip_price(message: Message, state: FSMContext) -> None:
    price = clean_int(message.text)
    if not price or price < 180000 or price > 300000:
        await message.answer("Narxni 180 000 - 300 000 so'm oralig'idagi tugmalardan tanlang.", reply_markup=price_keyboard())
        return
    await state.update_data(price_per_person=price)
    await state.set_state(DriverTripCreate.roof_luggage)
    await message.answer("Mashinada tom bagaj bormi?", reply_markup=yes_no_keyboard())


@router.message(DriverTripCreate.roof_luggage)
async def trip_roof_luggage(message: Message, state: FSMContext) -> None:
    if message.text not in ["Ha", "Yo'q"]:
        await message.answer("Iltimos, Ha yoki Yo'q tugmasini tanlang.", reply_markup=yes_no_keyboard())
        return
    await state.update_data(roof_luggage=message.text)
    await state.set_state(DriverTripCreate.comment)
    await message.answer("Qo'shimcha izoh bormi?", reply_markup=skip_keyboard())


@router.message(DriverTripCreate.comment)
async def trip_comment(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    comment = None if message.text == "Izoh yo'q" else message.text
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(Driver, User)
            .join(User, Driver.user_id == User.id)
            .where(User.telegram_id == message.from_user.id)
        )
        driver, _user = result.one()
        trip = DriverTrip(
            driver_id=driver.id,
            from_city=data["from_city"],
            to_city=data["to_city"],
            date=data["date"],
            time=data["time"],
            available_seats=data["available_seats"],
            price_per_person=data["price_per_person"],
            roof_luggage=data["roof_luggage"],
            comment=comment,
        )
        session.add(trip)
        await session.commit()
        await session.refresh(trip)

        orders_result = await session.execute(
            select(Order)
            .where(Order.status == "searching_driver")
            .where(Order.from_city == trip.from_city)
            .where(Order.to_city == trip.to_city)
            .where(Order.date == trip.date)
            .where(or_(Order.time == trip.time, Order.time == "⚡ Srochniy", trip.time == "⚡ Srochniy"))
            .where(Order.passengers_count <= trip.available_seats)
            .where((Order.roof_luggage == "Yo'q") | (Order.roof_luggage == trip.roof_luggage))
            .order_by(Order.id.desc())
            .limit(10)
        )
        matching_orders = orders_result.scalars().all()
        order_ids = [order.id for order in matching_orders]
        if order_ids:
            locations_result = await session.execute(select(OrderLocation).where(OrderLocation.order_id.in_(order_ids)))
            locations = {location.order_id: location for location in locations_result.scalars().all()}
        else:
            locations = {}

    await state.clear()
    await message.answer(
        f"Yo'nalish qo'shildi va kanalga yuborishga tayyorlandi.\n"
        f"Bu xizmat test davrida bepul.\n\n"
        f"Shu yo'nalish bo'yicha {len(matching_orders)} ta ochiq buyurtma topildi.",
        reply_markup=driver_menu(),
    )
    for order in matching_orders:
        await message.answer(format_order_for_driver(order, locations.get(order.id)), reply_markup=order_keyboard(order.id))

    if config.channel_id:
        try:
            channel_message = await bot.send_message(
                config.channel_id,
                format_channel_trip(trip, driver),
                reply_markup=channel_trip_keyboard(trip.id, config.bot_username),
                parse_mode="HTML",
            )
            async with database.SessionLocal() as session:
                db_trip = await session.get(DriverTrip, trip.id)
                if db_trip:
                    db_trip.channel_message_id = channel_message.message_id
                    await session.commit()
            await message.answer(f"E'lon kanalga yuborildi: {config.channel_id}")
        except Exception as exc:
            logging.warning("Kanalga e'lon yuborilmadi: %s", exc)
            await message.answer(
                "E'lon kanalga yuborilmadi.\n"
                "Bot kanalga admin qilinganini va Post Messages ruxsati borligini tekshiring."
            )
    else:
        await message.answer("Kanal sozlanmagan. CHANNEL_ID .env faylida ko'rsatilmagan.")


@router.callback_query(F.data.startswith("driver:"))
async def admin_driver_action(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Siz admin emassiz.", show_alert=True)
        return

    _prefix, action, raw_driver_id = callback.data.split(":")
    driver_id = int(raw_driver_id)

    if action in ("block", "unblock"):
        async with database.SessionLocal() as session:
            driver = await session.get(Driver, driver_id)
            if not driver:
                await callback.answer("Haydovchi topilmadi.", show_alert=True)
                return
            user = await session.get(User, driver.user_id)
            driver.status = "blocked" if action == "block" else "active"
            if action == "block":
                trips_result = await session.execute(
                    select(DriverTrip).where(DriverTrip.driver_id == driver.id).where(DriverTrip.status == "active")
                )
                for trip in trips_result.scalars().all():
                    trip.status = "blocked"
            await session.commit()
            new_status = driver.status

        status_text = "🚫 Bloklandi" if action == "block" else "✅ Blokdan chiqarildi"
        try:
            await callback.message.edit_text(callback.message.text + f"\n\nStatus: {status_text}")
            await callback.message.edit_reply_markup(reply_markup=admin_driver_manage_keyboard(driver_id, new_status))
        except Exception:
            pass
        try:
            if action == "block":
                await bot.send_message(user.telegram_id, "Sizning haydovchi profilingiz vaqtincha bloklandi. Savollar uchun admin bilan bog'laning.")
            else:
                await bot.send_message(user.telegram_id, "Sizning haydovchi profilingiz qayta faollashtirildi.")
        except Exception as exc:
            logging.warning("Haydovchiga xabar yuborilmadi: %s", exc)
        await callback.answer(status_text)
        return

    driver_id = int(raw_driver_id)
    async with database.SessionLocal() as session:
        driver = await session.get(Driver, driver_id)
        if not driver:
            await callback.answer("Haydovchi topilmadi.", show_alert=True)
            return
        # Agar allaqachon qaror qilingan bo'lsa — ikkinchi admin bosa olmaydi
        if driver.status in ("active", "blocked"):
            action_text = "✅ tasdiqlandi" if driver.status == "active" else "❌ rad etildi"
            await callback.answer(f"Bu haydovchi allaqachon {action_text}!", show_alert=True)
            return
        user = await session.get(User, driver.user_id)
        driver.status = "active" if action == "approve" else "blocked"
        await session.commit()

    action_text = "✅ tasdiqlandi" if action == "approve" else "❌ rad etildi"
    new_text = callback.message.text + f"\n\nStatus: {action_text} (admin: {callback.from_user.id})"

    if action == "approve":
        await bot.send_message(user.telegram_id, "Tabriklaymiz! Siz haydovchi sifatida tasdiqlandingiz.", reply_markup=driver_menu())
    else:
        await bot.send_message(user.telegram_id, "Haydovchilik arizangiz rad etildi.")

    for admin_id in config.admin_ids:
        if admin_id == callback.from_user.id:
            try:
                await callback.message.edit_text(new_text)
            except Exception:
                pass
        else:
            try:
                await bot.send_message(admin_id, f"ℹ️ Haydovchi #{driver_id} — {action_text} (boshqa admin tomonidan)")
            except Exception as exc:
                logging.warning("Admin %s ga xabar yuborilmadi: %s", admin_id, exc)

    await callback.answer("Bajarildi")


@router.callback_query(F.data.startswith("trip:select:"))
async def passenger_select_trip(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    trip_id = int(callback.data.split(":")[-1])
    async with database.SessionLocal() as session:
        user_result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
        passenger = user_result.scalar_one_or_none()
        if not passenger or not passenger.phone:
            await callback.answer("Avval Yo'lovchiman bo'limida telefon va buyurtma kiriting.", show_alert=True)
            return

        trip_result = await session.execute(
            select(DriverTrip, Driver, User)
            .join(Driver, DriverTrip.driver_id == Driver.id)
            .join(User, Driver.user_id == User.id)
            .where(DriverTrip.id == trip_id)
            .where(DriverTrip.status == "active")
            .where(Driver.status == "active")
        )
        row = trip_result.first()
        if not row:
            await callback.answer("Bu yo'nalish topilmadi yoki joy qolmagan.", show_alert=True)
            return
        trip, driver, driver_user = row

        order_query = (
            select(Order)
            .where(Order.passenger_id == passenger.id)
            .where(Order.status == "searching_driver")
            .where(Order.from_city == trip.from_city)
            .where(Order.to_city == trip.to_city)
            .where(Order.passengers_count <= trip.available_seats)
            .order_by(Order.id.desc())
            .limit(1)
        )
        if trip.roof_luggage != "Ha":
            order_query = order_query.where((Order.roof_luggage == "Yo'q") | (Order.roof_luggage.is_(None)))
        order_result = await session.execute(order_query)
        order = order_result.scalar_one_or_none()
        if not order:
            await state.update_data(
                prefill_trip_id=trip.id,
                from_city=trip.from_city,
                to_city=trip.to_city,
                date=trip.date,
                time=trip.time,
            )
            await callback.answer()
            if not passenger.phone:
                await state.set_state(PassengerOrder.phone)
                await callback.message.answer(
                    "Bu haydovchini tanlash uchun qisqa buyurtma yaratamiz.\nTelefon raqamingizni yuboring:",
                    reply_markup=phone_keyboard(),
                )
            else:
                await state.set_state(PassengerOrder.location)
                await callback.message.answer(
                    "Bu haydovchini tanlash uchun qisqa buyurtma yaratamiz.\nAniq olib ketish lokatsiyangizni yuboring:",
                    reply_markup=location_keyboard(),
                )
            return
        if order.price_per_person and trip.price_per_person > order.price_per_person:
            await callback.answer("Bu haydovchi narxi siz tanlagan maksimal narxdan yuqori.", show_alert=True)
            return

        location_result = await session.execute(select(OrderLocation).where(OrderLocation.order_id == order.id))
        location = location_result.scalar_one_or_none()
        order.driver_id = driver.id
        order.status = "accepted"
        trip.available_seats -= order.passengers_count
        if trip.available_seats <= 0:
            trip.status = "full"
        await session.commit()
        selected_trip_id = trip.id

    passenger_text = (
        "Haydovchi tanlandi!\n\n"
        f"Ism: {driver_user.full_name}\n"
        f"Telefon: {driver_user.phone}\n"
        f"Mashina: {driver.car_model} {driver.car_color}\n"
        f"Raqam: {driver.car_number}\n"
        f"Narx: {trip.price_per_person:,} so'm\n"
        f"Tom bagaj: {trip.roof_luggage}"
    ).replace(",", " ")
    driver_text = (
        "Yo'lovchi sizni tanladi.\n\n"
        f"Yo'lovchi: {passenger.full_name}\n"
        f"Telefon: {passenger.phone}\n"
        f"Yo'nalish: {order.from_city} -> {order.to_city}\n"
        f"Sana/vaqt: {order.date} {order.time}\n"
        f"Yo'lovchi soni: {order.passengers_count}\n"
        f"Tom bagaj kerak: {order.roof_luggage or '-'}"
    )
    await bot.send_message(passenger.telegram_id, passenger_text)
    await bot.send_message(driver_user.telegram_id, driver_text, reply_markup=accepted_order_keyboard(order.id))
    if location:
        await bot.send_location(driver_user.telegram_id, location.latitude, location.longitude)
    await refresh_channel_trip(bot, selected_trip_id)
    await callback.message.edit_text(callback.message.text + "\n\nStatus: tanlandi")
    await callback.answer("Haydovchi tanlandi")


@router.callback_query(F.data.startswith("order:"))
async def order_action(callback: CallbackQuery, bot: Bot) -> None:
    _prefix, action, raw_order_id = callback.data.split(":")
    order_id = int(raw_order_id)
    if action == "skip":
        async with database.SessionLocal() as session:
            msg_result = await session.execute(
                select(OrderMessage)
                .where(OrderMessage.order_id == order_id)
                .where(OrderMessage.chat_id == callback.from_user.id)
                .where(OrderMessage.status == "sent")
            )
            order_message = msg_result.scalar_one_or_none()
            if order_message:
                order_message.status = "skipped"
                await session.commit()
        try:
            await callback.message.edit_text(
                callback.message.text + "\n\n🚫 Siz bu buyurtmani o'chirib yubordingiz.",
                reply_markup=None,
            )
        except Exception as exc:
            logging.warning("Buyurtma xabari yangilanmadi (skip): %s", exc)
        await callback.answer("Buyurtma o'chirildi")
        return

    if action == "cancel":
        async with database.SessionLocal() as session:
            driver_row = await session.execute(
                select(Driver, User)
                .join(User, Driver.user_id == User.id)
                .where(User.telegram_id == callback.from_user.id)
            )
            row = driver_row.first()
            if not row:
                await callback.answer("Siz haydovchi emassiz.", show_alert=True)
                return
            driver, driver_user = row
            order = await session.get(Order, order_id)
            if not order or order.status != "accepted" or order.driver_id != driver.id:
                await callback.answer("Bu buyurtmani bekor qila olmaysiz.", show_alert=True)
                return

            trip_result = await session.execute(
                select(DriverTrip)
                .where(DriverTrip.driver_id == driver.id)
                .where(DriverTrip.from_city == order.from_city)
                .where(DriverTrip.to_city == order.to_city)
                .where(DriverTrip.date == order.date)
                .where(time_match_condition(order.time))
                .order_by(DriverTrip.id.desc())
                .limit(1)
            )
            trip = trip_result.scalars().first()
            if trip:
                trip.available_seats += order.passengers_count
                if trip.status == "full":
                    trip.status = "active"

            passenger = await session.get(User, order.passenger_id)
            order.driver_id = None
            order.status = "searching_driver"
            await session.commit()
            cancelled_trip_id = trip.id if trip else None
            excluded_driver_id = driver.id

        await callback.message.edit_text("❌ Buyurtma bekor qilindi. Buyurtma boshqa haydovchilarga qayta yuboriladi.")
        await bot.send_message(
            passenger.telegram_id,
            "Haydovchi buyurtmani bekor qildi. Buyurtmangiz yana boshqa haydovchilarga yuborilmoqda.",
        )
        if cancelled_trip_id:
            await refresh_channel_trip(bot, cancelled_trip_id)
        sent_count = await broadcast_order_to_drivers(bot, order_id, exclude_driver_id=excluded_driver_id)
        await restore_order_channel_post(bot, order_id)
        await callback.answer("Buyurtma bekor qilindi")
        await callback.message.answer(f"Buyurtma {sent_count} ta boshqa haydovchiga qayta yuborildi.", reply_markup=driver_menu())
        return

    async with database.SessionLocal() as session:
        driver_row = await session.execute(
            select(Driver, User)
            .join(User, Driver.user_id == User.id)
            .where(User.telegram_id == callback.from_user.id)
        )
        row = driver_row.first()
        if not row or row[0].status != "active":
            await callback.answer("Siz tasdiqlangan haydovchi emassiz.", show_alert=True)
            return

        driver, driver_user = row
        order = await session.get(Order, order_id)
        if not order or order.status != "searching_driver":
            await callback.answer("Bu buyurtma allaqachon olingan yoki bekor qilingan.", show_alert=True)
            return

        trip_query = (
            select(DriverTrip)
            .where(DriverTrip.driver_id == driver.id)
            .where(DriverTrip.status == "active")
            .where(DriverTrip.from_city == order.from_city)
            .where(DriverTrip.to_city == order.to_city)
            .where(DriverTrip.date == order.date)
            .where(time_match_condition(order.time))
            .where(DriverTrip.available_seats >= order.passengers_count)
            .order_by(DriverTrip.id.desc())
            .limit(1)
        )
        if order.roof_luggage == "Ha":
            trip_query = trip_query.where(DriverTrip.roof_luggage == "Ha")
        trip_result = await session.execute(trip_query)
        trip = trip_result.scalars().first()

        passenger = await session.get(User, order.passenger_id)
        location_result = await session.execute(select(OrderLocation).where(OrderLocation.order_id == order.id))
        location = location_result.scalar_one_or_none()
        order.driver_id = driver.id
        order.status = "accepted"
        if trip:
            trip.available_seats -= order.passengers_count
            if trip.available_seats == 0:
                trip.status = "full"
        await session.commit()
        selected_trip_id = trip.id if trip else None

    price_text = f"{trip.price_per_person:,} so'm".replace(",", " ") if trip else "Kelishilgan holda"
    car_color_text = trip.roof_luggage if trip else "-"
    passenger_text = (
        "Haydovchi topildi!\n\n"
        f"Ism: {driver_user.full_name}\n"
        f"Telefon: {driver_user.phone}\n"
        f"Mashina: {driver.car_model} {driver.car_color}\n"
        f"Raqam: {driver.car_number}\n"
        f"Narx: {price_text}\n"
        f"Tom bagaj: {car_color_text}"
    )
    driver_text = (
        "Buyurtmani qabul qildingiz.\n\n"
        f"Yo'lovchi: {passenger.full_name}\n"
        f"Telefon: {passenger.phone}\n"
        f"Yo'nalish: {order.from_city} -> {order.to_city}\n"
        f"Sana/vaqt: {order.date} {order.time}\n"
        f"Yo'lovchi soni: {order.passengers_count}\n"
        f"Tom bagaj kerak: {order.roof_luggage or '-'}"
    )
    await bot.send_message(passenger.telegram_id, passenger_text)
    await bot.send_message(driver_user.telegram_id, driver_text, reply_markup=accepted_order_keyboard(order.id))
    if location:
        await bot.send_location(driver_user.telegram_id, location.latitude, location.longitude)
    if selected_trip_id:
        await refresh_channel_trip(bot, selected_trip_id)
    await close_order_messages(bot, order.id, driver_user.id)
    if order.channel_message_id and config.channel_id:
        try:
            await bot.edit_message_text(
                "✅ Bu buyurtma allaqachon qabul qilindi.",
                chat_id=config.channel_id,
                message_id=order.channel_message_id,
            )
        except Exception as exc:
            logging.warning("Kanal buyurtma xabari yangilanmadi: %s", exc)
    await callback.answer("Buyurtma qabul qilindi")


@router.message(F.text == "Mening yo'nalishlarim")
@router.message(F.text == "Мои маршруты")
async def my_trips(message: Message) -> None:
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(DriverTrip, Driver, User)
            .join(Driver, DriverTrip.driver_id == Driver.id)
            .join(User, Driver.user_id == User.id)
            .where(User.telegram_id == message.from_user.id)
            .order_by(DriverTrip.id.desc())
            .limit(10)
        )
        trips = result.all()
    if not trips:
        await message.answer("Hali yo'nalish qo'shmagansiz.")
        return
    text = "Oxirgi yo'nalishlaringiz:\n\n"
    for trip, _driver, _user in trips:
        text += (
            f"#{trip.id}: {trip.from_city} -> {trip.to_city}, {trip.date} {trip.time}, "
            f"joy: {trip.available_seats}, narx: {trip.price_per_person} so'm, "
            f"tom bagaj: {trip.roof_luggage}, status: {trip.status}\n"
        )
    await message.answer(text)


@router.message(F.text == "Mos buyurtmalar")
@router.message(F.text == "Подходящие заказы")
async def matching_orders_for_driver(message: Message) -> None:
    async with database.SessionLocal() as session:
        driver_result = await session.execute(
            select(Driver, User)
            .join(User, Driver.user_id == User.id)
            .where(User.telegram_id == message.from_user.id)
        )
        row = driver_result.first()
        if not row:
            await message.answer("Siz haydovchi sifatida ro'yxatdan o'tmagansiz.")
            return
        driver, _user = row
        if driver.status != "active":
            await message.answer("Avval admin sizni haydovchi sifatida tasdiqlashi kerak.")
            return

        trips_result = await session.execute(
            select(DriverTrip).where(DriverTrip.driver_id == driver.id).where(DriverTrip.status == "active")
        )
        trips = trips_result.scalars().all()
        if not trips:
            await message.answer("Avval yo'nalish qo'shing.")
            return

        conditions = []
        for trip in trips:
            conditions.append(
                (Order.from_city == trip.from_city)
                & (Order.to_city == trip.to_city)
                & (Order.passengers_count <= trip.available_seats)
            )

        from sqlalchemy import or_

        orders_result = await session.execute(
            select(Order)
            .where(Order.status == "searching_driver")
            .where(or_(*conditions))
            .order_by(Order.id.desc())
            .limit(20)
        )
        orders = orders_result.scalars().all()
        order_ids = [order.id for order in orders]
        if order_ids:
            locations_result = await session.execute(select(OrderLocation).where(OrderLocation.order_id.in_(order_ids)))
            locations = {location.order_id: location for location in locations_result.scalars().all()}
        else:
            locations = {}

    if not orders:
        await message.answer("Hozircha yo'nalishlaringizga mos ochiq buyurtma yo'q.")
        return

    await message.answer(f"{len(orders)} ta mos buyurtma topildi:")
    for order in orders:
        await message.answer(format_order_for_driver(order, locations.get(order.id)), reply_markup=order_keyboard(order.id))


@router.message(F.text == "Buyurtmalarim")
@router.message(F.text == "📦 Buyurtmalarim")
@router.message(F.text == "Мои заказы")
@router.message(F.text == "📦 Мои заказы")
async def my_orders(message: Message) -> None:
    async with database.SessionLocal() as session:
        user_result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_result.scalar_one_or_none()
        if not user:
            await message.answer("Siz hali ro'yxatdan o'tmagansiz.")
            return

        result = await session.execute(
            select(Order)
            .where(Order.passenger_id == user.id)
            .order_by(Order.id.desc())
            .limit(10)
        )
        orders = result.scalars().all()

    if not orders:
        await message.answer("Hali buyurtmangiz yo'q.")
        return

    await message.answer("Oxirgi buyurtmalaringiz:")
    for order in orders:
        text = (
            f"#{order.id}: {order.from_city} -> {order.to_city}, "
            f"{order.date} {order.time}, status: {order.status}"
        )
        if order.status in ("searching_driver", "accepted"):
            await message.answer(text, reply_markup=my_order_cancel_keyboard(order.id))
        else:
            await message.answer(text)


@router.callback_query(F.data.startswith("myorder:cancel:"))
async def passenger_cancel_order(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.split(":")[2])
    async with database.SessionLocal() as session:
        user_result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
        user = user_result.scalar_one_or_none()
        order = await session.get(Order, order_id)
        if not user or not order or order.passenger_id != user.id:
            await callback.answer("Bu buyurtma sizga tegishli emas.", show_alert=True)
            return
        if order.status not in ("searching_driver", "accepted"):
            await callback.answer("Bu buyurtmani endi bekor qilib bo'lmaydi.", show_alert=True)
            return

        was_accepted = order.status == "accepted"
        driver_id = order.driver_id
        order.status = "cancelled"
        order.driver_id = None

        driver_user = None
        cancelled_trip_id = None
        if was_accepted and driver_id:
            driver = await session.get(Driver, driver_id)
            if driver:
                driver_user = await session.get(User, driver.user_id)
                trip_result = await session.execute(
                    select(DriverTrip)
                    .where(DriverTrip.driver_id == driver.id)
                    .where(DriverTrip.from_city == order.from_city)
                    .where(DriverTrip.to_city == order.to_city)
                    .where(DriverTrip.date == order.date)
                    .where(time_match_condition(order.time))
                    .order_by(DriverTrip.id.desc())
                    .limit(1)
                )
                trip = trip_result.scalars().first()
                if trip:
                    trip.available_seats += order.passengers_count
                    if trip.status == "full":
                        trip.status = "active"
                    cancelled_trip_id = trip.id

        msg_result = await session.execute(
            select(OrderMessage).where(OrderMessage.order_id == order.id).where(OrderMessage.status == "sent")
        )
        pending_messages = msg_result.scalars().all()
        for item in pending_messages:
            item.status = "closed"

        channel_message_id = order.channel_message_id
        order.channel_message_id = None
        await session.commit()

    for item in pending_messages:
        try:
            await bot.edit_message_text(
                "❌ Yo'lovchi buyurtmani bekor qildi.",
                chat_id=item.chat_id,
                message_id=item.message_id,
            )
        except Exception as exc:
            logging.warning("Buyurtma xabari yangilanmadi (passenger cancel): %s", exc)

    if channel_message_id and config.channel_id:
        try:
            await bot.edit_message_text(
                "❌ Bu buyurtma yo'lovchi tomonidan bekor qilindi.",
                chat_id=config.channel_id,
                message_id=channel_message_id,
            )
        except Exception as exc:
            logging.warning("Kanal buyurtma xabari yangilanmadi (passenger cancel): %s", exc)

    if was_accepted and driver_user:
        await bot.send_message(
            driver_user.telegram_id,
            (
                "⚠️ Diqqat! Yo'lovchi buyurtmasini bekor qildi.\n\n"
                f"Yo'nalish: {order.from_city} -> {order.to_city}\n"
                f"Sana/vaqt: {order.date} {order.time}\n\n"
                "Iltimos, rejangizni shunga qarab moslang."
            ),
        )
    if cancelled_trip_id:
        await refresh_channel_trip(bot, cancelled_trip_id)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("Buyurtma bekor qilindi")


@router.message(F.text == "Profil")
@router.message(F.text == "👤 Profil")
@router.message(F.text == "Профиль")
@router.message(F.text == "👤 Профиль")
async def profile(message: Message) -> None:
    async with database.SessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = result.scalar_one_or_none()
    if not user:
        await message.answer("Profil topilmadi. /start bosing.")
        return
    await message.answer(
        f"Profil\n\n"
        f"Ism: {user.full_name or '-'}\n"
        f"Telefon: {user.phone or '-'}\n"
        f"Rol: {user.role or '-'}"
    )


@router.message(F.text == "Admin panel")
@router.message(F.text == "🛠 Admin panel")
@router.message(F.text == "Analiz")
@router.message(F.text == "📊 Analiz")
@router.message(F.text == "Аналитика")
@router.message(F.text == "📊 Аналитика")
async def admin_panel(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz.")
        return
    lang = await get_user_language(message.from_user.id)
    tz = timezone(timedelta(hours=5))
    today_str = datetime.now(tz).date().isoformat()
    week_start = (datetime.now(tz).date() - timedelta(days=7)).isoformat()
    async with database.SessionLocal() as session:
        users_count = await session.scalar(select(func.count(User.id)))
        passengers_count = await session.scalar(select(func.count(User.id)).where(User.role == "passenger"))
        drivers_count = await session.scalar(select(func.count(Driver.id)))
        active_drivers_count = await session.scalar(select(func.count(Driver.id)).where(Driver.status == "active"))
        pending_drivers_count = await session.scalar(select(func.count(Driver.id)).where(Driver.status == "pending"))
        orders_count = await session.scalar(select(func.count(Order.id)))
        searching_orders_count = await session.scalar(
            select(func.count(Order.id)).where(Order.status == "searching_driver")
        )
        accepted_orders_count = await session.scalar(select(func.count(Order.id)).where(Order.status == "accepted"))
        active_trips_count = await session.scalar(select(func.count(DriverTrip.id)).where(DriverTrip.status == "active"))
        today_orders_count = await session.scalar(select(func.count(Order.id)).where(Order.date == today_str))
        week_orders_count = await session.scalar(select(func.count(Order.id)).where(Order.date >= week_start))
    if lang == "ru":
        text = (
            "📊 Админ аналитика\n\n"
            f"Пользователи: {users_count or 0}\n"
            f"Пассажиры: {passengers_count or 0}\n"
            f"Водители всего: {drivers_count or 0}\n"
            f"Подтвержденные водители: {active_drivers_count or 0}\n"
            f"Водители на проверке: {pending_drivers_count or 0}\n"
            f"Активные маршруты водителей: {active_trips_count or 0}\n"
            f"Заказы всего: {orders_count or 0}\n"
            f"Заказы сегодня: {today_orders_count or 0}\n"
            f"Заказы за 7 дней: {week_orders_count or 0}\n"
            f"Открытые заказы: {searching_orders_count or 0}\n"
            f"Принятые заказы: {accepted_orders_count or 0}"
        )
    else:
        text = (
            "📊 Admin analiz\n\n"
            f"Foydalanuvchilar: {users_count or 0}\n"
            f"Yo'lovchilar: {passengers_count or 0}\n"
            f"Haydovchilar jami: {drivers_count or 0}\n"
            f"Tasdiqlangan haydovchilar: {active_drivers_count or 0}\n"
            f"Tasdiq kutayotgan haydovchilar: {pending_drivers_count or 0}\n"
            f"Haydovchi e'lonlari aktiv: {active_trips_count or 0}\n"
            f"Buyurtmalar jami: {orders_count or 0}\n"
            f"Bugungi buyurtmalar: {today_orders_count or 0}\n"
            f"7 kunlik buyurtmalar: {week_orders_count or 0}\n"
            f"Ochiq buyurtmalar: {searching_orders_count or 0}\n"
            f"Qabul qilingan buyurtmalar: {accepted_orders_count or 0}"
        )
    await message.answer(
        text,
        reply_markup=main_menu(True, lang),
    )


@router.message(F.text == "Yo'lovchilar")
@router.message(F.text == "👥 Yo'lovchilar")
@router.message(F.text == "Пассажиры")
@router.message(F.text == "👥 Пассажиры")
async def admin_passengers(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz.")
        return
    lang = await get_user_language(message.from_user.id)
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(User)
            .where(User.phone.is_not(None))
            .order_by(User.id.desc())
            .limit(20)
        )
        users = result.scalars().all()
    if not users:
        await message.answer("Hozircha telefon raqamli foydalanuvchi yo'q.", reply_markup=main_menu(True, lang))
        return
    text = "👥 Oxirgi 20 ta foydalanuvchi raqami:\n\n"
    for user in users:
        role = user.role or "-"
        text += f"#{user.id} | {user.full_name or '-'} | {user.phone or '-'} | {role}\n"
    await message.answer(text, reply_markup=main_menu(True, lang))


@router.message(F.text == "Haydovchilar")
@router.message(F.text == "🚘 Haydovchilar")
@router.message(F.text == "Водители")
@router.message(F.text == "🚘 Водители")
async def admin_drivers(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz.")
        return
    lang = await get_user_language(message.from_user.id)
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(Driver, User)
            .join(User, Driver.user_id == User.id)
            .order_by(Driver.id.desc())
            .limit(20)
        )
        rows = result.all()
    if not rows:
        await message.answer("Hozircha haydovchi yo'q.", reply_markup=main_menu(True, lang))
        return
    await message.answer("🚘 Oxirgi 20 ta haydovchi:", reply_markup=main_menu(True, lang))
    for driver, user in rows:
        text = (
            f"#{driver.id} | {user.full_name or '-'} | {user.phone or '-'}\n"
            f"Mashina: {driver.car_model} {driver.car_color}, {driver.car_number}\n"
            f"Status: {driver.status}"
        )
        await message.answer(text, reply_markup=admin_driver_manage_keyboard(driver.id, driver.status))


@router.message(F.text == "📢 Xabar yuborish")
@router.message(F.text == "📢 Рассылка")
async def admin_broadcast_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz.")
        return
    lang = await get_user_language(message.from_user.id)
    await state.set_state(AdminBroadcast.target)
    await message.answer(
        "Kimga xabar yuborilsin?" if lang == "uz" else "Кому отправить сообщение?",
        reply_markup=broadcast_target_keyboard(lang),
    )


@router.message(AdminBroadcast.target)
async def admin_broadcast_target(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    if message.text == back_button(lang):
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu(True, lang))
        return
    target_map = {
        "Hammaga": "all", "Всем": "all",
        "Yo'lovchilarga": "passenger", "Пассажирам": "passenger",
        "Haydovchilarga": "driver", "Водителям": "driver",
    }
    target = target_map.get(message.text)
    if not target:
        await message.answer("Iltimos, tugmalardan birini tanlang.")
        return
    await state.update_data(target=target)
    await state.set_state(AdminBroadcast.text)
    await message.answer(
        "Yubormoqchi bo'lgan xabar matnini yozing:" if lang == "uz" else "Напишите текст сообщения:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=back_button(lang))]], resize_keyboard=True),
    )


@router.message(AdminBroadcast.text)
async def admin_broadcast_send(message: Message, state: FSMContext, bot: Bot) -> None:
    lang = await get_user_language(message.from_user.id)
    if message.text == back_button(lang):
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu(True, lang))
        return
    data = await state.get_data()
    target = data.get("target", "all")
    broadcast_text = message.text
    await state.clear()

    async with database.SessionLocal() as session:
        if target == "all":
            result = await session.execute(select(User.telegram_id))
        elif target == "passenger":
            result = await session.execute(select(User.telegram_id).where(User.role == "passenger"))
        else:
            result = await session.execute(
                select(User.telegram_id).join(Driver, Driver.user_id == User.id)
            )
        telegram_ids = [row[0] for row in result.all()]

    await message.answer(f"Yuborilmoqda... ({len(telegram_ids)} ta foydalanuvchiga)", reply_markup=main_menu(True, lang))
    sent = 0
    failed = 0
    for telegram_id in telegram_ids:
        try:
            await bot.send_message(telegram_id, broadcast_text)
            sent += 1
        except Exception:
            failed += 1
    await message.answer(f"✅ Yuborildi: {sent} ta\n❌ Yuborilmadi: {failed} ta")


@router.message(F.text == "🔍 Qidirish")
@router.message(F.text == "🔍 Поиск")
async def admin_search_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz.")
        return
    lang = await get_user_language(message.from_user.id)
    await state.set_state(AdminSearch.query)
    await message.answer(
        "Qidirish uchun ID, telefon raqami yoki ismni yozing:"
        if lang == "uz"
        else "Введите ID, номер телефона или имя для поиска:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=back_button(lang))]], resize_keyboard=True),
    )


@router.message(AdminSearch.query)
async def admin_search_run(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    if message.text == back_button(lang):
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu(True, lang))
        return
    query = message.text.strip()
    await state.clear()

    async with database.SessionLocal() as session:
        filters = []
        clean_id = clean_int(query)
        if clean_id:
            filters.append(User.id == clean_id)
        filters.append(User.phone.ilike(f"%{query}%"))
        filters.append(User.full_name.ilike(f"%{query}%"))
        result = await session.execute(
            select(User).where(or_(*filters)).limit(10)
        )
        users = result.scalars().all()

    if not users:
        await message.answer("Hech narsa topilmadi.", reply_markup=main_menu(True, lang))
        return

    for user in users:
        async with database.SessionLocal() as session2:
            driver_row = await session2.execute(select(Driver).where(Driver.user_id == user.id))
            driver = driver_row.scalars().first()
        text = (
            f"#{user.id} | {user.full_name or '-'} | {user.phone or '-'}\n"
            f"Til: {user.language or '-'} | Rol: {user.role or '-'}"
        )
        if driver:
            text += f"\n🚘 Haydovchi: {driver.car_model} {driver.car_color}, status: {driver.status}"
            await message.answer(text, reply_markup=admin_driver_manage_keyboard(driver.id, driver.status))
        else:
            await message.answer(text, reply_markup=main_menu(True, lang))


@router.message(F.text == "Ochiq buyurtmalar")
@router.message(F.text == "📦 Ochiq buyurtmalar")
@router.message(F.text == "Открытые заказы")
@router.message(F.text == "📦 Открытые заказы")
async def admin_open_orders(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Siz admin emassiz.")
        return
    lang = await get_user_language(message.from_user.id)
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(Order, User)
            .join(User, Order.passenger_id == User.id)
            .where(Order.status == "searching_driver")
            .order_by(Order.id.desc())
            .limit(20)
        )
        rows = result.all()
    if not rows:
        await message.answer(
            "Hozircha ochiq buyurtma yo'q." if lang == "uz" else "Пока нет открытых заказов.",
            reply_markup=main_menu(True, lang),
        )
        return
    text = "📦 Ochiq buyurtmalar:\n\n" if lang == "uz" else "📦 Открытые заказы:\n\n"
    for order, passenger in rows:
        text += (
            f"#{order.id} | {order.from_city} -> {order.to_city}\n"
            f"{order.date} {order.time}, {order.passengers_count} kishi\n"
            f"Yo'lovchi: {passenger.full_name or '-'} | {passenger.phone or '-'}\n\n"
        )
    await message.answer(text, reply_markup=main_menu(True, lang))


@router.message(F.text == "Asosiy menyu")
async def back_to_main(message: Message, state: FSMContext) -> None:
    await state.clear()
    lang = await get_user_language(message.from_user.id)
    await message.answer(
        "Asosiy menyu" if lang == "uz" else "Главное меню",
        reply_markup=main_menu(is_admin(message.from_user.id), lang),
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    database.setup_database(config.database_url)
    await database.init_db()

    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    dp.message.middleware(SubscriptionMiddleware())
    dp.callback_query.middleware(SubscriptionMiddleware())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
