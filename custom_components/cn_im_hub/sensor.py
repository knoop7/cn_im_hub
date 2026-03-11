"""Diagnostic sensors for CN IM Hub providers."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .models import HubRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: HubRuntime = entry.runtime_data
    for key, provider_runtime in runtime.providers.items():
        async_add_entities(
            [ProviderStatusSensor(entry, key)],
            True,
            config_subentry_id=provider_runtime.subentry_id,
        )


class ProviderStatusSensor(SensorEntity):
    """Status sensor per provider runtime."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:websocket"

    def __init__(self, entry: ConfigEntry, provider_key: str) -> None:
        self._entry = entry
        self._provider_key = provider_key
        self._attr_unique_id = f"{entry.entry_id}_{provider_key}_status"
        self._attr_name = f"{provider_key} status"

    @property
    def native_value(self) -> str:
        runtime: HubRuntime = self._entry.runtime_data
        provider = runtime.providers.get(self._provider_key)
        if provider is None:
            return "unavailable"
        return provider.status()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id, self._provider_key)},
            name=f"CN IM Hub {self._provider_key}",
            manufacturer="HA China",
            model="IM Provider",
            entry_type="service",
        )
