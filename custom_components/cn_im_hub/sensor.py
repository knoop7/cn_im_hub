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
    for runtime_key, provider_runtime in runtime.providers.items():
        async_add_entities(
            [
                ProviderStatusSensor(entry, runtime_key, provider_runtime.title),
                ProviderKnownTargetsSensor(entry, runtime_key, provider_runtime.title),
            ],
            True,
            config_subentry_id=provider_runtime.subentry_id,
        )


class ProviderStatusSensor(SensorEntity):
    """Status sensor per provider runtime."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:websocket"

    def __init__(self, entry: ConfigEntry, runtime_key: str, display_name: str) -> None:
        self._entry = entry
        self._runtime_key = runtime_key
        self._display_name = display_name
        self._attr_unique_id = f"{entry.entry_id}_{runtime_key}_status"
        self._attr_name = f"{display_name} status"

    @property
    def native_value(self) -> str:
        runtime: HubRuntime = self._entry.runtime_data
        provider = runtime.providers.get(self._runtime_key)
        if provider is None:
            return "unavailable"
        return provider.status()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id, self._runtime_key)},
            name=f"CN IM Hub {self._display_name}",
            manufacturer="HA China",
            model="IM Provider",
            entry_type="service",
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        runtime: HubRuntime = self._entry.runtime_data
        provider = runtime.providers.get(self._runtime_key)
        if provider is None:
            return {}
        return {"known_targets": provider.known_targets()}


class ProviderKnownTargetsSensor(SensorEntity):
    """Dedicated entity for known inbound targets per provider."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:account-details"

    def __init__(self, entry: ConfigEntry, runtime_key: str, display_name: str) -> None:
        self._entry = entry
        self._runtime_key = runtime_key
        self._display_name = display_name
        self._attr_unique_id = f"{entry.entry_id}_{runtime_key}_known_targets"
        self._attr_name = f"{display_name} known targets"

    @property
    def native_value(self) -> int:
        runtime: HubRuntime = self._entry.runtime_data
        provider = runtime.providers.get(self._runtime_key)
        if provider is None:
            return "none"
        targets = provider.known_targets()
        if not targets:
            return "none"
        ids = [str(item.get("target", "")).strip() for item in targets if str(item.get("target", "")).strip()]
        if not ids:
            return "none"
        preview = ids[:3]
        text = ", ".join(preview)
        if len(ids) > 3:
            text += f" (+{len(ids) - 3})"
        return text

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        runtime: HubRuntime = self._entry.runtime_data
        provider = runtime.providers.get(self._runtime_key)
        if provider is None:
            return {"known_targets": []}
        return {"known_targets": provider.known_targets()}

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id, self._runtime_key)},
            name=f"CN IM Hub {self._display_name}",
            manufacturer="HA China",
            model="IM Provider",
            entry_type="service",
        )
