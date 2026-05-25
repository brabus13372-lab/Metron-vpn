"""
Константы и перечисления уровня домена.
Импортируй отсюда — не дублируй в других модулях.
"""

from __future__ import annotations

import enum


class UserStatus(str, enum.Enum):
    """Статусы пользователя. Должны совпадать с CHECK-ограничением в schema.py."""

    NEW = "NEW"
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    EXPIRED = "EXPIRED"
