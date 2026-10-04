"""Alerting helper."""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from .base import Notification
from .registry import Registry

logger = logging.getLogger("notifications")


class Alerter:
    def __init__(self, registry: Registry, config: Optional[Dict] = None):
        self.registry = registry
        self.config = config or {}

    def emit(
        self,
        alert_type: str,
        message: str,
        data: Optional[Dict] = None,
        channels: Optional[List[str]] = None,
        key: Optional[str] = None,
        priority: Optional[str] = None,
        blocking: bool = False,
    ) -> None:
        n = Notification(
            alert_type=alert_type,
            message=message,
            data=data or {},
            channels=channels,
            key=key,
            priority=priority,
        )
        if blocking:
            try:
                self.registry.send(n)
            except Exception:
                logger.exception("emit failed")
            return
        t = threading.Thread(target=self._send, args=(n,), daemon=True)
        t.start()

    def _send(self, n: Notification) -> None:
        try:
            self.registry.send(n)
        except Exception:
            logger.exception("send failed")
