"""Base entities for Network Plus."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, EXPECTED_SPEED_OPTIONS
from .coordinator import NetworkConfigEntry, NetworkCoordinator
from .model import Gateway, LteBackup, NetworkState, Uplink


def gateway_device_info(gateway: Gateway, name: str) -> DeviceInfo:
    """Return the gateway's device, as the registry and the entities describe it."""
    return DeviceInfo(
        connections={(CONNECTION_NETWORK_MAC, format_mac(gateway.mac))},
        identifiers={(DOMAIN, gateway.mac)},
        name=name,
        manufacturer="Ubiquiti",
        model=gateway.model,
        sw_version=gateway.version,
    )


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
        self._attr_device_info = gateway_device_info(gateway, entry.title)


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
    """An entity of one LTE backup device, which hangs below the gateway."""

    _attr_has_entity_name = True

    def __init__(self, entry: NetworkConfigEntry, lte: LteBackup, key: str) -> None:
        """Initialize the entity of the LTE device with ``lte.mac``."""
        coordinator = entry.runtime_data.coordinator
        super().__init__(coordinator)
        self.lte_mac = lte.mac
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
        return (self.coordinator.data.lte or {}).get(self.lte_mac)

    @property
    def available(self) -> bool:
        """Return whether the console reports the LTE device."""
        return super().available and self.lte is not None


@callback
def remove_stale_entities(hass: HomeAssistant, platform: str, unique_ids: Iterable[str]) -> None:
    """Drop entities from the registry that the configuration no longer calls for.

    Used when the admin opt-in was withdrawn or an expected link speed removed.
    """
    registry = er.async_get(hass)
    for unique_id in unique_ids:
        if entity_id := registry.async_get_entity_id(platform, DOMAIN, unique_id):
            registry.async_remove(entity_id)


def expected_speed(entry: NetworkConfigEntry, key: str) -> int | None:
    """Return the link speed configured for the uplink ``key``, in Mbit/s; None if none."""
    option = EXPECTED_SPEED_OPTIONS.get(key)
    value = entry.options.get(option) if option else None
    return int(value) if isinstance(value, int | float) and value > 0 else None


@callback
def add_dynamic_entities[T](
    entry: NetworkConfigEntry,
    add_entities: Callable[[Iterable[Entity]], None],
    items: Callable[[NetworkState], Mapping[str, T]],
    create: Callable[[T], Iterable[Entity]],
) -> None:
    """Add entities for every item of ``items(state)``, also for items that appear later.

    Used for the uplinks (by network group) and the LTE devices (by MAC).
    """
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [(key, item) for key, item in items(coordinator.data).items() if key not in known]
        if not new:
            return
        known.update(key for key, _ in new)
        add_entities([entity for _, item in new for entity in create(item)])

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
