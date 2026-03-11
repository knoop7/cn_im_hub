"""WeChat (personal) provider binding for internal gateway."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from ..const import CONF_WECHAT_TOKEN, PROVIDER_WECHAT
from ..models import ProviderRuntime
from ..wechat_gateway import get_wechat_gateway_manager


async def async_validate_config(_: HomeAssistant, config: dict[str, str]) -> None:
    token = str(config.get(CONF_WECHAT_TOKEN, "")).strip()
    if not token:
        raise ValueError("wechat_token is required")


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, str],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    token = str(config.get(CONF_WECHAT_TOKEN, "")).strip()
    manager = get_wechat_gateway_manager(hass)
    manager.register_binding(subentry_id=subentry_id, token=token, agent_id=agent_id)

    async def _stop() -> None:
        manager.unregister_binding(subentry_id)

    async def _send(target: str, message: str, target_type: str) -> None:
        raise RuntimeError("WeChat personal service does not support direct send_message")

    return ProviderRuntime(
        key=PROVIDER_WECHAT,
        title="WeChat",
        subentry_id=subentry_id,
        client=None,
        stop=_stop,
        send_text=_send,
        status=lambda: manager.get_status(subentry_id),
    )
