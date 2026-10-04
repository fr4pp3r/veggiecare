"""Basic providers for notifications."""
from __future__ import annotations

import logging
from typing import Optional

from .base import Notification, Notifier, SendResult

logger = logging.getLogger("notifications")


class LogNotifier(Notifier):
    name = "log"
    channel = "log"

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def is_configured(self) -> bool:
        return True

    def send(self, notification: Notification) -> SendResult:
        try:
            logger.info("[NOTIFY:%s] %s", notification.alert_type, notification.message)
            return SendResult(ok=True, channel=self.channel, provider=self.name, message="logged")
        except Exception as e:
            return SendResult(ok=False, channel=self.channel, provider=self.name, error=str(e))
