"""Describe the speed test and the drill events for the logbook.

Every finished speed test and every completed backup drill is an entry of its
own in the activity. The name carries the result, readable in any language.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook.const import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_DRILL, EVENT_SPEEDTEST


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event[Any]], dict[str, str]]], None],
) -> None:
    """Register the describers of the speed test and the drill event."""

    @callback
    def describe_speedtest(event: Event[Any]) -> dict[str, str]:
        """Return the gateway with the result as name; the message stays empty."""
        return {LOGBOOK_ENTRY_NAME: speedtest_title(event.data), LOGBOOK_ENTRY_MESSAGE: ""}

    @callback
    def describe_drill(event: Event[Any]) -> dict[str, str]:
        """Return the gateway with the drill's results as name; the message stays empty."""
        return {LOGBOOK_ENTRY_NAME: drill_title(event.data), LOGBOOK_ENTRY_MESSAGE: ""}

    async_describe_event(DOMAIN, EVENT_SPEEDTEST, describe_speedtest)
    async_describe_event(DOMAIN, EVENT_DRILL, describe_drill)


def speedtest_title(data: dict[str, Any]) -> str:
    """Return the gateway and the result in symbols and numbers, readable in any language."""
    parts = [_gateway(data)]
    if (download := _number(data.get("download"))) is not None:
        parts.append(f"↓ {download:.0f} Mbit/s")
    if (upload := _number(data.get("upload"))) is not None:
        parts.append(f"↑ {upload:.0f} Mbit/s")
    if (ping := _number(data.get("ping"))) is not None:
        parts.append(f"{ping:.0f} ms")
    if uplink := data.get("uplink_name"):
        parts.append(str(uplink))
    return " · ".join(parts)


def drill_title(data: dict[str, Any]) -> str:
    """Return the gateway and the drill: time to the backup, time back, LTE data."""
    parts = [_gateway(data), "⇄"]
    if (failover := _number(data.get("failover_seconds"))) is not None:
        parts.append(f"→ {failover:.0f} s")
    if (restore := _number(data.get("restore_seconds"))) is not None:
        parts.append(f"← {restore:.0f} s")
    if (used := _number(data.get("data_used_bytes"))) is not None:
        parts.append(f"{used / 1_000_000:.0f} MB")
    return " · ".join(parts)


def _gateway(data: dict[str, Any]) -> str:
    return str(data.get("gateway") or "Network Plus")


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
