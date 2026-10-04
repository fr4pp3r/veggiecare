"""Base classes for notifications."""
import abc
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger("notifications")


@dataclass
class Notification:
    alert_type: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    channels: Optional[Sequence[str]] = None
    key: Optional[str] = None
    priority: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class SendResult:
    ok: bool
    channel: str
    provider: str
    message: Optional[str] = None
    error: Optional[str] = None
    latency_ms: Optional[float] = None


class Notifier(abc.ABC):
    name = "base"
    channel = "default"

    @abc.abstractmethod
    def send(self, notification):
        ...

    @abc.abstractmethod
    def is_configured(self):
        ...

    def close(self):
        pass
