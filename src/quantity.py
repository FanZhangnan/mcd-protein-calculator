"""Purchase-quantity contract.

A purchase quantity (``待买数量``) is how many whole servings go into a trial
quote. McDonald's POS sells whole items only, so it is a finite positive
**integer** (1, 2, 3 ...). It is never rounded, truncated or defaulted.

Anything else is rejected loudly with :class:`InvalidQuantity`; silently
coercing ``0.5`` to ``1`` would make the nutrition and the price describe
different meals (and it used to break the plan signature).
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

PURCHASE_HINT = "待买数量必须是正整数（1、2、3…），不能是小数、0 或负数"


class InvalidQuantity(ValueError):
    """Raised when a quantity violates the contract above."""

    def __init__(self, message: str, *, field: str = "quantity", value: object = None):
        super().__init__(message)
        self.field = field
        self.value = value


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def purchase_quantity(value: object, label: str = "待买数量") -> int:
    """Return a positive integer, or raise :class:`InvalidQuantity`.

    ``bool`` is rejected on purpose: it subclasses ``int`` and ``True`` would
    otherwise slip through as 1.
    """
    if isinstance(value, bool):
        raise InvalidQuantity(f"{label}：{PURCHASE_HINT}", value=value)
    if _blank(value):
        raise InvalidQuantity(f"{label}不能为空：{PURCHASE_HINT}", value=value)
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise InvalidQuantity(f"{label}不是数字：{PURCHASE_HINT}", value=value)
    if not number.is_finite():
        raise InvalidQuantity(f"{label}必须是有限数字：{PURCHASE_HINT}", value=value)
    if number != number.to_integral_value():
        raise InvalidQuantity(
            f"{label}必须是整数，收到 {value}",
            value=value,
        )
    if number < 1:
        raise InvalidQuantity(f"{label}必须大于等于 1，收到 {value}", value=value)
    return int(number)


def signature_quantity(value: object) -> int | float:
    """Normalise a quantity for hashing so ``2`` and ``2.0`` sign the same."""
    number = float(value)  # type: ignore[arg-type]
    return int(number) if number.is_integer() else number
