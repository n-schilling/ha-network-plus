"""Describe the speed test event for the logbook.

Every finished speed test is an entry of its own in the activity, and the
speed test sensors change state in its context. The activity's "What
happened" shows the name of that cause, so the name carries the result.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook.const import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_SPEEDTEST


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event[Any]], dict[str, str]]], None],
) -> None:
    """Register the describer of the speed test event."""

    @callback
    def describe(event: Event[Any]) -> dict[str, str]:
        """Return the gateway with the result as name; the message stays empty."""
        return {LOGBOOK_ENTRY_NAME: speedtest_title(event.data), LOGBOOK_ENTRY_MESSAGE: ""}

    async_describe_event(DOMAIN, EVENT_SPEEDTEST, describe)


def speedtest_title(data: dict[str, Any]) -> str:
    """Return the gateway and the result in symbols and numbers, readable in any language."""
    parts = [str(data.get("gateway") or "Network Plus")]
    if (download := _number(data.get("download"))) is not None:
        parts.append(f"\u2193 {download:.0f} Mbit/s")
    if (upload := _number(data.get("upload"))) is not None:
        parts.append(f"\u2191 {upload:.0f} Mbit/s")
    if (ping := _number(data.get("ping"))) is not None:
        parts.append(f"{ping:.0f} ms")
    if uplink := data.get("uplink_name"):
        parts.append(str(uplink))
    return " \u00b7 ".join(parts)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
