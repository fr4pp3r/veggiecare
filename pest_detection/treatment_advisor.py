"""IPM-first treatment advice for detected plant pests.

The advisor is a zero-ML, config-driven lookup table (the "knowledge base")
defined under ``pest_detection.suggestions`` / ``pest_detection.treatments``
in ``config/config.yaml``. It maps a detected pest class to culturally-,
organically- and (last resort) chemically-oriented guidance, and is consulted
by Lane B callers to surface treatment steps to the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_str_list(value: Any) -> list[str]:
    """Coerce a config value into a list of strings, tolerating bad data."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif item is not None:
            out.append(str(item))
    return out


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


@dataclass
class TreatmentAdvice:
    """IPM treatment guidance produced for a detected pest class."""

    matched: bool
    pest_class: str | None = None
    treatment_id: str | None = None
    title: str | None = None
    severity: str | None = None  # "low" | "moderate" | "high"
    summary: str | None = None
    cultural: list[str] = field(default_factory=list)
    organic: list[str] = field(default_factory=list)
    chemical: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    disclaimer: str | None = None
    model: str = "rule_engine"

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serialisable dict (lists stay lists)."""
        return {
            "matched": self.matched,
            "pest_class": self.pest_class,
            "treatment_id": self.treatment_id,
            "title": self.title,
            "severity": self.severity,
            "summary": self.summary,
            "cultural": list(self.cultural),
            "organic": list(self.organic),
            "chemical": list(self.chemical),
            "notes": list(self.notes),
            "disclaimer": self.disclaimer,
            "model": self.model,
        }


class PestTreatmentAdvisor:
    """Rule-based (config-driven) pest treatment advisor."""

    name = "rule_engine"

    def __init__(self, config: dict) -> None:
        self._config: dict[str, Any] = config if isinstance(config, dict) else {}
        self._disclaimer: str | None = None
        self._fallback: dict[str, Any] | None = None
        self._entries: list[dict[str, Any]] = []
        self._lookup: dict[str, dict[str, Any]] = {}
        self._treatments_configured: int = 0

        try:
            pest_cfg = self._config.get("pest_detection", {})
            if not isinstance(pest_cfg, dict):
                pest_cfg = {}

            sugg = pest_cfg.get("suggestions", {})
            if not isinstance(sugg, dict):
                sugg = {}

            disc = sugg.get("disclaimer")
            self._disclaimer = disc if isinstance(disc, str) else None

            fb = sugg.get("fallback", None)
            self._fallback = fb if isinstance(fb, dict) else None

            # treatments live beside suggestions (pest_detection.treatments)
            treatments = pest_cfg.get("treatments", None)
            if treatments is None:
                treatments = sugg.get("treatments", [])
            if not isinstance(treatments, list):
                treatments = []

            self._entries = [t for t in treatments if isinstance(t, dict)]
            self._treatments_configured = len(self._entries)

            for entry in self._entries:
                names = entry.get("names", [])
                if not isinstance(names, list):
                    continue
                for name in names:
                    if isinstance(name, str) and name.strip():
                        key = name.strip().lower()
                        if key not in self._lookup:
                            self._lookup[key] = entry
        except Exception:
            # Never let construction fail; degrade to an empty KB.
            self._disclaimer = self._disclaimer if isinstance(self._disclaimer, str) else None
            self._fallback = self._fallback if isinstance(self._fallback, dict) else None
            self._entries = []
            self._lookup = {}
            self._treatments_configured = 0

    def suggest(self, pest_class: str | None) -> TreatmentAdvice:
        try:
            if isinstance(pest_class, str):
                echoed = pest_class.strip()
                key = echoed.lower() if echoed else None
            else:
                echoed = pest_class  # None or other
                key = None
                if pest_class is not None:
                    try:
                        s = str(pest_class).strip()
                        echoed = s
                        key = s.lower() if s else None
                    except Exception:
                        echoed = None
                        key = None

            # Empty / None input -> fallback (or empty advice).
            if key is None or key == "":
                return self._fallback_advice(echoed)

            entry = self._lookup.get(key)
            if not isinstance(entry, dict):
                return self._fallback_advice(echoed)

            return TreatmentAdvice(
                matched=True,
                pest_class=echoed,
                treatment_id=_str_or_none(entry.get("id")),
                title=_str_or_none(entry.get("title")),
                severity=_str_or_none(entry.get("severity")),
                summary=_str_or_none(entry.get("summary")),
                cultural=_as_str_list(entry.get("cultural")),
                organic=_as_str_list(entry.get("organic")),
                chemical=_as_str_list(entry.get("chemical")),
                notes=_as_str_list(entry.get("notes")),
                disclaimer=self._disclaimer,
                model=self.name,
            )
        except Exception:
            return TreatmentAdvice(
                matched=False,
                pest_class=None,
                disclaimer=self._disclaimer,
                model=self.name,
            )

    def _fallback_advice(self, pest_class: str | None) -> TreatmentAdvice:
        fb = self._fallback
        if isinstance(fb, dict):
            return TreatmentAdvice(
                matched=False,
                pest_class=pest_class,
                treatment_id=None,
                title=_str_or_none(fb.get("title")),
                severity=None,
                summary=_str_or_none(fb.get("summary")),
                cultural=_as_str_list(fb.get("cultural")),
                organic=_as_str_list(fb.get("organic")),
                chemical=_as_str_list(fb.get("chemical")),
                notes=_as_str_list(fb.get("notes")),
                disclaimer=self._disclaimer,
                model=self.name,
            )
        return TreatmentAdvice(
            matched=False,
            pest_class=pest_class,
            disclaimer=self._disclaimer,
            model=self.name,
        )

    def status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "model": self.name,
            "treatments_configured": self._treatments_configured,
        }
