import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandObject
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InputMediaPhoto, KeyboardButton, Message, ReplyKeyboardMarkup
from sqlalchemy import delete, func, or_, select, update

import database
from config import load_config
from keyboards import (
    BACK_BUTTON,
    accepted_order_keyboard,
    admin_analysis_keyboard,
    admin_order_keyboard,
    admin_contacts_keyboard,
    admin_driver_keyboard,
    admin_driver_manage_keyboard,
    back_button,
    broadcast_target_keyboard,
    channel_order_keyboard,
    channel_trip_keyboard,
    city_keyboard,
    complaint_reason_keyboard,
    completion_confirmation_keyboard,
    date_keyboard,
    DISTRICTS_BY_CITY,
    district_keyboard,
    driver_services_keyboard,
    language_keyboard,
    location_keyboard,
    main_menu,
    max_price_keyboard,
    order_keyboard,
    passenger_order_keyboard,
    order_type_keyboard,
    passenger_time_keyboard,
    phone_keyboard,
    price_keyboard,
    rating_keyboard,
    skip_keyboard,
    subscribe_keyboard,
    time_keyboard,
    trip_select_keyboard,
    yes_no_keyboard,
)
from models import Complaint, Driver, DriverPhoto, DriverTrip, Order, OrderLocation, OrderMessage, Rating, User


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
    order_type = State()
    from_city = State()
    from_district = State()
    to_city = State()
    to_district = State()
    location = State()
    date = State()
    time = State()
    passengers_count = State()
    has_female_passenger = State()
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
    client_time = State()
    available_seats = State()
    price_per_person = State()
    roof_luggage = State()
    has_female_passenger = State()
    comment = State()


class AdminBroadcast(StatesGroup):
    target = State()
    text = State()


class AdminSearch(StatesGroup):
    query = State()


class ComplaintFlow(StatesGroup):
    comment = State()


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
        DriverTrip.time == "🕐 Klient vaqti",
        DriverTrip.time == "🕐 Время клиента",
        DriverTrip.time == "⚡ Srochniy",
        DriverTrip.time == "⚡ Срочно",
        order_time in {"⚡ Srochniy", "⚡ Срочно"},
    )


def format_order_for_driver(order: Order, location: OrderLocation | None = None) -> str:
    location_text = ""
    if location:
        location_text = f"\n📍 Lokatsiya: {maps_link(location.latitude, location.longitude)}"
    type_line = "📦 <b>Pochta/buyum yuborish</b>\n" if getattr(order, "order_type", "passenger") == "parcel" else ""
    pax = f"👥 Yo'lovchi soni: <b>{order.passengers_count}</b>\n" if getattr(order, "order_type", "passenger") != "parcel" else ""
    female_line = "👩 <b>Ayol yo'lovchi bor</b>\n" if getattr(order, "has_female_passenger", False) else ""
    return (
        f"🧾 <b>Buyurtma #{order.id}</b>\n\n"
        f"{type_line}"
        f"{female_line}"
        f"🛣 Yo'nalish: <b>{order.from_city} → {order.to_city}</b>\n"
        f"📅 Sana: <b>{order.date}</b>\n"
        f"🕘 Vaqt: <b>{order.time}</b>\n"
        f"{pax}"
        f"💰 Maks narx: <b>{order.price_per_person or 'Farqi yoq'}</b>\n"
        f"🧳 Tom bagaj kerak: <b>{order.roof_luggage or '-'}</b>\n"
        f"💬 Izoh: <b>{order.comment or '-'}</b>"
        f"{location_text}"
    )


def format_order_for_channel(order: Order) -> str:
    is_parcel = getattr(order, "order_type", "passenger") == "parcel"
    type_line = "📦 <b>Pochta/buyum yuborish</b>\n\n" if is_parcel else ""
    pax_line = f"👥 Yo'lovchi soni: <b>{order.passengers_count}</b>\n" if not is_parcel else ""
    female_line = "👩 <b>Ayol yo'lovchi bor</b>\n" if getattr(order, "has_female_passenger", False) else ""
    order_title = "Pochta buyurtmasi" if is_parcel else "Yo'lovchi buyurtmasi"
    return (
        f"🧾 <b>SafarX — {order_title}</b>\n\n"
        f"{type_line}"
        f"{female_line}"
        f"🛣 <b>{order.from_city}  →  {order.to_city}</b>\n\n"
        f"📅 Sana: <b>{order.date}</b>\n"
        f"🕘 Vaqt: <b>{order.time}</b>\n"
        f"{pax_line}"
        f"💰 Maks narx: <b>{order.price_per_person or 'Farqi yoq'}</b>\n"
        f"🧳 Tom bagaj kerak: <b>{order.roof_luggage or '-'}</b>\n\n"
        f"👇 Qabul qilish uchun tugmani bosing"
    )


def format_trip_for_passenger(trip: DriverTrip, driver: Driver) -> str:
    badges = []
    if getattr(trip, "has_female_passenger", False):
        badges.append("👩 Ayol yo'lovchi bor")
    badge_line = "<b>" + "  •  ".join(badges) + "</b>\n" if badges else ""
    price = f"{trip.price_per_person:,}".replace(",", " ")
    return (
        f"🚕 <b>Haydovchi yo'nalishi #{trip.id}</b>\n\n"
        f"{badge_line}"
        f"🛣 Yo'nalish: <b>{trip.from_city} → {trip.to_city}</b>\n"
        f"📅 Sana: <b>{trip.date}</b>\n"
        f"🕘 Vaqt: <b>{trip.time}</b>\n"
        f"👥 Bo'sh joy: <b>{trip.available_seats}</b>\n"
        f"💰 Narx: <b>{price} so'm</b>\n"
        f"🧳 Tom bagaj: <b>{trip.roof_luggage}</b>\n"
        f"🚘 Mashina: <b>{driver.car_model} {driver.car_color}</b>\n"
        f"💬 Izoh: <b>{trip.comment or '-'}</b>"
    )


def format_channel_trip(trip: DriverTrip, driver: Driver) -> str:
    status_line = "✅ <b>Joy mavjud</b>" if trip.status == "active" and trip.available_seats > 0 else "⛔ <b>Joy qolmadi</b>"
    price = f"{trip.price_per_person:,}".replace(",", " ")
    female_line = "👩 <b>Ayol yo'lovchilar bor</b>\n" if getattr(trip, "has_female_passenger", False) else ""
    return (
        f"🚖 <b>SafarX — Haydovchi e'loni</b>\n\n"
        f"{status_line}\n\n"
        f"{female_line}"
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


async def publish_order_to_channel(bot: Bot, order_id: int) -> int | None:
    if not config.channel_id:
        return None

    async with database.SessionLocal() as session:
        order = await session.get(Order, order_id)
        if not order or order.status != "searching_driver":
            return None
        old_channel_message_id = order.channel_message_id
        order_text = format_order_for_channel(order)

    if old_channel_message_id:
        try:
            await bot.delete_message(config.channel_id, old_channel_message_id)
        except Exception as exc:
            logging.warning("Eski kanal buyurtmasi o'chirilmadi: %s", exc)

    try:
        channel_message = await bot.send_message(
            config.channel_id,
            order_text,
            reply_markup=channel_order_keyboard(order_id, config.bot_username),
            parse_mode="HTML",
        )
    except Exception as exc:
        logging.warning("Buyurtma kanalga yuborilmadi: %s", exc)
        return None

    async with database.SessionLocal() as session:
        db_order = await session.get(Order, order_id)
        if db_order and db_order.status == "searching_driver":
            db_order.channel_message_id = channel_message.message_id
            await session.commit()
    return channel_message.message_id


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


async def remember_order_artifact(
    order_id: int,
    driver_user_id: int,
    field_name: str,
    message_id: int,
) -> None:
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(OrderMessage)
            .where(OrderMessage.order_id == order_id)
            .where(OrderMessage.driver_user_id == driver_user_id)
            .order_by(OrderMessage.id.desc())
            .limit(1)
        )
        item = result.scalar_one_or_none()
        if item:
            setattr(item, field_name, message_id)
            await session.commit()


async def cancel_order_by_passenger_or_admin(
    bot: Bot,
    order_id: int,
    requester_telegram_id: int,
    admin: bool = False,
) -> tuple[bool, str]:
    async with database.SessionLocal() as session:
        order = await session.get(Order, order_id)
        if not order:
            return False, "Buyurtma topilmadi."
        passenger = await session.get(User, order.passenger_id)
        if not admin and passenger.telegram_id != requester_telegram_id:
            return False, "Bu buyurtma sizga tegishli emas."
        if order.status not in {"searching_driver", "accepted"}:
            return False, "Bu buyurtmani endi bekor qilib bo'lmaydi."

        driver_user = None
        trip = None
        if order.status == "accepted" and order.driver_id:
            driver = await session.get(Driver, order.driver_id)
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

        messages_result = await session.execute(
            select(OrderMessage).where(OrderMessage.order_id == order.id)
        )
        artifacts: set[tuple[int, int]] = set()
        for item in messages_result.scalars().all():
            for message_id in (item.message_id, item.contact_message_id, item.location_message_id):
                if message_id:
                    artifacts.add((item.chat_id, message_id))
            item.status = "cancelled"

        channel_message_id = order.channel_message_id
        order.status = "cancelled"
        order.driver_id = None
        await session.commit()
        trip_id = trip.id if trip else None
        passenger_id = passenger.telegram_id
        passenger_lang = passenger.language or "uz"
        driver_id = driver_user.telegram_id if driver_user else None
        driver_lang = (driver_user.language or "uz") if driver_user else "uz"

    for chat_id, message_id in artifacts:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as exc:
            logging.warning("Bekor qilingan buyurtma xabari o'chirilmadi: %s", exc)
    if channel_message_id and config.channel_id:
        try:
            await bot.delete_message(config.channel_id, channel_message_id)
        except Exception as exc:
            logging.warning("Bekor qilingan kanal buyurtmasi o'chirilmadi: %s", exc)
    if trip_id:
        await refresh_channel_trip(bot, trip_id)
    if driver_id:
        await bot.send_message(
            driver_id,
            "❌ Yo'lovchi buyurtmani bekor qildi." if driver_lang == "uz" else "❌ Пассажир отменил заказ.",
        )
    if admin and requester_telegram_id != passenger_id:
        await bot.send_message(
            passenger_id,
            "❌ Buyurtmangiz admin tomonidan yopildi."
            if passenger_lang == "uz" else "❌ Ваш заказ закрыт администратором.",
        )
    return True, "Buyurtma bekor qilindi."


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
            reply_markup=order_keyboard(order.id, driver_user.language or "uz"),
            parse_mode="HTML",
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
            location_message = await bot.send_location(
                driver_user.telegram_id, location.latitude, location.longitude
            )
            await remember_order_artifact(
                order.id, driver_user.id, "location_message_id", location_message.message_id
            )
        sent_count += 1
    return sent_count


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
        parse_mode="HTML",
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
        driver, driver_user = driver_match
        lang = driver_user.language or await get_user_language(message.from_user.id)
        order = await session.get(Order, order_id)
        if not order or order.status != "searching_driver":
            await message.answer(
                "⛔ Bu buyurtma allaqachon band qilingan yoki bekor qilingan.",
                reply_markup=driver_menu(lang),
            )
            return
        location_result = await session.execute(select(OrderLocation).where(OrderLocation.order_id == order.id))
        location = location_result.scalar_one_or_none()
    sent_message = await message.answer(
        format_order_for_driver(order, location),
        reply_markup=order_keyboard(order.id, lang),
        parse_mode="HTML",
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
        location_message = await message.answer_location(location.latitude, location.longitude)
        await remember_order_artifact(
            order.id, driver_user.id, "location_message_id", location_message.message_id
        )


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
    await state.update_data(lang=lang, order_type="passenger")
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


@router.message(F.text == "📦 Pochta/Buyum")
@router.message(F.text == "📦 Посылка/Товар")
async def parcel_start(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message)
    lang = await get_user_language(message.from_user.id)
    await state.clear()
    await state.update_data(lang=lang, order_type="parcel")
    async with database.SessionLocal() as session:
        db_user = await session.get(User, user.id)
        db_user.role = "passenger"
        await session.commit()
    if user.phone:
        await state.set_state(PassengerOrder.from_city)
        await message.answer("Pochta qaysi shahardan ketadi?" if lang == "uz" else "Из какого города отправляете посылку?", reply_markup=city_keyboard(lang))
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
    await state.set_state(PassengerOrder.order_type)
    await message.answer(
        "Qanday xizmat kerak?" if lang == "uz" else "Какой тип заказа?",
        reply_markup=order_type_keyboard(lang),
    )


@router.message(PassengerOrder.order_type)
async def passenger_order_type(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    valid_uz = ["🧍 Yo'lovchi", "📦 Pochta/buyum yuborish"]
    valid_ru = ["🧍 Пассажир", "📦 Отправить посылку"]
    if message.text not in valid_uz + valid_ru:
        await message.answer(
            "Iltimos, tugmalardan birini tanlang." if lang == "uz" else "Пожалуйста, выберите один из вариантов.",
            reply_markup=order_type_keyboard(lang),
        )
        return
    is_parcel = message.text in ["📦 Pochta/buyum yuborish", "📦 Отправить посылку"]
    await state.update_data(order_type="parcel" if is_parcel else "passenger")
    await state.set_state(PassengerOrder.from_city)
    await message.answer("Qaysi shahardan ketasiz?" if lang == "uz" else "Из какого города выезжаете?", reply_markup=city_keyboard(lang))


@router.message(PassengerOrder.from_city)
async def passenger_from_city(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    if message.text == back_button(lang):
        await state.clear()
        await message.answer("Bekor qilindi." if lang == "uz" else "Отменено.", reply_markup=main_menu(False, lang))
        return
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
    if message.text == back_button(lang):
        await state.set_state(PassengerOrder.from_city)
        await message.answer("Qaysi shahardan ketasiz?" if lang == "uz" else "Из какого города выезжаете?", reply_markup=city_keyboard(lang))
        return
    city = data.get("from_city_base", "Andijon")
    await state.update_data(from_city=place_with_district(city, message.text))
    await state.set_state(PassengerOrder.to_city)
    await message.answer("Qayerga borasiz?" if lang == "uz" else "В какой город едете?", reply_markup=city_keyboard(lang))


@router.message(PassengerOrder.to_city)
async def passenger_to_city(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    if message.text == back_button(lang):
        await state.set_state(PassengerOrder.from_city)
        await message.answer("Qaysi shahardan ketasiz?" if lang == "uz" else "Из какого города выезжаете?", reply_markup=city_keyboard(lang))
        return
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
    data2 = await state.get_data()
    lang2 = data2.get("lang", await get_user_language(message.from_user.id))
    if message.text == back_button(lang2):
        await state.set_state(PassengerOrder.to_city)
        await message.answer("Qayerga borasiz?" if lang2 == "uz" else "В какой город едете?", reply_markup=city_keyboard(lang2))
        return
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
        await state.set_state(PassengerOrder.time)
        await message.answer(
            "Soat nechida ketmoqchisiz?" if lang == "uz" else "Во сколько вы хотите выехать?",
            reply_markup=passenger_time_keyboard(lang),
        )
        return
    await state.set_state(PassengerOrder.date)
    await message.answer("Qaysi sana ketasiz?" if lang == "uz" else "На какую дату поездка?", reply_markup=date_keyboard(lang))


@router.message(PassengerOrder.date)
async def passenger_date(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    await state.update_data(date=message.text)
    await state.set_state(PassengerOrder.time)
    await message.answer("Soat nechida ketasiz?" if lang == "uz" else "Во сколько выезжаете?", reply_markup=passenger_time_keyboard(lang))


@router.message(PassengerOrder.time)
async def passenger_time(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    await state.update_data(time=message.text)
    order_type = data.get("order_type", "passenger")
    if order_type == "parcel":
        await state.update_data(passengers_count=0)
        await state.set_state(PassengerOrder.roof_luggage)
        await message.answer(
            "Tom bagaj kerakmi? (Katta buyumlar uchun)" if lang == "uz" else "Нужен багажник на крыше?",
            reply_markup=yes_no_keyboard(),
        )
        return
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
    await state.set_state(PassengerOrder.has_female_passenger)
    await message.answer(
        "Yo'lovchilar orasida ayol kishi bormi?" if lang == "uz" else "Есть ли среди пассажиров женщина?",
        reply_markup=yes_no_keyboard(lang),
    )


@router.message(PassengerOrder.has_female_passenger)
async def passenger_female_flag(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    if not is_yes(message.text) and not is_no(message.text):
        await message.answer(
            "Iltimos, Ha yoki Yo'q tugmasini tanlang." if lang == "uz" else "Пожалуйста, выберите Да или Нет.",
            reply_markup=yes_no_keyboard(lang),
        )
        return
    await state.update_data(has_female_passenger=is_yes(message.text))
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
    lang = data.get("lang", await get_user_language(message.from_user.id))
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
            order_type=data.get("order_type", "passenger"),
            has_female_passenger=data.get("has_female_passenger", False),
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

            driver_text = "🕐 <b>Klient vaqti bo'yicha yangi so'rov</b>\n\n" + format_order_for_driver(order, location)
            sent_message = await bot.send_message(
                selected_driver_user.telegram_id,
                driver_text,
                reply_markup=order_keyboard(order.id, selected_driver_user.language or "uz"),
                parse_mode="HTML",
            )
            session.add(OrderMessage(
                order_id=order.id,
                driver_user_id=selected_driver_user.id,
                chat_id=selected_driver_user.telegram_id,
                message_id=sent_message.message_id,
            ))
            await session.commit()
            location_message = await bot.send_location(
                selected_driver_user.telegram_id, location.latitude, location.longitude
            )
            await remember_order_artifact(
                order.id, selected_driver_user.id, "location_message_id", location_message.message_id
            )
            await message.answer(
                (
                    f"So'rovingiz haydovchiga yuborildi. Haydovchi {order.time} vaqtini tasdiqlashini kuting."
                    if lang == "uz"
                    else f"Ваш запрос отправлен водителю. Ожидайте подтверждения времени {order.time}."
                ),
                reply_markup=main_menu(is_admin(message.from_user.id), lang),
            )
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
            reply_markup=order_keyboard(order.id, driver_user.language or "uz"),
            parse_mode="HTML",
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
        location_message = await bot.send_location(
            driver_user.telegram_id, location.latitude, location.longitude
        )
        await remember_order_artifact(
            order.id, driver_user.id, "location_message_id", location_message.message_id
        )

    await state.clear()
    if passenger_matched:
        await message.answer(
            f"{len(passenger_matched)} ta narxingizga mos haydovchi topildi. O'zingizga ma'qulini tanlang:"
            if lang == "uz"
            else f"Найдено водителей по вашей цене: {len(passenger_matched)}. Выберите подходящего:"
        )
        for trip, driver, _driver_user in passenger_matched:
            await message.answer(format_trip_for_passenger(trip, driver), reply_markup=trip_select_keyboard(trip.id, lang), parse_mode="HTML")
    else:
        await publish_order_to_channel(bot, order.id)
        await message.answer(
            (
                "Buyurtmangiz haydovchilarga yuborildi.\n"
                "Hozircha siz tanlagan maksimal narxga mos haydovchi topilmadi. "
                "E'loningiz tezroq haydovchiga yetishi uchun kanalga ham joylashtirildi."
                if lang == "uz"
                else "Ваш заказ отправлен водителям.\n"
                "Пока не найден водитель по указанной максимальной цене. "
                "Для ускорения поиска объявление также размещено в канале."
            )
        )
    await message.answer(
        "Asosiy menyu" if lang == "uz" else "Главное меню",
        reply_markup=main_menu(is_admin(message.from_user.id), lang),
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
    await state.set_state(DriverRegister.tech_passport_photo)
    await message.answer(
        "Endi avtomobil tex passporti rasmini yuboring."
        if lang == "uz"
        else "Теперь отправьте фото техпаспорта автомобиля."
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
    media_group = [
        InputMediaPhoto(media=data["car_front_photo"], caption="🚗 Mashinaning old ko'rinishi"),
        InputMediaPhoto(media=data["tech_passport_photo"], caption="📄 Avtomobil texpasporti"),
    ]
    for admin_id in config.admin_ids:
        logging.info("Adminga yuborilmoqda: admin_id=%s", admin_id)
        try:
            await bot.send_media_group(admin_id, media=media_group)
        except Exception as exc:
            logging.error("Haydovchi rasmlari albomi yuborilmadi admin_id=%s error=%s", admin_id, exc)
        try:
            await bot.send_message(admin_id, admin_text, reply_markup=admin_driver_keyboard(driver.id))
            logging.info("Admin xabari yuborildi: admin_id=%s driver_id=%s", admin_id, driver.id)
        except Exception as exc:
            logging.error("Admin xabari yuborilmadi: admin_id=%s error=%s", admin_id, exc)

    await state.clear()
    await message.answer(
        "Arizangiz adminga yuborildi. Tasdiqlanishini kuting."
        if data.get("lang", "uz") == "uz"
        else "Ваша заявка отправлена администратору. Ожидайте подтверждения."
    )


@router.message(F.text == "Yo'nalish qo'shish")
@router.message(F.text == "Добавить маршрут")
async def trip_start(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(Driver, User)
            .join(User, Driver.user_id == User.id)
            .where(User.telegram_id == message.from_user.id)
        )
        row = result.first()
    if not row or row[0].status != "active":
        await message.answer(
            "Avval admin sizni haydovchi sifatida tasdiqlashi kerak."
            if lang == "uz" else "Сначала администратор должен подтвердить вас как водителя."
        )
        return

    await state.update_data(lang=lang)
    await state.set_state(DriverTripCreate.from_city)
    await message.answer("Qayerdan ketasiz?" if lang == "uz" else "Откуда выезжаете?", reply_markup=city_keyboard(lang))


@router.message(DriverTripCreate.from_city)
async def trip_from_city(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    if message.text == back_button(lang):
        await state.clear()
        await message.answer("Bekor qilindi." if lang == "uz" else "Отменено.", reply_markup=driver_menu(lang))
        return
    if needs_district(message.text):
        await state.update_data(from_city_base=message.text)
        await state.set_state(DriverTripCreate.from_district)
        await message.answer("Qaysi tumandan ketasiz?" if lang == "uz" else "Из какого района выезжаете?", reply_markup=district_keyboard(message.text, lang))
        return
    await state.update_data(from_city=message.text, from_city_base=message.text)
    await state.set_state(DriverTripCreate.to_city)
    await message.answer("Qayerga borasiz?" if lang == "uz" else "Куда едете?", reply_markup=city_keyboard(lang))


@router.message(DriverTripCreate.from_district)
async def trip_from_district(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    if message.text == back_button(lang):
        await state.set_state(DriverTripCreate.from_city)
        await message.answer("Qayerdan ketasiz?" if lang == "uz" else "Откуда выезжаете?", reply_markup=city_keyboard(lang))
        return
    data = await state.get_data()
    city = data.get("from_city_base", "Andijon")
    await state.update_data(from_city=place_with_district(city, message.text))
    await state.set_state(DriverTripCreate.to_city)
    await message.answer("Qayerga borasiz?" if lang == "uz" else "Куда едете?", reply_markup=city_keyboard(lang))


@router.message(DriverTripCreate.to_city)
async def trip_to_city(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    if message.text == back_button(lang):
        await state.set_state(DriverTripCreate.from_city)
        await message.answer("Qayerdan ketasiz?" if lang == "uz" else "Откуда выезжаете?", reply_markup=city_keyboard(lang))
        return
    data = await state.get_data()
    if message.text == data.get("from_city") or (
        not needs_district(message.text) and message.text == data.get("from_city_base")
    ):
        await message.answer("Boradigan shahar ketadigan shahar bilan bir xil bo'lmasin." if lang == "uz" else "Город назначения не должен совпадать с городом отправления.")
        return
    if needs_district(message.text):
        await state.update_data(to_city_base=message.text)
        await state.set_state(DriverTripCreate.to_district)
        await message.answer("Qaysi tumanga borasiz?" if lang == "uz" else "В какой район едете?", reply_markup=district_keyboard(message.text, lang))
        return
    await state.update_data(to_city=message.text, to_city_base=message.text)
    await state.set_state(DriverTripCreate.date)
    await message.answer("Qaysi sana ketasiz?" if lang == "uz" else "На какую дату поездка?", reply_markup=date_keyboard(lang))


@router.message(DriverTripCreate.to_district)
async def trip_to_district(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    city = data.get("to_city_base", "Andijon")
    to_city = place_with_district(city, message.text)
    if to_city == data.get("from_city"):
        await message.answer("Boradigan tuman ketadigan tuman bilan bir xil bo'lmasin." if lang == "uz" else "Район назначения не должен совпадать с районом отправления.")
        return
    await state.update_data(to_city=to_city)
    await state.set_state(DriverTripCreate.date)
    await message.answer("Qaysi sana ketasiz?" if lang == "uz" else "На какую дату поездка?", reply_markup=date_keyboard(lang))


@router.message(DriverTripCreate.date)
async def trip_date(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    if message.text == back_button(lang):
        await state.set_state(DriverTripCreate.to_district)
        await message.answer("Orqaga qaytdik." if lang == "uz" else "Вернулись назад.")
        return
    raw_text = message.text.strip()
    date_part = raw_text.split(" - ")[-1].strip() if " - " in raw_text else raw_text
    try:
        datetime.strptime(date_part, "%Y-%m-%d")
    except ValueError:
        await message.answer("Iltimos, tugmalardan birini tanlang yoki sanani YYYY-MM-DD formatida yozing." if lang == "uz" else "Выберите одну из кнопок или введите дату в формате ГГГГ-ММ-ДД.")
        return
    await state.update_data(date=date_part)
    await state.set_state(DriverTripCreate.time)
    await message.answer("Soat nechida ketasiz?" if lang == "uz" else "Во сколько выезжаете?", reply_markup=time_keyboard(lang))


@router.message(DriverTripCreate.time)
async def trip_time(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    if message.text == back_button(lang):
        await state.set_state(DriverTripCreate.date)
        await message.answer("Qaysi sana ketasiz?", reply_markup=date_keyboard(lang))
        return
    if message.text in {"🕐 Klient vaqti", "🕐 Время клиента"}:
        await state.update_data(time=message.text)
        await state.set_state(DriverTripCreate.available_seats)
        await message.answer("Bo'sh joy soni nechta?" if lang == "uz" else "Сколько свободных мест?")
        return
    await state.update_data(time=message.text)
    await state.set_state(DriverTripCreate.available_seats)
    await message.answer("Bo'sh joy soni nechta?" if lang == "uz" else "Сколько свободных мест?")


@router.message(DriverTripCreate.client_time)
async def trip_client_time(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    if message.text == back_button(lang):
        await state.set_state(DriverTripCreate.time)
        await message.answer("Soat nechida ketasiz?" if lang == "uz" else "Во сколько выезжаете?", reply_markup=time_keyboard(lang))
        return
    await state.update_data(time=message.text)
    await state.set_state(DriverTripCreate.available_seats)
    await message.answer("Bo'sh joy soni nechta?" if lang == "uz" else "Сколько свободных мест?")


@router.message(DriverTripCreate.available_seats)
async def trip_seats(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    seats = clean_int(message.text)
    if not seats or seats < 1:
        await message.answer("Bo'sh joy sonini raqam bilan kiriting." if lang == "uz" else "Введите количество свободных мест цифрой.")
        return
    await state.update_data(available_seats=seats)
    await state.set_state(DriverTripCreate.price_per_person)
    await message.answer("Bir kishi uchun narxni tanlang:" if lang == "uz" else "Выберите цену за одного человека:", reply_markup=price_keyboard(lang))


@router.message(DriverTripCreate.price_per_person)
async def trip_price(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    price = clean_int(message.text)
    if not price or price < 180000 or price > 300000:
        await message.answer("Narxni 200 000 - 250 000 so'm oralig'idagi tugmalardan tanlang." if lang == "uz" else "Выберите цену кнопкой в диапазоне 200 000–250 000 сум.", reply_markup=price_keyboard(lang))
        return
    await state.update_data(price_per_person=price)
    await state.set_state(DriverTripCreate.roof_luggage)
    await message.answer("Mashinada tom bagaj bormi?" if lang == "uz" else "Есть ли у машины багажник на крыше?", reply_markup=yes_no_keyboard(lang))


@router.message(DriverTripCreate.roof_luggage)
async def trip_roof_luggage(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    if not is_yes(message.text) and not is_no(message.text):
        await message.answer("Iltimos, Ha yoki Yo'q tugmasini tanlang." if lang == "uz" else "Выберите Да или Нет.", reply_markup=yes_no_keyboard(lang))
        return
    await state.update_data(roof_luggage="Ha" if is_yes(message.text) else "Yo'q", is_pickup_service=False)
    await state.set_state(DriverTripCreate.has_female_passenger)
    await message.answer("Yo'lovchilar orasida ayol kishi bormi?" if lang == "uz" else "Есть ли среди пассажиров женщина?", reply_markup=yes_no_keyboard(lang))


@router.message(DriverTripCreate.has_female_passenger)
async def trip_female_passenger(message: Message, state: FSMContext) -> None:
    lang = await get_user_language(message.from_user.id)
    if not is_yes(message.text) and not is_no(message.text):
        await message.answer("Iltimos, Ha yoki Yo'q tugmasini tanlang." if lang == "uz" else "Выберите Да или Нет.", reply_markup=yes_no_keyboard(lang))
        return
    await state.update_data(has_female_passenger=is_yes(message.text))
    await state.set_state(DriverTripCreate.comment)
    await message.answer("Qo'shimcha izoh bormi?" if lang == "uz" else "Есть дополнительный комментарий?", reply_markup=skip_keyboard(lang))


@router.message(DriverTripCreate.comment)
async def trip_comment(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    lang = data.get("lang", await get_user_language(message.from_user.id))
    comment = None if is_skip_comment(message.text) else message.text
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
            is_pickup_service=data.get("is_pickup_service", False),
            has_female_passenger=data.get("has_female_passenger", False),
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
            .where(or_(
                Order.time == trip.time,
                Order.time.in_(["⚡ Srochniy", "⚡ Срочно"]),
                trip.time in ["⚡ Srochniy", "⚡ Срочно", "🕐 Klient vaqti", "🕐 Время клиента"],
            ))
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
        (
            f"Yo'nalish qo'shildi va kanalga yuborishga tayyorlandi.\nBu xizmat test davrida bepul.\n\nShu yo'nalish bo'yicha {len(matching_orders)} ta ochiq buyurtma topildi."
            if lang == "uz"
            else f"Маршрут добавлен и подготовлен к публикации в канале.\nВ тестовый период услуга бесплатна.\n\nНайдено открытых заказов по маршруту: {len(matching_orders)}."
        ),
        reply_markup=driver_menu(lang),
    )
    for order in matching_orders:
        await message.answer(format_order_for_driver(order, locations.get(order.id)), reply_markup=order_keyboard(order.id, lang), parse_mode="HTML")

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
            await message.answer(f"E'lon kanalga yuborildi: {config.channel_id}" if lang == "uz" else f"Объявление отправлено в канал: {config.channel_id}")
        except Exception as exc:
            logging.warning("Kanalga e'lon yuborilmadi: %s", exc)
            await message.answer(
                "E'lon kanalga yuborilmadi.\nBot kanalga admin qilinganini va Post Messages ruxsati borligini tekshiring."
                if lang == "uz" else
                "Объявление не отправлено в канал.\nПроверьте, что бот назначен администратором и имеет право публикации сообщений."
            )
    else:
        await message.answer("Kanal sozlanmagan. CHANNEL_ID .env faylida ko'rsatilmagan." if lang == "uz" else "Канал не настроен: CHANNEL_ID не указан в .env.")


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
    lang = await get_user_language(callback.from_user.id)
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
                lang=lang,
            )
            await callback.answer()
            if not passenger.phone:
                await state.set_state(PassengerOrder.phone)
                await callback.message.answer(
                    (
                        "Bu haydovchini tanlash uchun qisqa buyurtma yaratamiz.\nTelefon raqamingizni yuboring:"
                        if lang == "uz"
                        else "Для выбора этого водителя создадим короткий заказ.\nОтправьте номер телефона:"
                    ),
                    reply_markup=phone_keyboard(lang),
                )
            else:
                await state.set_state(PassengerOrder.location)
                await callback.message.answer(
                    (
                        "Bu haydovchini tanlash uchun qisqa buyurtma yaratamiz.\nAniq olib ketish lokatsiyangizni yuboring:"
                        if lang == "uz"
                        else "Для выбора этого водителя создадим короткий заказ.\nОтправьте точную точку посадки:"
                    ),
                    reply_markup=location_keyboard(lang),
                )
            return
        if order.price_per_person and trip.price_per_person > order.price_per_person:
            await callback.answer("Bu haydovchi narxi siz tanlagan maksimal narxdan yuqori.", show_alert=True)
            return

        location_result = await session.execute(select(OrderLocation).where(OrderLocation.order_id == order.id))
        location = location_result.scalar_one_or_none()
        if trip.time in {"🕐 Klient vaqti", "🕐 Время клиента"}:
            order.driver_id = driver.id
            await session.commit()
            request_text = "🕐 <b>Klient vaqti bo'yicha yangi so'rov</b>\n\n" + format_order_for_driver(order, location)
            sent_message = await bot.send_message(
                driver_user.telegram_id,
                request_text,
                reply_markup=order_keyboard(order.id, driver_user.language or "uz"),
                parse_mode="HTML",
            )
            session.add(OrderMessage(
                order_id=order.id,
                driver_user_id=driver_user.id,
                chat_id=driver_user.telegram_id,
                message_id=sent_message.message_id,
            ))
            await session.commit()
            if location:
                location_message = await bot.send_location(
                    driver_user.telegram_id, location.latitude, location.longitude
                )
                await remember_order_artifact(
                    order.id, driver_user.id, "location_message_id", location_message.message_id
                )
            await callback.message.answer(
                (
                    f"So'rovingiz haydovchiga yuborildi. Haydovchi {order.time} vaqtini tasdiqlashini kuting."
                    if lang == "uz"
                    else f"Ваш запрос отправлен водителю. Ожидайте подтверждения времени {order.time}."
                )
            )
            await callback.answer("So'rov haydovchiga yuborildi" if lang == "uz" else "Запрос отправлен водителю")
            return
        order.driver_id = driver.id
        order.status = "accepted"
        trip.available_seats -= order.passengers_count
        if trip.available_seats <= 0:
            trip.status = "full"
        await session.commit()
        selected_trip_id = trip.id

    driver_phone = driver_user.phone or ""
    if driver_phone and not driver_phone.startswith("+"):
        driver_phone = "+" + driver_phone
    passenger_phone = passenger.phone or ""
    if passenger_phone and not passenger_phone.startswith("+"):
        passenger_phone = "+" + passenger_phone
    price_str = f"{trip.price_per_person:,}".replace(",", " ")
    passenger_text = (
        "✅ <b>Haydovchi tanlandi!</b>\n\n"
        f"👤 Ism: <b>{driver_user.full_name}</b>\n"
        f"📞 Telefon: <b>{driver_phone}</b>\n"
        f"🚘 Mashina: <b>{driver.car_model} {driver.car_color}</b>\n"
        f"🔢 Raqam: <b>{driver.car_number}</b>\n"
        f"💰 Narx: <b>{price_str} so'm</b>\n"
        f"🧳 Tom bagaj: <b>{trip.roof_luggage}</b>"
    )
    driver_text = (
        "✅ <b>Yo'lovchi sizni tanladi!</b>\n\n"
        f"👤 Yo'lovchi: <b>{passenger.full_name}</b>\n"
        f"🛣 Yo'nalish: <b>{order.from_city} → {order.to_city}</b>\n"
        f"📅 Sana/vaqt: <b>{order.date} {order.time}</b>\n"
        f"👥 Yo'lovchi soni: <b>{order.passengers_count}</b>\n"
        f"🧳 Tom bagaj kerak: <b>{order.roof_luggage or '-'}</b>"
    )
    if (passenger.language or "uz") == "ru":
        passenger_text = (
            "✅ <b>Водитель выбран!</b>\n\n"
            f"👤 Имя: <b>{driver_user.full_name}</b>\n"
            f"📞 Телефон: <b>{driver_phone}</b>\n"
            f"🚘 Машина: <b>{driver.car_model} {driver.car_color}</b>\n"
            f"🔢 Номер: <b>{driver.car_number}</b>\n"
            f"💰 Цена: <b>{price_str} сум</b>\n"
            f"🧳 Багажник на крыше: <b>{trip.roof_luggage}</b>"
        )
    if (driver_user.language or "uz") == "ru":
        driver_text = (
            "✅ <b>Пассажир выбрал вас!</b>\n\n"
            f"👤 Пассажир: <b>{passenger.full_name}</b>\n"
            f"🛣 Маршрут: <b>{order.from_city} → {order.to_city}</b>\n"
            f"📅 Дата/время: <b>{order.date} {order.time}</b>\n"
            f"👥 Пассажиров: <b>{order.passengers_count}</b>\n"
            f"🧳 Нужен багажник: <b>{order.roof_luggage or '-'}</b>"
        )
    passenger_accepted_message = await bot.send_message(passenger.telegram_id, passenger_text, parse_mode="HTML")
    passenger_contact_message_id = None
    try:
        passenger_contact_message = await bot.send_contact(
            passenger.telegram_id,
            phone_number=driver_phone,
            first_name=driver_user.full_name or "Haydovchi",
        )
        passenger_contact_message_id = passenger_contact_message.message_id
    except Exception:
        pass
    await bot.send_message(
        driver_user.telegram_id,
        driver_text,
        reply_markup=accepted_order_keyboard(order.id, passenger.telegram_id, driver_user.language or "uz"),
        parse_mode="HTML",
    )
    try:
        contact_message = await bot.send_contact(
            driver_user.telegram_id,
            phone_number=passenger_phone,
            first_name=passenger.full_name or "Yo'lovchi",
        )
        await remember_order_artifact(
            order.id, driver_user.id, "contact_message_id", contact_message.message_id
        )
    except Exception:
        pass
    async with database.SessionLocal() as session:
        session.add(
            OrderMessage(
                order_id=order.id,
                driver_user_id=driver_user.id,
                chat_id=passenger.telegram_id,
                message_id=passenger_accepted_message.message_id,
                contact_message_id=passenger_contact_message_id,
                status="accepted_passenger",
            )
        )
        await session.commit()
    await refresh_channel_trip(bot, selected_trip_id)
    await callback.message.edit_text(callback.message.text + ("\n\nStatus: tanlandi" if lang == "uz" else "\n\nСтатус: выбран"))
    await callback.answer("Haydovchi tanlandi" if lang == "uz" else "Водитель выбран")


@router.callback_query(F.data.startswith("order:"))
async def order_action(callback: CallbackQuery, bot: Bot) -> None:
    _prefix, action, raw_order_id = callback.data.split(":")
    if action == "skip":
        order_id = int(raw_order_id)
        async with database.SessionLocal() as session:
            driver_row = await session.execute(
                select(Driver, User).join(User, Driver.user_id == User.id).where(User.telegram_id == callback.from_user.id)
            )
            row = driver_row.first()
            driver = row[0] if row else None
            driver_user = row[1] if row else None
            order = await session.get(Order, order_id)
            if driver and driver_user and order:
                order_messages_result = await session.execute(
                    select(OrderMessage)
                    .where(OrderMessage.order_id == order.id)
                    .where(OrderMessage.driver_user_id == driver_user.id)
                    .where(OrderMessage.status == "sent")
                )
                for item in order_messages_result.scalars().all():
                    item.status = "rejected"
                await session.commit()
                if order.driver_id != driver.id:
                    try:
                        await callback.message.edit_reply_markup(reply_markup=None)
                    except Exception:
                        pass
            if driver and order and order.status == "searching_driver" and order.driver_id == driver.id:
                passenger = await session.get(User, order.passenger_id)
                order.status = "rejected"
                await session.commit()
                await callback.message.edit_text("❌ Klient vaqti bo'yicha so'rov rad etildi.")
                await bot.send_message(
                    passenger.telegram_id,
                    "Haydovchi tanlagan vaqtingizni tasdiqlamadi. Boshqa haydovchini tanlashingiz mumkin.",
                )
        await callback.answer("O'tkazib yuborildi")
        return

    order_id = int(raw_order_id)
    if action == "finish":
        async with database.SessionLocal() as session:
            driver_result = await session.execute(
                select(Driver)
                .join(User, Driver.user_id == User.id)
                .where(User.telegram_id == callback.from_user.id)
            )
            driver = driver_result.scalar_one_or_none()
            order = await session.get(Order, order_id)
            if not driver or not order or order.driver_id != driver.id or order.status != "accepted":
                await callback.answer("Bu safarni yakunlay olmaysiz.", show_alert=True)
                return
            passenger = await session.get(User, order.passenger_id)
            if order.completion_requested_at:
                await callback.answer("Tasdiqlash so'rovi allaqachon yuborilgan.", show_alert=True)
                return
            order.completion_requested_at = datetime.utcnow()
            await session.commit()
            passenger_lang = passenger.language or "uz"

        await bot.send_message(
            passenger.telegram_id,
            (
                "🏁 Haydovchi safar yakunlanganini bildirdi.\n\nSafar haqiqatan yakunlandimi? "
                "1 soat ichida javob bermasangiz, safar avtomatik yakunlanadi."
                if passenger_lang == "uz" else
                "🏁 Водитель сообщил о завершении поездки.\n\nПоездка действительно завершена? "
                "Если вы не ответите в течение часа, поездка завершится автоматически."
            ),
            reply_markup=completion_confirmation_keyboard(order_id, passenger_lang),
        )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Yo'lovchiga tasdiqlash yuborildi."
            if (await get_user_language(callback.from_user.id)) == "uz"
            else "Пассажиру отправлен запрос на подтверждение."
        )
        await callback.answer("Yuborildi")
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
            old_messages_result = await session.execute(
                select(OrderMessage)
                .where(OrderMessage.order_id == order.id)
                .where(OrderMessage.driver_user_id == driver_user.id)
            )
            old_messages = old_messages_result.scalars().all()
            messages_to_delete = []
            for item in old_messages:
                for message_id in (
                    item.message_id,
                    item.contact_message_id,
                    item.location_message_id,
                ):
                    if message_id:
                        messages_to_delete.append((item.chat_id, message_id))
            for item in old_messages:
                item.status = "cancelled"
            order.driver_id = None
            order.status = "searching_driver"
            await session.commit()
            cancelled_trip_id = trip.id if trip else None
            excluded_driver_id = driver.id

        for chat_id, message_id in set(messages_to_delete):
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception as exc:
                logging.warning("Bekor qilingan buyurtma xabari o'chirilmadi: %s", exc)
        await callback.message.edit_text(
            "❌ <b>Buyurtma bekor bo'ldi</b>\n\n"
            "Buyurtma boshqa haydovchilarga qayta yuborildi.",
            parse_mode="HTML",
        )
        await bot.send_message(
            passenger.telegram_id,
            "Haydovchi buyurtmani bekor qildi. Buyurtmangiz yana boshqa haydovchilarga yuborilmoqda.",
        )
        if cancelled_trip_id:
            await refresh_channel_trip(bot, cancelled_trip_id)
        await publish_order_to_channel(bot, order_id)
        await broadcast_order_to_drivers(bot, order_id, exclude_driver_id=excluded_driver_id)
        await callback.answer("Buyurtma bekor qilindi")
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
        if order.driver_id is not None and order.driver_id != driver.id:
            await callback.answer("Bu so'rov boshqa haydovchiga yuborilgan.", show_alert=True)
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
        claim_result = await session.execute(
            update(Order)
            .where(Order.id == order.id)
            .where(Order.status == "searching_driver")
            .where(or_(Order.driver_id.is_(None), Order.driver_id == driver.id))
            .values(driver_id=driver.id, status="accepted")
            .execution_options(synchronize_session=False)
        )
        if claim_result.rowcount != 1:
            await session.rollback()
            await callback.answer(
                "Bu buyurtmani boshqa haydovchi olib bo'ldi."
                if (driver_user.language or "uz") == "uz"
                else "Этот заказ уже принял другой водитель.",
                show_alert=True,
            )
            return
        if trip:
            if trip.time in {"🕐 Klient vaqti", "🕐 Время клиента"}:
                trip.time = order.time
            trip.available_seats -= order.passengers_count
            if trip.available_seats == 0:
                trip.status = "full"
        await session.commit()
        selected_trip_id = trip.id if trip else None
        order_channel_message_id = order.channel_message_id

    price_text = f"{trip.price_per_person:,}".replace(",", " ") + " so'm" if trip else "Kelishilgan holda"
    car_color_text = trip.roof_luggage if trip else "-"
    d_phone = driver_user.phone or ""
    if d_phone and not d_phone.startswith("+"): d_phone = "+" + d_phone
    p_phone = passenger.phone or ""
    if p_phone and not p_phone.startswith("+"): p_phone = "+" + p_phone
    passenger_text = (
        "✅ <b>Haydovchi topildi!</b>\n\n"
        f"👤 Ism: <b>{driver_user.full_name}</b>\n"
        f"📞 Telefon: <b>{d_phone}</b>\n"
        f"🚘 Mashina: <b>{driver.car_model} {driver.car_color}</b>\n"
        f"🔢 Raqam: <b>{driver.car_number}</b>\n"
        f"💰 Narx: <b>{price_text}</b>\n"
        f"🧳 Tom bagaj: <b>{car_color_text}</b>"
    )
    driver_text = (
        "✅ <b>Buyurtmani qabul qildingiz!</b>\n\n"
        f"👤 Yo'lovchi: <b>{passenger.full_name}</b>\n"
        f"🛣 Yo'nalish: <b>{order.from_city} → {order.to_city}</b>\n"
        f"📅 Sana/vaqt: <b>{order.date} {order.time}</b>\n"
        f"👥 Yo'lovchi soni: <b>{order.passengers_count}</b>\n"
        f"🧳 Tom bagaj kerak: <b>{order.roof_luggage or '-'}</b>"
    )
    if (passenger.language or "uz") == "ru":
        passenger_text = (
            "✅ <b>Водитель найден!</b>\n\n"
            f"👤 Имя: <b>{driver_user.full_name}</b>\n"
            f"📞 Телефон: <b>{d_phone}</b>\n"
            f"🚘 Машина: <b>{driver.car_model} {driver.car_color}</b>\n"
            f"🔢 Номер: <b>{driver.car_number}</b>\n"
            f"💰 Цена: <b>{price_text}</b>\n"
            f"🧳 Багажник на крыше: <b>{car_color_text}</b>"
        )
    if (driver_user.language or "uz") == "ru":
        driver_text = (
            "✅ <b>Вы приняли заказ!</b>\n\n"
            f"👤 Пассажир: <b>{passenger.full_name}</b>\n"
            f"🛣 Маршрут: <b>{order.from_city} → {order.to_city}</b>\n"
            f"📅 Дата/время: <b>{order.date} {order.time}</b>\n"
            f"👥 Пассажиров: <b>{order.passengers_count}</b>\n"
            f"🧳 Нужен багажник: <b>{order.roof_luggage or '-'}</b>"
        )
    passenger_accepted_message = await bot.send_message(passenger.telegram_id, passenger_text, parse_mode="HTML")
    passenger_contact_message_id = None
    try:
        passenger_contact_message = await bot.send_contact(
            passenger.telegram_id,
            phone_number=d_phone,
            first_name=driver_user.full_name or "Haydovchi",
        )
        passenger_contact_message_id = passenger_contact_message.message_id
    except Exception:
        pass
    await bot.send_message(
        driver_user.telegram_id,
        driver_text,
        reply_markup=accepted_order_keyboard(order.id, passenger.telegram_id, driver_user.language or "uz"),
        parse_mode="HTML",
    )
    try:
        contact_message = await bot.send_contact(
            driver_user.telegram_id,
            phone_number=p_phone,
            first_name=passenger.full_name or "Yo'lovchi",
        )
        await remember_order_artifact(
            order.id, driver_user.id, "contact_message_id", contact_message.message_id
        )
    except Exception:
        pass
    async with database.SessionLocal() as session:
        session.add(
            OrderMessage(
                order_id=order.id,
                driver_user_id=driver_user.id,
                chat_id=passenger.telegram_id,
                message_id=passenger_accepted_message.message_id,
                contact_message_id=passenger_contact_message_id,
                status="accepted_passenger",
            )
        )
        await session.commit()
    if selected_trip_id:
        await refresh_channel_trip(bot, selected_trip_id)
    await close_order_messages(bot, order.id, driver_user.id)
    if order_channel_message_id and config.channel_id:
        try:
            await bot.edit_message_text(
                "✅ Bu buyurtma allaqachon qabul qilindi.",
                chat_id=config.channel_id,
                message_id=order_channel_message_id,
            )
        except Exception as exc:
            logging.warning("Kanal buyurtma xabari yangilanmadi: %s", exc)
    await callback.answer("Buyurtma qabul qilindi")


@router.callback_query(F.data.startswith("complete:confirm:"))
async def passenger_confirm_completion(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.split(":")[-1])
    lang = await get_user_language(callback.from_user.id)
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(Order, User)
            .join(User, Order.passenger_id == User.id)
            .where(Order.id == order_id)
            .where(User.telegram_id == callback.from_user.id)
        )
        row = result.first()
        if not row or row[0].status != "accepted":
            await callback.answer("Bu safar allaqachon yopilgan." if lang == "uz" else "Поездка уже закрыта.", show_alert=True)
            return
        order, _passenger = row
        order.status = "completed"
        order.completed_at = datetime.utcnow()
        await session.commit()
    await callback.message.edit_text(
        "✅ Safar yakunlandi. Haydovchini baholang:"
        if lang == "uz" else "✅ Поездка завершена. Оцените водителя:",
        reply_markup=rating_keyboard(order_id),
    )
    await callback.answer("Rahmat" if lang == "uz" else "Спасибо")


@router.callback_query(F.data.startswith("rating:"))
async def rate_driver(callback: CallbackQuery) -> None:
    _prefix, raw_stars, raw_order_id = callback.data.split(":")
    stars = int(raw_stars)
    order_id = int(raw_order_id)
    lang = await get_user_language(callback.from_user.id)
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(Order, User)
            .join(User, Order.passenger_id == User.id)
            .where(Order.id == order_id)
            .where(User.telegram_id == callback.from_user.id)
        )
        row = result.first()
        if not row or row[0].status != "completed" or not row[0].driver_id:
            await callback.answer("Baholab bo'lmaydi." if lang == "uz" else "Оценка недоступна.", show_alert=True)
            return
        order, passenger = row
        existing = await session.scalar(select(Rating).where(Rating.order_id == order.id))
        if existing:
            await callback.answer("Siz allaqachon baholagansiz." if lang == "uz" else "Вы уже поставили оценку.", show_alert=True)
            return
        session.add(Rating(order_id=order.id, passenger_id=passenger.id, driver_id=order.driver_id, stars=stars))
        await session.commit()
    await callback.message.edit_text(
        f"✅ Baho qabul qilindi: {stars} ⭐" if lang == "uz" else f"✅ Оценка принята: {stars} ⭐"
    )
    await callback.answer("Rahmat" if lang == "uz" else "Спасибо")


@router.callback_query(F.data.startswith("complete:complaint:"))
async def start_complaint(callback: CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[-1])
    lang = await get_user_language(callback.from_user.id)
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(Order)
            .join(User, Order.passenger_id == User.id)
            .where(Order.id == order_id)
            .where(User.telegram_id == callback.from_user.id)
        )
        if not result.scalar_one_or_none():
            await callback.answer("Ruxsat yo'q." if lang == "uz" else "Нет доступа.", show_alert=True)
            return
    await callback.message.answer(
        "Shikoyat sababini tanlang:" if lang == "uz" else "Выберите причину жалобы:",
        reply_markup=complaint_reason_keyboard(order_id, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("complaint_reason:"))
async def choose_complaint_reason(callback: CallbackQuery, state: FSMContext) -> None:
    _prefix, reason, raw_order_id = callback.data.split(":")
    lang = await get_user_language(callback.from_user.id)
    await state.update_data(complaint_order_id=int(raw_order_id), complaint_reason=reason, lang=lang)
    await state.set_state(ComplaintFlow.comment)
    await callback.message.edit_text(
        "Shikoyat tafsilotlarini yozing:" if lang == "uz" else "Опишите подробности жалобы:"
    )
    await callback.answer()


@router.message(ComplaintFlow.comment)
async def submit_complaint(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    order_id = data["complaint_order_id"]
    reason = data["complaint_reason"]
    lang = data.get("lang", "uz")
    comment = message.text or "-"
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(Order, User, Driver)
            .join(User, Order.passenger_id == User.id)
            .join(Driver, Order.driver_id == Driver.id)
            .where(Order.id == order_id)
            .where(User.telegram_id == message.from_user.id)
        )
        row = result.first()
        if not row:
            await state.clear()
            await message.answer("Buyurtma topilmadi." if lang == "uz" else "Заказ не найден.")
            return
        order, passenger, driver = row
        driver_user = await session.get(User, driver.user_id)
        complaint = Complaint(
            order_id=order.id,
            passenger_id=passenger.id,
            driver_id=driver.id,
            reason=reason,
            comment=comment,
        )
        order.status = "disputed"
        session.add(complaint)
        await session.commit()
        await session.refresh(complaint)

    admin_text = (
        f"⚠️ <b>Yangi shikoyat #{complaint.id}</b>\n\n"
        f"🧾 Buyurtma: #{order.id}\n"
        f"🛣 Yo'nalish: {order.from_city} → {order.to_city}\n"
        f"📅 Sana/vaqt: {order.date} {order.time}\n\n"
        f"👤 Yo'lovchi: {passenger.full_name or '-'}\n"
        f"📞 Yo'lovchi telefoni: {passenger.phone or '-'}\n\n"
        f"🚘 Haydovchi: {driver_user.full_name or '-'}\n"
        f"📞 Haydovchi telefoni: {driver_user.phone or '-'}\n"
        f"🚗 Mashina: {driver.car_model} {driver.car_color}\n"
        f"🔢 Davlat raqami: {driver.car_number}\n\n"
        f"⚠️ Sabab: {reason}\n"
        f"💬 Izoh: {comment}"
    )
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                admin_text,
                reply_markup=admin_driver_manage_keyboard(driver.id, driver.status),
                parse_mode="HTML",
            )
        except Exception as exc:
            logging.error("Shikoyat adminga yuborilmadi: %s", exc)
    await state.clear()
    await message.answer(
        "✅ Shikoyatingiz adminga yuborildi." if lang == "uz" else "✅ Жалоба отправлена администратору."
    )


@router.message(F.text == "Mening yo'nalishlarim")
@router.message(F.text == "Мои маршруты")
async def my_trips(message: Message) -> None:
    lang = await get_user_language(message.from_user.id)
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
        await message.answer("Hali yo'nalish qo'shmagansiz." if lang == "uz" else "Вы ещё не добавили ни одного маршрута.")
        return
    text = "Oxirgi yo'nalishlaringiz:\n\n" if lang == "uz" else "Ваши последние маршруты:\n\n"
    for trip, _driver, _user in trips:
        text += (
            f"#{trip.id}: {trip.from_city} → {trip.to_city}, {trip.date} {trip.time}, "
            + (f"joy: {trip.available_seats}, narx: {trip.price_per_person} so'm, tom bagaj: {trip.roof_luggage}, status: {trip.status}\n"
               if lang == "uz" else
               f"мест: {trip.available_seats}, цена: {trip.price_per_person} сум, багажник: {trip.roof_luggage}, статус: {trip.status}\n")
        )
    await message.answer(text)


@router.message(F.text == "Mos buyurtmalar")
@router.message(F.text == "Подходящие заказы")
async def matching_orders_for_driver(message: Message) -> None:
    lang = await get_user_language(message.from_user.id)
    tz = timezone(timedelta(hours=5))
    now = datetime.now(tz)
    now_date = now.date().isoformat()
    now_time = now.strftime("%H:%M")
    flexible_times = ["⚡ Srochniy", "⚡ Срочно", "🕐 Klient vaqti", "🕐 Время клиента"]
    async with database.SessionLocal() as session:
        driver_result = await session.execute(
            select(Driver, User)
            .join(User, Driver.user_id == User.id)
            .where(User.telegram_id == message.from_user.id)
        )
        row = driver_result.first()
        if not row:
            await message.answer("Siz haydovchi sifatida ro'yxatdan o'tmagansiz." if lang == "uz" else "Вы не зарегистрированы как водитель.")
            return
        driver, _user = row
        if driver.status != "active":
            await message.answer("Avval admin sizni haydovchi sifatida tasdiqlashi kerak." if lang == "uz" else "Сначала администратор должен подтвердить вас как водителя.")
            return

        trips_result = await session.execute(
            select(DriverTrip)
            .where(DriverTrip.driver_id == driver.id)
            .where(DriverTrip.status == "active")
            .where(
                (DriverTrip.date > now_date)
                | (
                    (DriverTrip.date == now_date)
                    & ((DriverTrip.time > now_time) | DriverTrip.time.in_(flexible_times))
                )
            )
        )
        trips = trips_result.scalars().all()
        if not trips:
            await message.answer("Avval yo'nalish qo'shing." if lang == "uz" else "Сначала добавьте маршрут.")
            return

        conditions = []
        for trip in trips:
            conditions.append(
                (Order.from_city == trip.from_city)
                & (Order.to_city == trip.to_city)
                & (Order.passengers_count <= trip.available_seats)
            )

        from sqlalchemy import or_

        expired_orders_result = await session.execute(
            select(Order)
            .where(Order.status == "searching_driver")
            .where(
                (Order.date < now_date)
                | (
                    (Order.date == now_date)
                    & (Order.time <= now_time)
                    & Order.time.not_in(flexible_times)
                )
            )
        )
        for expired_order in expired_orders_result.scalars().all():
            expired_order.status = "expired"
        await session.commit()

        orders_result = await session.execute(
            select(Order)
            .where(Order.status == "searching_driver")
            .where(
                (Order.date > now_date)
                | (
                    (Order.date == now_date)
                    & ((Order.time > now_time) | Order.time.in_(flexible_times))
                )
            )
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
        await message.answer("Hozircha yo'nalishlaringizga mos ochiq buyurtma yo'q." if lang == "uz" else "Пока нет открытых заказов, подходящих вашим маршрутам.")
        return

    await message.answer(f"{len(orders)} ta mos buyurtma topildi:" if lang == "uz" else f"Найдено подходящих заказов: {len(orders)}")
    for order in orders:
        await message.answer(format_order_for_driver(order, locations.get(order.id)), reply_markup=order_keyboard(order.id, lang), parse_mode="HTML")


@router.message(F.text == "Buyurtmalarim")
@router.message(F.text == "📦 Buyurtmalarim")
@router.message(F.text == "Мои заказы")
@router.message(F.text == "📦 Мои заказы")
async def my_orders(message: Message) -> None:
    lang = await get_user_language(message.from_user.id)
    async with database.SessionLocal() as session:
        user_result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_result.scalar_one_or_none()
        if not user:
            await message.answer("Siz hali ro'yxatdan o'tmagansiz." if lang == "uz" else "Вы ещё не зарегистрированы.")
            return

        result = await session.execute(
            select(Order)
            .where(Order.passenger_id == user.id)
            .order_by(Order.id.desc())
            .limit(10)
        )
        orders = result.scalars().all()

    if not orders:
        await message.answer("Hali buyurtmangiz yo'q." if lang == "uz" else "У вас пока нет заказов.")
        return
    await message.answer("Oxirgi buyurtmalaringiz:" if lang == "uz" else "Ваши последние заказы:")
    for order in orders:
        text = (
            f"#{order.id}: {order.from_city} -> {order.to_city}, "
            f"{order.date} {order.time}, {'status' if lang == 'uz' else 'статус'}: {order.status}\n"
        )
        keyboard = (
            passenger_order_keyboard(order.id, lang)
            if order.status in {"searching_driver", "accepted"}
            else None
        )
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("passenger_order:cancel:"))
async def passenger_cancel_order(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.split(":")[-1])
    lang = await get_user_language(callback.from_user.id)
    success, reason = await cancel_order_by_passenger_or_admin(
        bot, order_id, callback.from_user.id
    )
    if not success:
        await callback.answer(
            reason if lang == "uz" else "Этот заказ нельзя отменить.",
            show_alert=True,
        )
        return
    await callback.message.edit_text(
        "❌ Buyurtma bekor qilindi." if lang == "uz" else "❌ Заказ отменён."
    )
    await callback.answer("Bekor qilindi" if lang == "uz" else "Отменено")


@router.message(F.text == "Profil")
@router.message(F.text == "👤 Profil")
@router.message(F.text == "Профиль")
@router.message(F.text == "👤 Профиль")
async def profile(message: Message) -> None:
    lang = await get_user_language(message.from_user.id)
    async with database.SessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = result.scalar_one_or_none()
    if not user:
        await message.answer("Profil topilmadi. /start bosing." if lang == "uz" else "Профиль не найден. Нажмите /start.")
        return
    await message.answer(
        (f"Profil\n\nIsm: {user.full_name or '-'}\nTelefon: {user.phone or '-'}\nRol: {user.role or '-'}")
        if lang == "uz" else
        (f"Профиль\n\nИмя: {user.full_name or '-'}\nТелефон: {user.phone or '-'}\nРоль: {user.role or '-'}")
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
    await message.answer(
        "📊 Qaysi analizni ko'ramiz?" if lang == "uz" else "📊 Какую аналитику открыть?",
        reply_markup=admin_analysis_keyboard(lang),
    )
    return
    tz = timezone(timedelta(hours=5))
    today_str = datetime.now(tz).date().isoformat()
    week_since = datetime.utcnow() - timedelta(days=7)
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
        week_orders_count = await session.scalar(select(func.count(Order.id)).where(Order.created_at >= week_since))
        week_orders_result = await session.execute(select(Order).where(Order.created_at >= week_since))
        week_orders = week_orders_result.scalars().all()
        week_order_ids = [order.id for order in week_orders]
        week_orders_by_id = {order.id: order for order in week_orders}

        order_messages = []
        if week_order_ids:
            order_messages_result = await session.execute(
                select(OrderMessage).where(OrderMessage.order_id.in_(week_order_ids))
            )
            order_messages = order_messages_result.scalars().all()

        users_result = await session.execute(select(User))
        users_by_id = {user.id: user for user in users_result.scalars().all()}
        drivers_result = await session.execute(select(Driver))
        drivers = drivers_result.scalars().all()
        drivers_by_id = {driver.id: driver for driver in drivers}
        driver_by_user_id = {driver.user_id: driver for driver in drivers}
        ratings_result = await session.execute(select(Rating))
        ratings = ratings_result.scalars().all()

    driver_stats: dict[int, dict[str, Any]] = {}
    passenger_stats: dict[int, dict[str, Any]] = {}
    driver_ratings: dict[int, list[int]] = {}
    passenger_given_ratings: dict[int, list[int]] = {}

    for rating in ratings:
        driver_ratings.setdefault(rating.driver_id, []).append(rating.stars)
        passenger_given_ratings.setdefault(rating.passenger_id, []).append(rating.stars)

    for order in week_orders:
        passenger_stat = passenger_stats.setdefault(
            order.passenger_id,
            {"orders": 0, "accepted": 0, "completed": 0, "cancelled": 0},
        )
        passenger_stat["orders"] += 1
        if order.status in {"accepted", "completed", "disputed"}:
            passenger_stat["accepted"] += 1
        if order.status == "completed":
            passenger_stat["completed"] += 1
        if order.status in {"cancelled", "expired", "rejected"}:
            passenger_stat["cancelled"] += 1

        if order.driver_id:
            driver_stat = driver_stats.setdefault(
                order.driver_id,
                {"accepted": 0, "completed": 0, "rejected": 0},
            )
            if order.status in {"accepted", "completed", "disputed"}:
                driver_stat["accepted"] += 1
            if order.status == "completed":
                driver_stat["completed"] += 1
            if order.status == "rejected":
                driver_stat["rejected"] += 1

    for order_message in order_messages:
        driver = driver_by_user_id.get(order_message.driver_user_id)
        if not driver:
            continue
        driver_stat = driver_stats.setdefault(
            driver.id,
            {"accepted": 0, "completed": 0, "rejected": 0},
        )
        order = week_orders_by_id.get(order_message.order_id)
        if order and order.driver_id == driver.id and order.status == "rejected":
            continue
        if order_message.status in {"rejected", "cancelled"}:
            driver_stat["rejected"] += 1

    def _short_name(user: User | None) -> str:
        return html.escape((user.full_name or user.phone or "-")[:32]) if user else "-"

    def _avg(values: list[int]) -> str:
        return f"{(sum(values) / len(values)):.1f}" if values else "-"

    top_drivers = sorted(
        driver_stats.items(),
        key=lambda item: (item[1]["accepted"], item[1]["completed"], -item[1]["rejected"]),
        reverse=True,
    )[:8]
    top_passengers = sorted(
        passenger_stats.items(),
        key=lambda item: (item[1]["completed"], item[1]["accepted"], item[1]["orders"]),
        reverse=True,
    )[:8]

    if lang == "ru":
        driver_lines = [
            (
                f"{index}. {_short_name(users_by_id.get(drivers_by_id[driver_id].user_id))} — "
                f"принял: {stats['accepted']}, завершил: {stats['completed']}, отказ: {stats['rejected']}, "
                f"⭐ {_avg(driver_ratings.get(driver_id, []))}"
            )
            for index, (driver_id, stats) in enumerate(top_drivers, start=1)
            if driver_id in drivers_by_id
        ]
        passenger_lines = [
            (
                f"{index}. {_short_name(users_by_id.get(passenger_id))} — "
                f"заказов: {stats['orders']}, поездок: {stats['completed']}, активно/принято: {stats['accepted']}, "
                f"отменено: {stats['cancelled']}, оценок дал: {len(passenger_given_ratings.get(passenger_id, []))}"
            )
            for index, (passenger_id, stats) in enumerate(top_passengers, start=1)
        ]
    else:
        driver_lines = [
            (
                f"{index}. {_short_name(users_by_id.get(drivers_by_id[driver_id].user_id))} — "
                f"oldi: {stats['accepted']}, yakunladi: {stats['completed']}, rad: {stats['rejected']}, "
                f"⭐ {_avg(driver_ratings.get(driver_id, []))}"
            )
            for index, (driver_id, stats) in enumerate(top_drivers, start=1)
            if driver_id in drivers_by_id
        ]
        passenger_lines = [
            (
                f"{index}. {_short_name(users_by_id.get(passenger_id))} — "
                f"buyurtma: {stats['orders']}, safar: {stats['completed']}, qabul qilingan: {stats['accepted']}, "
                f"bekor/rad: {stats['cancelled']}, baho berdi: {len(passenger_given_ratings.get(passenger_id, []))}"
            )
            for index, (passenger_id, stats) in enumerate(top_passengers, start=1)
        ]
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
            f"Qabul qilingan buyurtmalar: {accepted_orders_count or 0}\n\n"
            "Top haydovchilar (7 kun):\n"
            + ("\n".join(driver_lines) if driver_lines else "Hali ma'lumot yo'q.")
            + "\n\nTop yo'lovchilar (7 kun):\n"
            + ("\n".join(passenger_lines) if passenger_lines else "Hali ma'lumot yo'q.")
        )
    if lang == "ru":
        text += (
            "\n\nТоп водителей за 7 дней:\n"
            + ("\n".join(driver_lines) if driver_lines else "Пока нет данных.")
            + "\n\nТоп пассажиров за 7 дней:\n"
            + ("\n".join(passenger_lines) if passenger_lines else "Пока нет данных.")
        )
    await message.answer(
        text,
        reply_markup=main_menu(True, lang),
    )


async def build_admin_analysis_text(section: str, lang: str = "uz") -> str:
    week_since = datetime.utcnow() - timedelta(days=7)
    today_str = datetime.now(timezone(timedelta(hours=5))).date().isoformat()

    async with database.SessionLocal() as session:
        users_count = await session.scalar(select(func.count(User.id)))
        passengers_count = await session.scalar(select(func.count(User.id)).where(User.role == "passenger"))
        drivers_count = await session.scalar(select(func.count(Driver.id)))
        active_drivers_count = await session.scalar(select(func.count(Driver.id)).where(Driver.status == "active"))
        pending_drivers_count = await session.scalar(select(func.count(Driver.id)).where(Driver.status == "pending"))
        active_trips_count = await session.scalar(select(func.count(DriverTrip.id)).where(DriverTrip.status == "active"))
        orders_count = await session.scalar(select(func.count(Order.id)))
        today_orders_count = await session.scalar(select(func.count(Order.id)).where(Order.date == today_str))
        week_orders_result = await session.execute(select(Order).where(Order.created_at >= week_since))
        week_orders = week_orders_result.scalars().all()
        week_order_ids = [order.id for order in week_orders]
        week_orders_by_id = {order.id: order for order in week_orders}

        order_messages = []
        if week_order_ids:
            order_messages_result = await session.execute(
                select(OrderMessage).where(OrderMessage.order_id.in_(week_order_ids))
            )
            order_messages = order_messages_result.scalars().all()

        users_result = await session.execute(select(User))
        users_by_id = {user.id: user for user in users_result.scalars().all()}
        drivers_result = await session.execute(select(Driver))
        drivers = drivers_result.scalars().all()
        drivers_by_id = {driver.id: driver for driver in drivers}
        driver_by_user_id = {driver.user_id: driver for driver in drivers}
        ratings_result = await session.execute(select(Rating))
        ratings = ratings_result.scalars().all()
        complaints_result = await session.execute(select(Complaint))
        complaints = complaints_result.scalars().all()

    def short_user(user: User | None) -> str:
        return html.escape((user.full_name or user.phone or "-")[:32]) if user else "-"

    def avg(values: list[int]) -> str:
        return f"{sum(values) / len(values):.1f}" if values else "-"

    def percent(part: int, total: int) -> str:
        return f"{(part * 100 / total):.0f}%" if total else "0%"

    driver_stats: dict[int, dict[str, int]] = {}
    passenger_stats: dict[int, dict[str, int]] = {}
    driver_ratings: dict[int, list[int]] = {}
    passenger_ratings: dict[int, list[int]] = {}
    driver_complaints: dict[int, int] = {}

    for rating in ratings:
        driver_ratings.setdefault(rating.driver_id, []).append(rating.stars)
        passenger_ratings.setdefault(rating.passenger_id, []).append(rating.stars)

    for complaint in complaints:
        driver_complaints[complaint.driver_id] = driver_complaints.get(complaint.driver_id, 0) + 1

    for order in week_orders:
        passenger_stat = passenger_stats.setdefault(
            order.passenger_id,
            {"orders": 0, "accepted": 0, "completed": 0, "cancelled": 0, "open": 0},
        )
        passenger_stat["orders"] += 1
        if order.status == "searching_driver":
            passenger_stat["open"] += 1
        if order.status in {"accepted", "completed", "disputed"}:
            passenger_stat["accepted"] += 1
        if order.status == "completed":
            passenger_stat["completed"] += 1
        if order.status in {"cancelled", "expired", "rejected"}:
            passenger_stat["cancelled"] += 1

        if order.driver_id:
            driver_stat = driver_stats.setdefault(
                order.driver_id,
                {"accepted": 0, "completed": 0, "rejected": 0, "active": 0},
            )
            if order.status == "accepted":
                driver_stat["active"] += 1
            if order.status in {"accepted", "completed", "disputed"}:
                driver_stat["accepted"] += 1
            if order.status == "completed":
                driver_stat["completed"] += 1
            if order.status == "rejected":
                driver_stat["rejected"] += 1

    for order_message in order_messages:
        driver = driver_by_user_id.get(order_message.driver_user_id)
        if not driver:
            continue
        driver_stat = driver_stats.setdefault(
            driver.id,
            {"accepted": 0, "completed": 0, "rejected": 0, "active": 0},
        )
        order = week_orders_by_id.get(order_message.order_id)
        if order and order.driver_id == driver.id and order.status == "rejected":
            continue
        if order_message.status in {"rejected", "cancelled"}:
            driver_stat["rejected"] += 1

    week_total = len(week_orders)
    week_open = sum(1 for order in week_orders if order.status == "searching_driver")
    week_accepted = sum(1 for order in week_orders if order.status in {"accepted", "completed", "disputed"})
    week_completed = sum(1 for order in week_orders if order.status == "completed")
    week_cancelled = sum(1 for order in week_orders if order.status in {"cancelled", "expired", "rejected"})
    week_disputed = sum(1 for order in week_orders if order.status == "disputed")

    if section == "summary":
        if lang == "ru":
            return (
                "📈 <b>Общая аналитика</b>\n\n"
                f"Пользователи: <b>{users_count or 0}</b>\n"
                f"Пассажиры: <b>{passengers_count or 0}</b>\n"
                f"Водители всего: <b>{drivers_count or 0}</b>\n"
                f"Активные водители: <b>{active_drivers_count or 0}</b>\n"
                f"На проверке: <b>{pending_drivers_count or 0}</b>\n"
                f"Активные маршруты: <b>{active_trips_count or 0}</b>\n\n"
                f"Заказы всего: <b>{orders_count or 0}</b>\n"
                f"Сегодня: <b>{today_orders_count or 0}</b>\n"
                f"За 7 дней: <b>{week_total}</b>\n"
                f"Открытые: <b>{week_open}</b>\n"
                f"Принятые: <b>{week_accepted}</b> ({percent(week_accepted, week_total)})\n"
                f"Завершённые: <b>{week_completed}</b> ({percent(week_completed, week_total)})\n"
                f"Отменённые/истёкшие: <b>{week_cancelled}</b>\n"
                f"Спорные: <b>{week_disputed}</b>"
            )
        return (
            "📈 <b>Umumiy analiz</b>\n\n"
            f"Foydalanuvchilar: <b>{users_count or 0}</b>\n"
            f"Yo'lovchilar: <b>{passengers_count or 0}</b>\n"
            f"Haydovchilar jami: <b>{drivers_count or 0}</b>\n"
            f"Tasdiqlangan haydovchilar: <b>{active_drivers_count or 0}</b>\n"
            f"Tasdiq kutayotganlar: <b>{pending_drivers_count or 0}</b>\n"
            f"Aktiv yo'nalishlar: <b>{active_trips_count or 0}</b>\n\n"
            f"Buyurtmalar jami: <b>{orders_count or 0}</b>\n"
            f"Bugun: <b>{today_orders_count or 0}</b>\n"
            f"7 kunda: <b>{week_total}</b>\n"
            f"Ochiq: <b>{week_open}</b>\n"
            f"Qabul qilingan: <b>{week_accepted}</b> ({percent(week_accepted, week_total)})\n"
            f"Yakunlangan: <b>{week_completed}</b> ({percent(week_completed, week_total)})\n"
            f"Bekor/rad/vaqti o'tgan: <b>{week_cancelled}</b>\n"
            f"Shikoyatli: <b>{week_disputed}</b>"
        )

    if section == "drivers":
        top_drivers = sorted(
            driver_stats.items(),
            key=lambda item: (item[1]["accepted"], item[1]["completed"], -item[1]["rejected"]),
            reverse=True,
        )[:15]
        lines = []
        accepted_label = "Oldi" if lang == "uz" else "Принял"
        completed_label = "Yakunladi" if lang == "uz" else "Завершил"
        active_label = "Aktiv" if lang == "uz" else "Активно"
        rejected_label = "Rad" if lang == "uz" else "Отказ"
        for index, (driver_id, stats) in enumerate(top_drivers, start=1):
            driver = drivers_by_id.get(driver_id)
            if not driver:
                continue
            name = short_user(users_by_id.get(driver.user_id))
            lines.append(
                f"{index}. <b>{name}</b>\n"
                f"   {accepted_label}: {stats['accepted']} | {completed_label}: {stats['completed']} | {active_label}: {stats['active']} | {rejected_label}: {stats['rejected']} | ⭐ {avg(driver_ratings.get(driver_id, []))}"
            )
        title = "🚕 <b>Haydovchilar analizi — 7 kun</b>" if lang == "uz" else "🚕 <b>Аналитика водителей — 7 дней</b>"
        empty = "Hali ma'lumot yo'q." if lang == "uz" else "Пока нет данных."
        return title + "\n\n" + ("\n".join(lines) if lines else empty)

    if section == "passengers":
        top_passengers = sorted(
            passenger_stats.items(),
            key=lambda item: (item[1]["completed"], item[1]["accepted"], item[1]["orders"]),
            reverse=True,
        )[:15]
        lines = []
        orders_label = "Buyurtma" if lang == "uz" else "Заказы"
        trips_label = "Safar" if lang == "uz" else "Поездки"
        accepted_label = "Qabul" if lang == "uz" else "Принято"
        open_label = "Ochiq" if lang == "uz" else "Открыто"
        cancelled_label = "Bekor/rad" if lang == "uz" else "Отмена/отказ"
        for index, (passenger_id, stats) in enumerate(top_passengers, start=1):
            name = short_user(users_by_id.get(passenger_id))
            lines.append(
                f"{index}. <b>{name}</b>\n"
                f"   {orders_label}: {stats['orders']} | {trips_label}: {stats['completed']} | {accepted_label}: {stats['accepted']} | {open_label}: {stats['open']} | {cancelled_label}: {stats['cancelled']}"
            )
        title = "🧍 <b>Yo'lovchilar analizi — 7 kun</b>" if lang == "uz" else "🧍 <b>Аналитика пассажиров — 7 дней</b>"
        empty = "Hali ma'lumot yo'q." if lang == "uz" else "Пока нет данных."
        return title + "\n\n" + ("\n".join(lines) if lines else empty)

    top_rated = sorted(
        drivers_by_id.keys(),
        key=lambda driver_id: (
            len(driver_ratings.get(driver_id, [])),
            sum(driver_ratings.get(driver_id, [])) / len(driver_ratings.get(driver_id, [])) if driver_ratings.get(driver_id) else 0,
        ),
        reverse=True,
    )[:10]
    lines = []
    for index, driver_id in enumerate(top_rated, start=1):
        driver = drivers_by_id.get(driver_id)
        if not driver:
            continue
        stars = driver_ratings.get(driver_id, [])
        name = short_user(users_by_id.get(driver.user_id))
        rating_label = "ta baho" if lang == "uz" else "оценок"
        complaint_label = "shikoyat" if lang == "uz" else "жалоб"
        lines.append(
            f"{index}. <b>{name}</b> — ⭐ {avg(stars)} ({len(stars)} {rating_label}), {complaint_label}: {driver_complaints.get(driver_id, 0)}"
        )
    complaints_total = len(complaints)
    title = "⭐ <b>Reyting va shikoyatlar</b>" if lang == "uz" else "⭐ <b>Рейтинг и жалобы</b>"
    empty = "Hali reyting yo'q." if lang == "uz" else "Пока нет рейтингов."
    total_complaints_label = "Jami shikoyat" if lang == "uz" else "Всего жалоб"
    return title + f"\n\n{total_complaints_label}: <b>{complaints_total}</b>\n\n" + ("\n".join(lines) if lines else empty)


@router.callback_query(F.data.startswith("admin_analysis:"))
async def admin_analysis_callback(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Siz admin emassiz.", show_alert=True)
        return
    section = callback.data.split(":")[-1]
    lang = await get_user_language(callback.from_user.id)
    if section == "close":
        await callback.message.delete()
        await callback.answer()
        return
    text = await build_admin_analysis_text(section, lang)
    await callback.message.edit_text(
        text,
        reply_markup=admin_analysis_keyboard(lang),
        parse_mode="HTML",
    )
    await callback.answer()


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
    await message.answer(
        f"📦 Ochiq buyurtmalar: {len(rows)} ta" if lang == "uz"
        else f"📦 Открытых заказов: {len(rows)}",
        reply_markup=main_menu(True, lang),
    )
    for order, passenger in rows:
        text = (
            f"#{order.id} | {order.from_city} → {order.to_city}\n"
            f"{order.date} {order.time}, {order.passengers_count} "
            f"{'kishi' if lang == 'uz' else 'пасс.'}\n"
            f"{'Yo‘lovchi' if lang == 'uz' else 'Пассажир'}: "
            f"{passenger.full_name or '-'} | {passenger.phone or '-'}"
        )
        await message.answer(
            text,
            reply_markup=admin_order_keyboard(order.id, passenger.telegram_id, lang),
        )


@router.callback_query(F.data.startswith("admin_order:cancel:"))
async def admin_cancel_order(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Siz admin emassiz.", show_alert=True)
        return
    order_id = int(callback.data.split(":")[-1])
    lang = await get_user_language(callback.from_user.id)
    success, reason = await cancel_order_by_passenger_or_admin(
        bot, order_id, callback.from_user.id, admin=True
    )
    if not success:
        await callback.answer(
            reason if lang == "uz" else "Заказ уже закрыт.", show_alert=True
        )
        return
    await callback.message.edit_text(
        "❌ Buyurtma admin tomonidan yopildi."
        if lang == "uz" else "❌ Заказ закрыт администратором."
    )
    await callback.answer("Buyurtma yopildi" if lang == "uz" else "Заказ закрыт")


@router.message(F.text == "Asosiy menyu")
async def back_to_main(message: Message, state: FSMContext) -> None:
    await state.clear()
    lang = await get_user_language(message.from_user.id)
    await message.answer(
        "Asosiy menyu" if lang == "uz" else "Главное меню",
        reply_markup=main_menu(is_admin(message.from_user.id), lang),
    )


async def auto_expire_trips(bot: Bot) -> None:
    """Har 30 daqiqada eskirgan yo'nalish va buyurtmalarni o'chiradi."""
    while True:
        try:
            tz = timezone(timedelta(hours=5))
            now = datetime.now(tz)
            now_date = now.date().isoformat()
            now_time = now.strftime("%H:%M")

            async with database.SessionLocal() as session:
                # Eskirgan yo'nalishlar
                result = await session.execute(
                    select(DriverTrip)
                    .where(DriverTrip.status == "active")
                    .where(
                        (DriverTrip.date < now_date) |
                        ((DriverTrip.date == now_date) & (DriverTrip.time <= now_time))
                    )
                )
                expired_trips = result.scalars().all()
                for trip in expired_trips:
                    trip.status = "expired"
                    logging.info("Yo'nalish muddati o'tdi: trip_id=%s", trip.id)
                    if trip.channel_message_id and config.channel_id:
                        try:
                            await bot.edit_message_text(
                                "⛔ Bu yo'nalish muddati o'tdi.",
                                chat_id=config.channel_id,
                                message_id=trip.channel_message_id,
                            )
                        except Exception:
                            pass

                # Eskirgan buyurtmalar
                order_result = await session.execute(
                    select(Order)
                    .where(Order.status == "searching_driver")
                    .where(
                        (Order.date < now_date) |
                        ((Order.date == now_date) & (Order.time <= now_time))
                    )
                )
                expired_orders = order_result.scalars().all()
                for order in expired_orders:
                    order.status = "expired"
                    logging.info("Buyurtma muddati o'tdi: order_id=%s", order.id)
                    if order.channel_message_id and config.channel_id:
                        try:
                            await bot.delete_message(
                                chat_id=config.channel_id,
                                message_id=order.channel_message_id,
                            )
                        except Exception:
                            try:
                                await bot.edit_message_text(
                                    "⛔ Bu buyurtma muddati o'tdi.",
                                    chat_id=config.channel_id,
                                    message_id=order.channel_message_id,
                                )
                            except Exception:
                                pass
                    try:
                        await close_order_messages(bot, order.id, None)
                    except Exception:
                        pass

                completion_deadline = datetime.utcnow() - timedelta(hours=1)
                completion_result = await session.execute(
                    select(Order, User)
                    .join(User, Order.passenger_id == User.id)
                    .where(Order.status == "accepted")
                    .where(Order.completion_requested_at.is_not(None))
                    .where(Order.completion_requested_at <= completion_deadline)
                )
                auto_completed = completion_result.all()
                for completed_order, _passenger in auto_completed:
                    completed_order.status = "completed"
                    completed_order.completed_at = datetime.utcnow()

                await session.commit()
                if expired_trips:
                    logging.info("%d ta yo'nalish o'chirildi.", len(expired_trips))
                if expired_orders:
                    logging.info("%d ta buyurtma o'chirildi.", len(expired_orders))
                for completed_order, passenger in auto_completed:
                    passenger_lang = passenger.language or "uz"
                    try:
                        await bot.send_message(
                            passenger.telegram_id,
                            "✅ Safar avtomatik yakunlandi. Haydovchini baholang:"
                            if passenger_lang == "uz" else
                            "✅ Поездка завершена автоматически. Оцените водителя:",
                            reply_markup=rating_keyboard(completed_order.id),
                        )
                    except Exception as exc:
                        logging.warning("Avtomatik yakunlash xabari yuborilmadi: %s", exc)

        except Exception as exc:
            logging.error("auto_expire_trips xatosi: %s", exc)

        await asyncio.sleep(5 * 60)  # har 5 daqiqada tekshiradi


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    database.setup_database(config.database_url)
    await database.init_db()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(protect_content=True),
    )
    dp = Dispatcher()
    dp.message.middleware(SubscriptionMiddleware())
    dp.callback_query.middleware(SubscriptionMiddleware())
    dp.include_router(router)

    async def on_startup(bot: Bot) -> None:
        asyncio.create_task(auto_expire_trips(bot))

    dp.startup.register(on_startup)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
