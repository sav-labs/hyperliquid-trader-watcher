from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class UserStates(StatesGroup):
    adding_traders = State()


class AdminStates(StatesGroup):
    setting_channel = State()


