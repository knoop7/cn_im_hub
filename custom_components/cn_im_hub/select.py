"""Select entities for choosing known targets per provider."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
            [ProviderKnownTargetSelect(entry, key)],
            True,
            config_subentry_id=provider_runtime.subentry_id,
        )


class ProviderKnownTargetSelect(SelectEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:account-search"

    def __init__(self, entry: ConfigEntry, provider_key: str) -> None:
        self._entry = entry
        self._provider_key = provider_key
        self._attr_unique_id = f"{entry.entry_id}_{provider_key}_known_target_select"
        self._attr_name = f"{provider_key} target selector"

    @property
    def current_option(self) -> str | None:
        runtime: HubRuntime = self._entry.runtime_data
        provider = runtime.providers.get(self._provider_key)
        if provider is None:
            return None
        value = provider.selected_target()
        return value or None

    @property
    def options(self) -> list[str]:
        runtime: HubRuntime = self._entry.runtime_data
        provider = runtime.providers.get(self._provider_key)
        if provider is None:
            return []
        return [item.get("target", "") for item in provider.known_targets() if item.get("target")]

    async def async_select_option(self, option: str) -> None:
        runtime: HubRuntime = self._entry.runtime_data
        provider = runtime.providers.get(self._provider_key)
        if provider is None:
            return
        await provider.select_target(option)
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id, self._provider_key)},
            name=f"CN IM Hub {self._provider_key}",
            manufacturer="HA China",
            model="IM Provider",
            entry_type="service",
        )
