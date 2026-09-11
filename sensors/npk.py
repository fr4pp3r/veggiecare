"""NPK sensor — JXCT JXBS-3001 7-in-1 soil sensor over RS485 (Modbus RTU).

Wraps the user's tested minimalmodbus code into the VeggieCare sensor
interface.  Hardware libraries are imported lazily so the module can be
loaded on a Windows dev machine (simulate_hardware: true) without errors.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sensors.base import BaseSensor, Reading, SensorError

logger = logging.getLogger(__name__)


class NpkSensor(BaseSensor):
    """Reads N, P, K via Modbus RTU on an RS485 bus."""

    name = "npk"

    def __init__(self, cfg: dict[str, Any], simulate: bool = False):
        super().__init__(simulate=simulate)
        self._cfg = cfg
        self._instrument = None  # lazily created on first real read

    # ------------------------------------------------------------------
    # real hardware path
    # ------------------------------------------------------------------

    def _ensure_instrument(self):
        import minimalmodbus
        import serial  # noqa: F401 — minimalmodbus needs pyserial

        cfg = self._cfg
        instrument = minimalmodbus.Instrument(cfg["port"], cfg["slave_id"])
        instrument.serial.baudrate = int(cfg["baudrate"])
        instrument.serial.bytesize = 8
        instrument.serial.parity = serial.PARITY_NONE
        instrument.serial.stopbits = 1
        instrument.serial.timeout = float(cfg["timeout"])
        instrument.mode = minimalmodbus.MODE_RTU
        instrument.debug = False
        return instrument

    def _read_hardware(self) -> Reading:
        try:
            instrument = self._ensure_instrument()
            values = instrument.read_registers(
                registeraddress=int(self._cfg["register_start"]),
                number_of_registers=int(self._cfg["register_count"]),
                functioncode=3,
            )
            scale = float(self._cfg.get("scale", 1.0))
            result = {
                "nitrogen": float(values[0]) * scale,
                "phosphorus": float(values[1]) * scale,
                "potassium": float(values[2]) * scale,
            }
            return Reading(timestamp=datetime.now(), values=result, raw={"registers": values})
        except Exception as exc:
            msg = f"NPK read failed: {exc}"
            logger.error(msg)
            raise SensorError(msg) from exc

    # ------------------------------------------------------------------
    # sensor interface
    # ------------------------------------------------------------------

    def read(self) -> Reading:
        if self.simulate:
            sim = self._cfg.get("simulated", {})
            return Reading(
                timestamp=datetime.now(),
                values={
                    "nitrogen": float(sim.get("nitrogen", 0)),
                    "phosphorus": float(sim.get("phosphorus", 0)),
                    "potassium": float(sim.get("potassium", 0)),
                },
            )
        return self._read_hardware()

    def close(self) -> None:
        if self._instrument is not None:
            try:
                self._instrument.serial.close()
            except Exception:
                pass
            self._instrument = None