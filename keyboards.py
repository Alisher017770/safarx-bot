from datetime import datetime, timedelta, timezone

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


CITIES = [
    "Toshkent",
    "Andijon",
    "Farg'ona",
    "Namangan",
    "Samarqand",
    "Buxoro",
    "Xorazm",
    "Qashqadaryo",
    "Surxondaryo",
    "Sirdaryo",
    "Jizzax",
    "Navoiy",
    "Qoraqalpog'iston",
]
DISTRICTS_BY_CITY = {
    "Andijon": [
        "Andijon shahar",
        "Andijon tumani",
        "Asaka",
        "Baliqchi",
        "Bo'ston",
        "Buloqboshi",
        "Izboskan",
        "Jalaquduq",
        "Marhamat",
        "Oltinko'l",
        "Paxtaobod",
        "Qo'rg'ontepa",
        "Shahrixon",
        "Ulug'nor",
        "Xo'jaobod",
        "Xonobod",
    ],
}
PRICE_OPTIONS = [200000, 220000, 250000]
TIME_OPTIONS = [
    "⚡ Srochniy",
    "🕐 Klient vaqti",
    "06:00",
    "07:00",
    "08:00",
    "09:00",
    "10:00",
    "11:00",
    "12:00",
    "13:00",
    "14:00",
    "15:00",
    "16:00",
    "17:00",
    "18:00",
    "19:00",
    "20:00",
    "21:00",
    "22:00",
    "23:00",
]
BACK_BUTTON = "⬅️ Orqaga"
BACK_BUTTON_RU = "⬅️ Назад"
LANGUAGE_BUTTONS = {"uz": "🌐 Til", "ru": "🌐 Язык"}
BUTTONS = {
    "uz": {
        "passenger": "🚕 Yo'lovchiman",
        "parcel": "📦 Pochta/Buyum",
        "driver": "🚘 Haydovchiman",
        "my_orders": "📦 Buyurtmalarim",
        "profile": "👤 Profil",
        "help": "☎️ Yordam",
        "analysis": "📊 Analiz",
        "passengers": "👥 Yo'lovchilar",
        "drivers": "🚘 Haydovchilar",
        "open_orders": "📦 Ochiq buyurtmalar",
        "broadcast": "📢 Xabar yuborish",
        "search": "🔍 Qidirish",
        "send_phone": "📞 Telefon raqamni yuborish",
        "send_location": "📍 Lokatsiyani yuborish",
        "today": "Bugun",
        "tomorrow": "Ertaga",
        "after_tomorrow": "Indinga",
        "up_to": "so'mgacha",
        "any_price": "Farqi yo'q",
        "yes": "Ha",
        "no": "Yo'q",
        "skip_comment": "Izoh yo'q",
        "join_channel": "📢 Kanalga a'zo bo'lish",
        "check": "✅ Tekshirish",
    },
    "ru": {
        "passenger": "🚕 Я пассажир",
        "parcel": "📦 Посылка/Товар",
        "driver": "🚘 Я водитель",
        "my_orders": "📦 Мои заказы",
        "profile": "👤 Профиль",
        "help": "☎️ Помощь",
        "analysis": "📊 Аналитика",
        "passengers": "👥 Пассажиры",
        "drivers": "🚘 Водители",
        "open_orders": "📦 Открытые заказы",
        "broadcast": "📢 Рассылка",
        "search": "🔍 Поиск",
        "send_phone": "📞 Отправить номер",
        "send_location": "📍 Отправить локацию",
        "today": "Сегодня",
        "tomorrow": "Завтра",
        "after_tomorrow": "Послезавтра",
        "up_to": "сум",
        "any_price": "Не важно",
        "yes": "Да",
        "no": "Нет",
        "skip_comment": "Без комментария",
        "join_channel": "📢 Подписаться на канал",
        "check": "✅ Проверить",
    },
}


def tr_button(key: str, lang: str = "uz") -> str:
    return BUTTONS.get(lang, BUTTONS["uz"]).get(key, BUTTONS["uz"][key])


def back_button(lang: str = "uz") -> str:
    return BACK_BUTTON_RU if lang == "ru" else BACK_BUTTON


def with_back(rows: list[list[KeyboardButton]], lang: str = "uz") -> list[list[KeyboardButton]]:
    return rows + [[KeyboardButton(text=back_button(lang))]]


def main_menu(is_admin: bool = False, lang: str = "uz") -> ReplyKeyboardMarkup:
    if is_admin:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=tr_button("analysis", lang)), KeyboardButton(text=tr_button("open_orders", lang))],
                [KeyboardButton(text=tr_button("passengers", lang)), KeyboardButton(text=tr_button("drivers", lang))],
                [KeyboardButton(text=tr_button("broadcast", lang)), KeyboardButton(text=tr_button("search", lang))],
                [KeyboardButton(text=LANGUAGE_BUTTONS.get(lang, LANGUAGE_BUTTONS["uz"])), KeyboardButton(text=tr_button("help", lang))],
            ],
            resize_keyboard=True,
        )

    rows = [
        [KeyboardButton(text=tr_button("passenger", lang)), KeyboardButton(text=tr_button("driver", lang))],
        [KeyboardButton(text=tr_button("parcel", lang))],
        [KeyboardButton(text=tr_button("my_orders", lang)), KeyboardButton(text=tr_button("profile", lang))],
        [KeyboardButton(text=LANGUAGE_BUTTONS.get(lang, LANGUAGE_BUTTONS["uz"])), KeyboardButton(text=tr_button("help", lang))],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def phone_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=with_back([[KeyboardButton(text=tr_button("send_phone", lang), request_contact=True)]], lang),
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def location_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=with_back([[KeyboardButton(text=tr_button("send_location", lang), request_location=True)]], lang),
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def city_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=city)] for city in CITIES]
    return ReplyKeyboardMarkup(keyboard=with_back(rows, lang), resize_keyboard=True, one_time_keyboard=True)


def district_keyboard(city: str, lang: str = "uz") -> ReplyKeyboardMarkup:
    districts = DISTRICTS_BY_CITY.get(city, [])
    rows = [[KeyboardButton(text=district)] for district in districts]
    return ReplyKeyboardMarkup(keyboard=with_back(rows, lang), resize_keyboard=True, one_time_keyboard=True)


def date_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5))).date()
    labels = [tr_button("today", lang), tr_button("tomorrow", lang), tr_button("after_tomorrow", lang)]
    rows = []
    for day_index, label in enumerate(labels):
        date_value = today + timedelta(days=day_index)
        rows.append([KeyboardButton(text=f"{label} - {date_value.isoformat()}")])
    rows.append([KeyboardButton(text=(today + timedelta(days=3)).isoformat())])
    return ReplyKeyboardMarkup(keyboard=with_back(rows, lang), resize_keyboard=True, one_time_keyboard=True)


def price_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    rows = []
    for index in range(0, len(PRICE_OPTIONS), 2):
        row = [
            KeyboardButton(text=f"{price:,} so'm".replace(",", " "))
            for price in PRICE_OPTIONS[index : index + 2]
        ]
        rows.append(row)
    return ReplyKeyboardMarkup(keyboard=with_back(rows, lang), resize_keyboard=True, one_time_keyboard=True)


def max_price_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    rows = []
    for index in range(0, len(PRICE_OPTIONS), 2):
        row = [
            KeyboardButton(text=f"{price:,} {tr_button('up_to', lang)}".replace(",", " "))
            for price in PRICE_OPTIONS[index : index + 2]
        ]
        rows.append(row)
    rows.append([KeyboardButton(text=tr_button("any_price", lang))])
    return ReplyKeyboardMarkup(keyboard=with_back(rows, lang), resize_keyboard=True, one_time_keyboard=True)


def passenger_time_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    """Yo'lovchi uchun vaqt tanlash - Klient vaqti yo'q."""
    urgent = "⚡ Срочно" if lang == "ru" else "⚡ Srochniy"
    times = [urgent, "06:00", "07:00", "08:00", "09:00", "10:00",
             "11:00", "12:00", "13:00", "14:00", "15:00", "16:00",
             "17:00", "18:00", "19:00", "20:00", "21:00", "22:00", "23:00"]
    rows = []
    for i in range(0, len(times), 3):
        rows.append([KeyboardButton(text=t) for t in times[i:i+3]])
    return ReplyKeyboardMarkup(keyboard=with_back(rows, lang), resize_keyboard=True, one_time_keyboard=True)


def time_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    times = TIME_OPTIONS.copy()
    if lang == "ru":
        times[0] = "⚡ Срочно"
        times[1] = "🕐 Время клиента"
    rows = []
    for index in range(0, len(times), 3):
        rows.append([KeyboardButton(text=time) for time in times[index : index + 3]])
    return ReplyKeyboardMarkup(keyboard=with_back(rows, lang), resize_keyboard=True, one_time_keyboard=True)


def yes_no_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=with_back([[KeyboardButton(text=tr_button("yes", lang)), KeyboardButton(text=tr_button("no", lang))]], lang),
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def skip_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=with_back([[KeyboardButton(text=tr_button("skip_comment", lang))]], lang),
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def language_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🇺🇿 O'zbekcha", callback_data="lang:uz")
    builder.button(text="🇷🇺 Русский", callback_data="lang:ru")
    builder.adjust(2)
    return builder.as_markup()


def admin_driver_keyboard(driver_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data=f"driver:approve:{driver_id}")
    builder.button(text="❌ Rad etish", callback_data=f"driver:reject:{driver_id}")
    builder.adjust(2)
    return builder.as_markup()


def admin_driver_manage_keyboard(driver_id: int, status: str):
    builder = InlineKeyboardBuilder()
    if status == "active":
        builder.button(text="🚫 Bloklash", callback_data=f"driver:block:{driver_id}")
    elif status == "blocked":
        builder.button(text="✅ Blokdan chiqarish", callback_data=f"driver:unblock:{driver_id}")
    builder.adjust(1)
    return builder.as_markup()


def broadcast_target_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    if lang == "ru":
        rows = [
            [KeyboardButton(text="Всем")],
            [KeyboardButton(text="Пассажирам")],
            [KeyboardButton(text="Водителям")],
        ]
    else:
        rows = [
            [KeyboardButton(text="Hammaga")],
            [KeyboardButton(text="Yo'lovchilarga")],
            [KeyboardButton(text="Haydovchilarga")],
        ]
    return ReplyKeyboardMarkup(keyboard=with_back(rows, lang), resize_keyboard=True, one_time_keyboard=True)


def order_keyboard(order_id: int, lang: str = "uz"):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Qabul qilish" if lang == "uz" else "✅ Принять", callback_data=f"order:accept:{order_id}")
    builder.button(text="↪️ O'tkazib yuborish" if lang == "uz" else "↪️ Пропустить", callback_data=f"order:skip:{order_id}")
    builder.adjust(2)
    return builder.as_markup()


def trip_select_keyboard(trip_id: int, lang: str = "uz"):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tanlash" if lang == "uz" else "✅ Выбрать", callback_data=f"trip:select:{trip_id}")
    return builder.as_markup()


def channel_trip_keyboard(trip_id: int, bot_username: str):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🚕 Botda tanlash",
        url=f"https://t.me/{bot_username}?start=trip_{trip_id}",
    )
    return builder.as_markup()


def channel_order_keyboard(order_id: int, bot_username: str):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🚕 Qabul qilish",
        url=f"https://t.me/{bot_username}?start=order_{order_id}",
    )
    return builder.as_markup()


def driver_services_keyboard() -> ReplyKeyboardMarkup:
    """Haydovchi o'ziga xos xizmatlarini belgilash tugmalari."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚗 Oddiy"), KeyboardButton(text="🚪 Oldi-bosh xizmati")],
        ],
        resize_keyboard=True,
    )


def order_type_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    """Yo'lovchi buyurtma turi: yo'lovchi yoki pochta."""
    if lang == "uz":
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🧍 Yo'lovchi"), KeyboardButton(text="📦 Pochta/buyum yuborish")],
            ],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧍 Пассажир"), KeyboardButton(text="📦 Отправить посылку")],
        ],
        resize_keyboard=True,
    )
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🚕 Qabul qilish",
        url=f"https://t.me/{bot_username}?start=order_{order_id}",
    )
    return builder.as_markup()


def subscribe_keyboard(channel_id: str):
    channel_url = f"https://t.me/{channel_id.lstrip('@')}" if channel_id.startswith("@") else "https://t.me/SafarX_0"
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Kanalga a'zo bo'lish", url=channel_url)
    builder.button(text="✅ Tekshirish", callback_data="check_sub")
    builder.adjust(1)
    return builder.as_markup()


def admin_contacts_keyboard(admin_ids: list[int], lang: str = "uz"):
    builder = InlineKeyboardBuilder()
    label = "Admin" if lang == "uz" else "Админ"
    for index, admin_id in enumerate(admin_ids, start=1):
        builder.button(text=f"{label} {index}", url=f"tg://user?id={admin_id}")
    builder.adjust(1)
    return builder.as_markup()


def accepted_order_keyboard(order_id: int, passenger_telegram_id: int, lang: str = "uz"):
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Klient lichkasi" if lang == "uz" else "👤 Профиль клиента", url=f"tg://user?id={passenger_telegram_id}")
    builder.button(text="✅ Safarni yakunlash" if lang == "uz" else "✅ Завершить поездку", callback_data=f"order:finish:{order_id}")
    builder.button(text="❌ Buyurtmani bekor qilish" if lang == "uz" else "❌ Отменить заказ", callback_data=f"order:cancel:{order_id}")
    builder.adjust(1)
    return builder.as_markup()


def completion_confirmation_keyboard(order_id: int, lang: str = "uz"):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Ha, safar yakunlandi" if lang == "uz" else "✅ Да, поездка завершена",
        callback_data=f"complete:confirm:{order_id}",
    )
    builder.button(
        text="⚠️ Muammo / shikoyat" if lang == "uz" else "⚠️ Проблема / жалоба",
        callback_data=f"complete:complaint:{order_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def rating_keyboard(order_id: int):
    builder = InlineKeyboardBuilder()
    for stars in range(1, 6):
        builder.button(text=f"{stars} ⭐", callback_data=f"rating:{stars}:{order_id}")
    builder.button(text="⚠️ Shikoyat / Жалоба", callback_data=f"complete:complaint:{order_id}")
    builder.adjust(5, 1)
    return builder.as_markup()


def complaint_reason_keyboard(order_id: int, lang: str = "uz"):
    reasons = (
        [("Haydovchi kelmadi", "no_show"), ("Muomala yomon", "behavior"), ("Xavfli haydash", "unsafe"), ("Narx muammosi", "price"), ("Boshqa", "other")]
        if lang == "uz" else
        [("Водитель не приехал", "no_show"), ("Грубое отношение", "behavior"), ("Опасное вождение", "unsafe"), ("Проблема с ценой", "price"), ("Другое", "other")]
    )
    builder = InlineKeyboardBuilder()
    for label, code in reasons:
        builder.button(text=label, callback_data=f"complaint_reason:{code}:{order_id}")
    builder.adjust(1)
    return builder.as_markup()


def passenger_order_keyboard(order_id: int, lang: str = "uz"):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Buyurtmani bekor qilish" if lang == "uz" else "❌ Отменить заказ",
        callback_data=f"passenger_order:cancel:{order_id}",
    )
    return builder.as_markup()


def admin_order_keyboard(order_id: int, passenger_telegram_id: int, lang: str = "uz"):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="👤 Yo'lovchi" if lang == "uz" else "👤 Пассажир",
        url=f"tg://user?id={passenger_telegram_id}",
    )
    builder.button(
        text="❌ Buyurtmani yopish" if lang == "uz" else "❌ Закрыть заказ",
        callback_data=f"admin_order:cancel:{order_id}",
    )
    builder.adjust(1)
    return builder.as_markup()
