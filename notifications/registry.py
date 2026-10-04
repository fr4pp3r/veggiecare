"""Notification registry and throttling/deduplication."""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from .base import Notification, Notifier, SendResult

logger = logging.getLogger("notifications")


class Throttler:
    def __init__(self, throttle_seconds: float = 60.0, dedup_window_seconds: float = 300.0):
        self.throttle_seconds = throttle_seconds
        self.dedup_window_seconds = dedup_window_seconds
        self._last_sent: Dict[Tuple[str, str, Optional[str]], float] = {}
        self._lock = threading.Lock()

    def should_send(self, channel: str, alert_type: str, key: Optional[str] = None) -> bool:
        now = time.time()
        with self._lock:
            k = (channel, alert_type, key)
            last = self._last_sent.get(k)
            if last is None:
                self._last_sent[k] = now
                return True
            if now - last > self.throttle_seconds:
                self._last_sent[k] = now
                return True
            return False

    def mark_sent(self, channel: str, alert_type: str, key: Optional[str] = None, ts: Optional[float] = None):
        now = ts or time.time()
        with self._lock:
            k = (channel, alert_type, key)
            self._last_sent[k] = now


class Registry:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._notifiers: Dict[str, Notifier] = {}
        self.throttler = Throttler(
            throttle_seconds=float(self.config.get("throttle_seconds", 60.0)),
            dedup_window_seconds=float(self.config.get("dedup_window_seconds", 300.0)),
        )

    def register(self, name: str, notifier: Notifier) -> None:
        self._notifiers[name] = notifier

    def get(self, name: str) -> Optional[Notifier]:
        return self._notifiers.get(name)

    def all(self) -> List[Notifier]:
        return list(self._notifiers.values())

    def send(self, notification: Notification, channels: Optional[List[str]] = None) -> List[SendResult]:
        results: List[SendResult] = []
        target_channels = channels or list(notification.channels or [])
        if not target_channels:
            target_channels = self.config.get("default_channels") or ["log"]
        for channel in target_channels:
            notifier = self._notifiers.get(channel)
            if notifier is None:
                continue
            if not self.throttler.should_send(channel, notification.alert_type, notification.key):
                results.append(
                    SendResult(ok=False, channel=channel, provider=notifier.name, message="throttled", error="throttled")
                )
                continue
            try:
                res = notifier.send(notification)
                if res.ok:
                    self.throttler.mark_sent(channel, notification.alert_type, notification.key, ts=notification.timestamp)
                results.append(res)
            except Exception as e:
                logger.exception("send failed for %s", channel)
                results.append(SendResult(ok=False, channel=channel, provider=notifier.name, error=str(e)))
        return results

    def close(self):
        for n in self._notifiers.values():
            try:
                n.close()
            except Exception:
                pass
