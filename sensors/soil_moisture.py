"""Soil moisture sensor — capacitive probe via MCP3008 ADC on SPI.

Wraps the user's tested spidev code into the VeggieCare sensor interface.
Hardware libraries are imported lazily so the module works in simulated
mode on non-Pi machines.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sensors.base import BaseSensor, Reading, SensorError

logger = logging.getLogger(__name__)


class SoilMoistureSensor(BaseSensor):
    """Reads soil moisture percentage from an MCP3008 ADC channel."""

    name = "soil_moisture"

    def __init__(self, cfg: dict[str, Any], simulate: bool = False):
        super().__init__(simulate=simulate)
        self._cfg = cfg
        self._spi = None  # opened per read in hardware mode

    # ------------------------------------------------------------------
    # real hardware path — faithful copy of the tested soilmoisture.py
    # ------------------------------------------------------------------

    def _open_spi(self):
        import spidev

        spi = spidev.SpiDev()
        spi.open(int(self._cfg["spi_bus"]), int(self._cfg["spi_device"]))
        spi.max_speed_hz = 1350000
        spi.mode = 0
        return spi

    def _read_adc(self, channel: int) -> int:
        if channel < 0 or channel > 7:
            raise ValueError("Channel must be between 0 and 7")
        result = self._spi.xfer2([1, (8 + channel) << 4, 0])
        return ((result[1] & 3) << 8) | result[2]

    def _read_average(self, channel: int, samples: int) -> float:
        import time

        readings = []
        for _ in range(samples):
            readings.append(self._read_adc(channel))
            time.sleep(0.01)
        return sum(readings) / len(readings)

    def _read_hardware(self) -> Reading:
        cfg = self._cfg
        try:
            self._spi = self._open_spi()
            channel = int(cfg["channel"])
            samples = int(cfg.get("samples", 20))
            adc = self._read_average(channel, samples)
            dry_adc = int(cfg["dry_adc"])
            wet_adc = int(cfg["wet_adc"])
            moisture = ((dry_adc - adc) / (dry_adc - wet_adc)) * 100
            moisture = max(0.0, min(100.0, moisture))
            voltage = float(adc) * float(cfg["vref"]) / int(cfg["adc_max"])
            return Reading(
                timestamp=datetime.now(),
                values={"moisture": round(moisture, 1)},
                raw={"adc": round(adc, 1), "voltage": round(voltage, 3)},
            )
        except Exception as exc:
            msg = f"Moisture read failed: {exc}"
            logger.error(msg)
            raise SensorError(msg) from exc
        finally:
            if self._spi is not None:
                try:
                    self._spi.close()
                except Exception:
                    pass
                self._spi = None

    # ------------------------------------------------------------------
    # sensor interface
    # ------------------------------------------------------------------

    def read(self) -> Reading:
        if self.simulate:
            sim = self._cfg.get("simulated", {})
            return Reading(
                timestamp=datetime.now(),
                values={"moisture": float(sim.get("moisture", 0))},
            )
        return self._read_hardware()

    def close(self) -> None:
        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:
                pass
            self._spi = None