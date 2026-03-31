"""CN IM Hub integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    ATTR_CHANNEL,
    ATTR_MESSAGE,
    ATTR_TARGET,
    ATTR_WECHAT_ACCOUNT_ID,
    CHANNEL_DINGTALK_GROUP,
    CHANNEL_DINGTALK_USER,
    CHANNEL_FEISHU_CHAT_ID,
    CHANNEL_OPTIONS,
    CHANNEL_QQ_CHANNEL,
    CHANNEL_QQ_GROUP,
    CHANNEL_QQ_USER,
    CHANNEL_WECHAT_USER_ID,
    CHANNEL_WECOM_CHATID,
    CONF_AGENT_ID,
    DOMAIN,
    PROVIDER_WECHAT,
    SERVICE_SEND_MESSAGE,
)
from .models import HubRuntime, ProviderRuntime
from .providers.registry import get_provider_specs

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.SELECT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SEND_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CHANNEL, default=CHANNEL_FEISHU_CHAT_ID): vol.In(CHANNEL_OPTIONS),
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_TARGET, default=""): cv.string,
        vol.Optional(ATTR_WECHAT_ACCOUNT_ID, default=""): cv.string,
        vol.Optional("use_selected_target", default=False): cv.boolean,
    }
)

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    options = dict(entry.options)
    agent_id = str(options.get(CONF_AGENT_ID, "")).strip()
    _LOGGER.debug("Setting up CN IM Hub entry %s with %d subentries", entry.entry_id, len(entry.subentries))

    runtimes = {}
    provider_specs = get_provider_specs()
    for subentry in entry.subentries.values():
        provider = subentry.subentry_type
        cfg = dict(subentry.data)
        spec = provider_specs.get(provider)
        if spec is None:
            _LOGGER.warning("Unknown provider in subentry: %s", provider)
            continue
        runtime = await spec.setup_provider(
            hass,
            cfg,
            agent_id=agent_id,
            subentry_id=subentry.subentry_id,
        )
        runtimes[f"{provider}:{subentry.subentry_id}"] = runtime

    entry.runtime_data = HubRuntime(providers=runtimes)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if runtimes and not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
        _register_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    runtime: HubRuntime = entry.runtime_data
    for provider_runtime in runtime.providers.values():
        await provider_runtime.stop()

    has_any_provider = False
    for existing in hass.config_entries.async_entries(DOMAIN):
        if existing.entry_id == entry.entry_id:
            continue
        rt = getattr(existing, "runtime_data", None)
        if rt and getattr(rt, "providers", None):
            if rt.providers:
                has_any_provider = True
                break

    if not has_any_provider:
        if hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
            hass.services.async_remove(DOMAIN, SERVICE_SEND_MESSAGE)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _runtime_wechat_account_id(provider_runtime: ProviderRuntime) -> str:
    if provider_runtime.key != PROVIDER_WECHAT:
        return ""
    return str(getattr(provider_runtime.client, "_account_id", "")).strip()


def _all_provider_runtimes(hass: HomeAssistant, provider_key: str) -> list[ProviderRuntime]:
    providers: list[ProviderRuntime] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime: HubRuntime = entry.runtime_data
        providers.extend(item for item in runtime.providers.values() if item.key == provider_key)
    return providers


def _select_wechat_runtime(
    runtimes: list[ProviderRuntime],
    *,
    wechat_account_id: str,
    explicit_target: str,
    use_selected_target: bool,
)-> ProviderRuntime | None:
    candidates = list(runtimes)
    if wechat_account_id:
        candidates = [item for item in candidates if _runtime_wechat_account_id(item) == wechat_account_id]
        return candidates[0] if len(candidates) == 1 else None

    if explicit_target:
        matched = [
            item
            for item in candidates
            if any(str(t.get("target", "")).strip() == explicit_target for t in item.known_targets())
        ]
        if len(matched) == 1:
            return matched[0]

    if use_selected_target:
        selected = [item for item in candidates if item.selected_target().strip()]
        if len(selected) == 1:
            return selected[0]

    return candidates[0] if len(candidates) == 1 else None


def _parse_channel(channel: str) -> tuple[str, str]:
    value = (channel or "").strip()
    mapping = {
        CHANNEL_FEISHU_CHAT_ID: ("feishu", "chat_id"),
        CHANNEL_WECOM_CHATID: ("wecom", "chatid"),
        CHANNEL_QQ_USER: ("qq", "user"),
        CHANNEL_QQ_GROUP: ("qq", "group"),
        CHANNEL_QQ_CHANNEL: ("qq", "channel"),
        CHANNEL_DINGTALK_USER: ("dingtalk", "user"),
        CHANNEL_DINGTALK_GROUP: ("dingtalk", "group"),
        CHANNEL_WECHAT_USER_ID: ("wechat", "user_id"),
    }
    mapped = mapping.get(value)
    if mapped is None:
        raise ValueError(f"Unsupported channel: {value}")
    return mapped


def _register_services(hass: HomeAssistant) -> None:
    async def _handle_send_message(call: ServiceCall) -> None:
        channel = str(call.data.get(ATTR_CHANNEL, CHANNEL_FEISHU_CHAT_ID))
        target = call.data.get(ATTR_TARGET, "")
        message = call.data.get(ATTR_MESSAGE, "")
        wechat_account_id = str(call.data.get(ATTR_WECHAT_ACCOUNT_ID, "")).strip()
        use_selected_target = bool(call.data.get("use_selected_target", False))
        if not message:
            return
        requested, normalized_target_type = _parse_channel(channel)
        resolved_target = str(target or "").strip()

        providers = _all_provider_runtimes(hass, requested)
        if not providers:
            _LOGGER.error("No matched provider runtime for send_message")
            return

        if requested == PROVIDER_WECHAT:
            provider = _select_wechat_runtime(
                providers,
                wechat_account_id=wechat_account_id,
                explicit_target=resolved_target,
                use_selected_target=use_selected_target,
            )
            if provider is None:
                raise ValueError(
                    "WeChat account is ambiguous. Set wechat_account_id, or provide a target already seen by one account, "
                    "or set use_selected_target with only one WeChat selector currently chosen."
                )
        else:
            if len(providers) != 1:
                raise ValueError(f"Provider '{requested}' is ambiguous across multiple entries.")
            provider = providers[0]

        if use_selected_target and not resolved_target:
            resolved_target = provider.selected_target()
        if not resolved_target:
            raise ValueError("target is required, or enable use_selected_target after selecting a known target entity")
        await provider.send_text(resolved_target, message, normalized_target_type)

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_MESSAGE,
            _handle_send_message,
            schema=SERVICE_SEND_MESSAGE_SCHEMA,
        )
