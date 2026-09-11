"""Explicit runtime values used by order sizing and margin calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """Execution values that must be supplied to domain components."""

    leverage: int = 1
    position_size: float = 0.05
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        if isinstance(self.leverage, bool) or self.leverage < 1:
            raise ValueError("leverage must be at least one")
        if not math.isfinite(self.position_size) or not 0.0 < self.position_size <= 1.0:
            raise ValueError("position_size must be greater than zero and at most one")
        if not isinstance(self.timezone, str) or not self.timezone:
            raise ValueError("timezone must be a non-empty IANA timezone name")
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError(f"invalid timezone: {self.timezone!r}") from error
