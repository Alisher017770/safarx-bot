import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_IDS", "7074563321")

import main
from keyboards import assistant_keyboard, main_menu


class FakeState:
    def __init__(self, current, data=None):
        self.current = current
        self.data = dict(data or {"lang": "uz"})

    async def get_state(self):
        return self.current.state if hasattr(self.current, "state") else self.current

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, state):
        self.current = state

    async def update_data(self, **values):
        self.data.update(values)

    async def clear(self):
        self.current = None
        self.data = {}


def fake_message():
    return SimpleNamespace(
        text=main.BACK_BUTTON,
        from_user=SimpleNamespace(id=1),
        answer=AsyncMock(),
    )


def keyboard_texts(markup):
    return [button.text for row in markup.keyboard for button in row]


class BackNavigationTests(unittest.IsolatedAsyncioTestCase):
    def test_free_assistant_is_visible_and_answers_every_topic(self):
        for language, assistant_label in (("uz", "🤖 Yordamchi"), ("ru", "🤖 Помощник")):
            self.assertIn(assistant_label, keyboard_texts(main_menu(False, language)))
            self.assertIn(assistant_label, keyboard_texts(main_menu(True, language)))
            topics = keyboard_texts(assistant_keyboard(language))[:-1]
            self.assertEqual(7, len(topics))
            for topic in topics:
                answer, _show_admin = main.assistant_answer(topic, language)
                self.assertNotIn("tushunmadim", answer.casefold(), topic)
                self.assertNotIn("не понял", answer.casefold(), topic)

    def test_free_assistant_does_not_route_unknown_question_to_admin(self):
        answer, show_admin = main.assistant_answer("mutlaqo noma'lum savol", "uz")
        self.assertFalse(show_admin)
        self.assertIn("aniq javob", answer.casefold())

    def test_free_assistant_understands_common_written_questions(self):
        questions = {
            "botni qande ishlataman": "1.",
            "pochta jo'natmoqchiman": "📦",
            "bu bepulmi yoki pulmi": "💰",
            "buyurtmam holati qanaqa": "👤",
            "tilni ruscha qilaman": "🌐",
            "xavfsizmi ishonchlimi": "🛡",
        }
        for question, expected in questions.items():
            answer, show_admin = main.assistant_answer(question, "uz")
            self.assertFalse(show_admin, question)
            self.assertIn(expected, answer, question)

        _answer, show_admin = main.assistant_answer("admin bilan gaplashaman", "uz")
        self.assertTrue(show_admin)

    async def test_passenger_back_returns_one_step(self):
        state = FakeState(main.PassengerOrder.max_price)
        message = fake_message()
        await main.back_one_step(message, state)
        self.assertEqual(main.PassengerOrder.roof_luggage, state.current)
        message.answer.assert_awaited_once()

    async def test_passenger_back_restores_selected_district(self):
        state = FakeState(
            main.PassengerOrder.to_city,
            {"lang": "uz", "from_city_base": "Andijon", "from_city": "Andijon, Asaka"},
        )
        message = fake_message()
        await main.back_one_step(message, state)
        self.assertEqual(main.PassengerOrder.from_district, state.current)

    async def test_driver_registration_back_returns_to_previous_photo(self):
        state = FakeState(main.DriverRegister.tech_passport_photo)
        message = fake_message()
        await main.back_one_step(message, state)
        self.assertEqual(main.DriverRegister.car_front_photo, state.current)

    async def test_driver_trip_back_returns_one_step(self):
        state = FakeState(main.DriverTripCreate.price_per_person)
        message = fake_message()
        await main.back_one_step(message, state)
        self.assertEqual(main.DriverTripCreate.available_seats, state.current)

    async def test_driver_destination_district_continues_without_error(self):
        state = FakeState(
            main.DriverTripCreate.to_district,
            {"lang": "uz", "from_city": "Toshkent", "to_city_base": "Samarqand"},
        )
        message = fake_message()
        message.text = "Urgut"
        await main.trip_to_district(message, state)
        self.assertEqual(main.DriverTripCreate.date, state.current)

    async def test_admin_broadcast_back_returns_to_target(self):
        state = FakeState(main.AdminBroadcast.text)
        message = fake_message()
        await main.back_one_step(message, state)
        self.assertEqual(main.AdminBroadcast.target, state.current)

    async def test_main_menu_still_cancels_the_process(self):
        state = FakeState(main.PassengerOrder.comment)
        message = fake_message()
        with patch.object(main, "get_user_language", new=AsyncMock(return_value="uz")):
            await main.back_to_main_menu(message, state)
        self.assertIsNone(state.current)


if __name__ == "__main__":
    unittest.main()
