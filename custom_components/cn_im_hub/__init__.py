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
    ATTR_MESSAGE,
    ATTR_PROVIDER,
    ATTR_TARGET,
    ATTR_TARGET_TYPE,
    ATTR_TEXT,
    CONF_AGENT_ID,
    DEFAULT_FEISHU_TARGET_TYPE,
    DOMAIN,
    PROVIDER_DINGTALK,
    PROVIDER_FEISHU,
    PROVIDER_QQ,
    PROVIDER_WECHAT,
    PROVIDER_WECOM,
    SERVICE_SEND_MESSAGE,
    SERVICE_TEST_CONVERSATION,
)
from .conversation import ask_home_assistant
from .models import HubRuntime
from .providers.feishu import async_setup_provider as async_setup_feishu
from .providers.dingtalk import async_setup_provider as async_setup_dingtalk
from .providers.qq import async_setup_provider as async_setup_qq
from .providers.wechat import async_setup_provider as async_setup_wechat
from .providers.wecom import async_setup_provider as async_setup_wecom

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]

SERVICE_SEND_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PROVIDER): cv.string,
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_TARGET, default=""): cv.string,
        vol.Optional(ATTR_TARGET_TYPE, default=DEFAULT_FEISHU_TARGET_TYPE): cv.string,
    }
)

SERVICE_TEST_CONVERSATION_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PROVIDER): cv.string,
        vol.Required(ATTR_TEXT): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    options = dict(entry.options)
    agent_id = str(options.get(CONF_AGENT_ID, "")).strip()
    _LOGGER.debug("Setting up CN IM Hub entry %s with %d subentries", entry.entry_id, len(entry.subentries))

    runtimes = {}
    for subentry in entry.subentries.values():
        provider = subentry.subentry_type
        cfg = dict(subentry.data)
        if provider == PROVIDER_FEISHU:
            runtimes[provider] = await async_setup_feishu(
                hass,
                cfg,
                agent_id=agent_id,
                subentry_id=subentry.subentry_id,
            )
        elif provider == PROVIDER_WECOM:
            runtimes[provider] = await async_setup_wecom(
                hass,
                cfg,
                agent_id=agent_id,
                subentry_id=subentry.subentry_id,
            )
        elif provider == PROVIDER_QQ:
            runtimes[provider] = await async_setup_qq(
                hass,
                cfg,
                agent_id=agent_id,
                subentry_id=subentry.subentry_id,
            )
        elif provider == PROVIDER_DINGTALK:
            runtimes[provider] = await async_setup_dingtalk(
                hass,
                cfg,
                agent_id=agent_id,
                subentry_id=subentry.subentry_id,
            )
        elif provider == PROVIDER_WECHAT:
            runtimes[provider] = await async_setup_wechat(
                hass,
                cfg,
                agent_id=agent_id,
                subentry_id=subentry.subentry_id,
            )

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
        if hass.services.has_service(DOMAIN, SERVICE_TEST_CONVERSATION):
            hass.services.async_remove(DOMAIN, SERVICE_TEST_CONVERSATION)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _resolve_provider(entry: ConfigEntry, requested: str | None) -> str | None:
    runtime: HubRuntime = entry.runtime_data
    if requested:
        return requested if requested in runtime.providers else None
    if len(runtime.providers) == 1:
        return next(iter(runtime.providers))
    return None


def _register_services(hass: HomeAssistant) -> None:
    async def _handle_send_message(call: ServiceCall) -> None:
        requested = call.data.get(ATTR_PROVIDER)
        target = call.data.get(ATTR_TARGET, "")
        message = call.data.get(ATTR_MESSAGE, "")
        target_type = call.data.get(ATTR_TARGET_TYPE, DEFAULT_FEISHU_TARGET_TYPE)
        if not message:
            return

        entries = hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            runtime: HubRuntime = entry.runtime_data
            selected = _resolve_provider(entry, requested)
            if not selected:
                continue
            provider = runtime.providers.get(selected)
            if provider is None:
                continue
            await provider.send_text(target, message, target_type)
            return

        _LOGGER.error("No matched provider runtime for send_message")

    async def _handle_test_conversation(call: ServiceCall) -> None:
        text = call.data[ATTR_TEXT]
        provider = call.data.get(ATTR_PROVIDER, "default")
        agent_id = ""
        entries = hass.config_entries.async_entries(DOMAIN)
        if entries:
            agent_id = str(entries[0].options.get(CONF_AGENT_ID, "")).strip()
        reply = await ask_home_assistant(
            hass,
            text,
            conversation_id=f"test:{provider}",
            agent_id=agent_id or None,
        )
        _LOGGER.info("Test conversation provider=%s input=%s reply=%s", provider, text, reply)

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_MESSAGE,
            _handle_send_message,
            schema=SERVICE_SEND_MESSAGE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_TEST_CONVERSATION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_TEST_CONVERSATION,
            _handle_test_conversation,
            schema=SERVICE_TEST_CONVERSATION_SCHEMA,
        )
