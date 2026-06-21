"""Backend for the SafarX Telegram Mini App.

Runs as an aiohttp web application inside the same process as the bot
(see main.py). It serves the static Mini App frontend (webapp/) and a
small read-only JSON API the frontend uses to list driver trips.

Booking itself is NOT done through this API. The "Joy band qilish"
button in the Mini App deep-links back into the bot chat
(t.me/<bot>?start=trip_<id>), which already has the full booking flow
implemented in main.py. This keeps the web surface simple and avoids
re-implementing the phone/location/FSM logic in JavaScript.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web
from sqlalchemy import select

import database
from models import Driver, DriverTrip, Order, User

WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"
CITIES = ["Toshkent", "Andijon", "Farg'ona", "Namangan", "Qo'qon", "Marg'ilon"]


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """Validates Telegram WebApp initData (see Telegram Bot API docs:
    "Validating data received via the Mini App"). Returns the parsed
    fields (with 'user' decoded to a dict) if the signature is valid,
    otherwise None.
    """
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    if "user" in pairs:
        try:
            pairs["user"] = json.loads(pairs["user"])
        except (json.JSONDecodeError, TypeError):
            pairs["user"] = None
    return pairs


def get_authenticated_telegram_id(request: web.Request) -> int | None:
    bot_token = request.app["bot_token"]
    init_data = request.headers.get("X-Telegram-Init-Data") or request.query.get("initData", "")
    data = validate_init_data(init_data, bot_token)
    if not data or not data.get("user"):
        return None
    return data["user"].get("id")


def trip_to_dict(trip: DriverTrip, driver: Driver, driver_user: User) -> dict:
    return {
        "id": trip.id,
        "from_city": trip.from_city,
        "to_city": trip.to_city,
        "date": trip.date,
        "time": trip.time,
        "available_seats": trip.available_seats,
        "price_per_person": trip.price_per_person,
        "car_model": driver.car_model,
        "car_color": driver.car_color,
        "roof_luggage": trip.roof_luggage,
        "is_urgent": bool(trip.is_urgent),
        "driver_name": driver_user.full_name,
        "comment": trip.comment,
        "status": trip.status,
    }


async def handle_trips(request: web.Request) -> web.Response:
    from_city = request.query.get("from") or None
    to_city = request.query.get("to") or None
    date = request.query.get("date") or None

    async with database.SessionLocal() as session:
        query = (
            select(DriverTrip, Driver, User)
            .join(Driver, DriverTrip.driver_id == Driver.id)
            .join(User, Driver.user_id == User.id)
            .where(DriverTrip.status == "active")
            .where(DriverTrip.available_seats > 0)
            .where(Driver.status == "active")
            .order_by(DriverTrip.id.desc())
            .limit(100)
        )
        if from_city:
            query = query.where(DriverTrip.from_city == from_city)
        if to_city:
            query = query.where(DriverTrip.to_city == to_city)
        if date:
            query = query.where(DriverTrip.date.like(f"%{date}"))
        result = await session.execute(query)
        rows = result.all()

    trips = [trip_to_dict(trip, driver, driver_user) for trip, driver, driver_user in rows]
    return web.json_response({"trips": trips})


async def handle_my_trips(request: web.Request) -> web.Response:
    telegram_id = get_authenticated_telegram_id(request)
    if not telegram_id:
        return web.json_response({"error": "unauthorized"}, status=401)

    async with database.SessionLocal() as session:
        result = await session.execute(
            select(DriverTrip, Driver, User)
            .join(Driver, DriverTrip.driver_id == Driver.id)
            .join(User, Driver.user_id == User.id)
            .where(User.telegram_id == telegram_id)
            .order_by(DriverTrip.id.desc())
            .limit(50)
        )
        trip_rows = result.all()

        order_result = await session.execute(
            select(Order)
            .join(User, Order.passenger_id == User.id)
            .where(User.telegram_id == telegram_id)
            .where(Order.status.in_(["searching_driver", "accepted"]))
            .order_by(Order.id.desc())
            .limit(50)
        )
        orders = order_result.scalars().all()

    trips = [trip_to_dict(trip, driver, driver_user) for trip, driver, driver_user in trip_rows]
    order_list = [
        {
            "id": order.id,
            "from_city": order.from_city,
            "to_city": order.to_city,
            "date": order.date,
            "time": order.time,
            "status": order.status,
        }
        for order in orders
    ]
    return web.json_response({"trips": trips, "orders": order_list})


async def handle_meta(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "bot_username": request.app["bot_username"],
            "cities": CITIES,
        }
    )


async def handle_index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html")


def build_web_app(bot_token: str, bot_username: str) -> web.Application:
    app = web.Application()
    app["bot_token"] = bot_token
    app["bot_username"] = bot_username

    app.router.add_get("/api/trips", handle_trips)
    app.router.add_get("/api/trips/mine", handle_my_trips)
    app.router.add_get("/api/meta", handle_meta)
    app.router.add_get("/", handle_index)
    app.router.add_static("/static/", WEBAPP_DIR, show_index=False)

    logging.info("SafarX Mini App backend tayyor: %s", WEBAPP_DIR)
    return app
