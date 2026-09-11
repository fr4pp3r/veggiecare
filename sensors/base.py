"""Sensor base classes for VeggieCare.

A sensor produces a :class:`Reading` and raises :class:`SensorError` on
failure. Concrete sensors never block the dashboard: they are only ever
called from the automation controller thread.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class SensorError(Exception):
    """Raised when a sensor cannot produce a reading."""


@dataclass
class Reading:
    """One successful (or failed) sensor measurement.

    ``values`` maps a quantity name to its scalar value, e.g.
    ``{"nitrogen": 25.0, "phosphorus": 15.0, "potassium": 30.0}`` or
    ``{"moisture": 45.0}``.
    """

    timestamp: datetime
    values: dict[str, float]
    raw: dict[str, Any] | None = None
    error: str | None = None


class BaseSensor(ABC):
    """Common interface all sensors implement."""

    name: str = "base"

    def __init__(self, simulate: bool = False):
        self.simulate = simulate

    @abstractmethod
    def read(self) -> Reading:
        """Read the sensor once and return a Reading. Raises SensorError."""

    def close(self) -> None:
        """Release any resources. Base implementation does nothing."""