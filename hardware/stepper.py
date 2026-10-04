from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class StepperMotor:
    """28BYJ-48 stepper motor controller using gpiozero."""

    def __init__(self, pins: list[int], simulate: bool = False) -> None:
        self.pins = pins
        self.simulate = simulate
        self._devices: list[Any] = []
        self._sequence = [
            [1, 1, 0, 0],
            [0, 1, 1, 0],
            [0, 0, 1, 1],
            [1, 0, 0, 1],
        ]

        if not simulate:
            try:
                from gpiozero import OutputDevice

                for pin in pins:
                    self._devices.append(OutputDevice(pin))
                logger.info("Stepper motor initialized on pins %s", pins)
            except Exception as exc:
                logger.warning("Failed to init stepper: %s", exc)
                self.simulate = True
                self._devices = []
        else:
            logger.info("Stepper motor in SIMULATED mode")

    def step(self, steps: int, delay: float = 0.002, clockwise: bool = True) -> None:
        sequence = self._sequence if clockwise else list(reversed(self._sequence))
        if self.simulate or not self._devices:
            time.sleep(delay * steps * len(sequence) * 0.05)
            return
        for _ in range(steps):
            for step in sequence:
                for pin_index in range(4):
                    if pin_index < len(self._devices):
                        if step[pin_index] == 1:
                            self._devices[pin_index].on()
                        else:
                            self._devices[pin_index].off()
                time.sleep(delay)

    def off(self) -> None:
        if self._devices:
            for dev in self._devices:
                try:
                    dev.off()
                except Exception:
                    pass
