"""Thread-safe reservations before each paid attempt, including retries."""

from __future__ import annotations

import math
import threading
import uuid


class BudgetExceeded(RuntimeError):  # noqa: N818 - public design contract
    def __init__(self, code="budget_exceeded"):
        self.code = code
        super().__init__(code)


class BudgetGuard:
    def __init__(self, max_cost_usd, prices):
        if (
            type(max_cost_usd) not in {int, float}
            or not math.isfinite(max_cost_usd)
            or max_cost_usd <= 0
        ):
            raise ValueError("Maximum cost must be finite and positive")
        self.max_cost_usd = max_cost_usd
        self.prices = prices
        self._spent = 0.0
        self._pending = {}
        self._lock = threading.Lock()

    @property
    def spent(self):
        with self._lock:
            return self._spent

    def reserve(self, prompt, spec):
        price = self.prices.get("models", {}).get(spec.model)
        if not isinstance(price, dict) or any(
            type(price.get(name)) not in {int, float}
            or not math.isfinite(price[name])
            or price[name] < 0
            for name in ("input_per_million", "output_per_million")
        ):
            raise BudgetExceeded("price_missing")
        estimate = (
            math.ceil(len(prompt) / 3.5) * price["input_per_million"]
            + spec.max_output_tokens * price["output_per_million"]
        ) / 1_000_000
        with self._lock:
            if (
                self._spent + sum(row["estimate"] for row in self._pending.values()) + estimate
                > self.max_cost_usd
            ):
                raise BudgetExceeded()
            reservation = {"id": uuid.uuid4().hex, "estimate": estimate, "price": price}
            self._pending[reservation["id"]] = reservation
        return reservation

    def complete(self, reservation, usage=None, *, posted=True):
        if reservation is None:
            return
        with self._lock:
            row = self._pending.pop(reservation["id"], None)
            if row is None or not posted:
                return
            known = (
                isinstance(usage, dict)
                and usage.get("usage_known") is not False
                and all(
                    type(usage.get(key)) is int and usage[key] >= 0
                    for key in ("input_tokens", "output_tokens")
                )
            )
            cost = row["estimate"]
            if known:
                try:
                    cost = (
                        usage["input_tokens"] * row["price"]["input_per_million"]
                        + usage["output_tokens"] * row["price"]["output_per_million"]
                    ) / 1_000_000
                    if not math.isfinite(cost):
                        cost = self.max_cost_usd
                except OverflowError:
                    # An unrepresentable claimed bill cannot reopen the budget.
                    cost = self.max_cost_usd
            self._spent += cost
