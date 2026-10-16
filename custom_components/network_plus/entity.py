"""Base entities for Network Plus."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NetworkConfigEntry, NetworkCoordinator
from .model import LteBackup, Uplink


class NetworkPlusEntity(CoordinatorEntity[NetworkCoordinator]):
    """An entity of the gateway.

    Since Home Assistant 2026.10 a device belongs to a single integration, so
    these entities get their own device. It carries the gateway's MAC address
    as connection, like the gateway's device of the UniFi integration.
    """

    _attr_has_entity_name = True

    def __init__(self, entry: NetworkConfigEntry, key: str) -> None:
        """Initialize the entity."""
        coordinator = entry.runtime_data.coordinator
        super().__init__(coordinator)
        gateway = coordinator.data.gateway
        self._attr_unique_id = f"{gateway.mac}_{key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, format_mac(gateway.mac))},
            identifiers={(DOMAIN, gateway.mac)},
            name=entry.title,
            manufacturer="Ubiquiti",
            model=gateway.model,
            sw_version=gateway.version,
        )


class UplinkEntity(NetworkPlusEntity):
    """An entity of one uplink; unavailable while the gateway does not report it."""

    def __init__(self, entry: NetworkConfigEntry, uplink: Uplink, key: str) -> None:
        """Initialize the entity of ``uplink``."""
        super().__init__(entry, f"{key}_{uplink.key.lower()}")
        self.uplink_key = uplink.key
        self._attr_translation_placeholders = {"uplink": uplink.name}

    @property
    def uplink(self) -> Uplink | None:
        """Return the uplink's current state."""
        return self.coordinator.data.uplinks.get(self.uplink_key)

    @property
    def available(self) -> bool:
        """Return whether the gateway reports the uplink."""
        return super().available and self.uplink is not None


class LteEntity(CoordinatorEntity[NetworkCoordinator]):
    """An entity of the LTE backup device, which hangs below the gateway."""

    _attr_has_entity_name = True

    def __init__(self, entry: NetworkConfigEntry, lte: LteBackup, key: str) -> None:
        """Initialize the entity."""
        coordinator = entry.runtime_data.coordinator
        super().__init__(coordinator)
        self._attr_unique_id = f"{lte.mac}_{key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, format_mac(lte.mac))},
            identifiers={(DOMAIN, lte.mac)},
            name=lte.name,
            manufacturer="Ubiquiti",
            model=lte.model,
            sw_version=lte.version,
            via_device_id=entry.runtime_data.gateway_device_id,
        )

    @property
    def lte(self) -> LteBackup | None:
        """Return the LTE device's current state."""
        return self.coordinator.data.lte

    @property
    def available(self) -> bool:
        """Return whether the console reports the LTE device."""
        return super().available and self.lte is not None


@callback
def add_uplink_entities(
    entry: NetworkConfigEntry,
    add_entities: Callable[[Iterable[Entity]], None],
    create: Callable[[Uplink], Iterable[Entity]],
) -> None:
    """Add the entities of every uplink, also of uplinks that appear later."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [u for k, u in coordinator.data.uplinks.items() if k not in known]
        if not new:
            return
        known.update(u.key for u in new)
        add_entities([entity for uplink in new for entity in create(uplink)])

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


@callback
def add_lte_entities(
    entry: NetworkConfigEntry,
    add_entities: Callable[[Iterable[Entity]], None],
    create: Callable[[LteBackup], Iterable[Entity]],
) -> None:
    """Add the entities of the LTE backup device once the console reports one."""
    coordinator = entry.runtime_data.coordinator
    added = False

    @callback
    def _add_new() -> None:
        nonlocal added
        lte = coordinator.data.lte
        if added or lte is None:
            return
        added = True
        add_entities(create(lte))

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
