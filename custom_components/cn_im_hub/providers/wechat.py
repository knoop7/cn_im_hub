"""Weixin provider based on Tencent OpenClaw Weixin plugin protocol."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..command import execute_command, parse_command
from ..const import (
    CONF_WECHAT_ACCOUNT_ID,
    CONF_WECHAT_BASE_URL,
    CONF_WECHAT_TOKEN,
    CONF_WECHAT_USER_ID,
    PROVIDER_WECHAT,
    WECHAT_DEFAULT_BASE_URL,
)
from ..models import ProviderRuntime
from ..wechat_weixin_auth import async_get_updates, async_send_weixin_text, extract_text_body
from .base import ProviderSpec
from .wechat_flow import WeixinProviderSubentryFlow

_LOGGER = logging.getLogger(__name__)
_STORE_VERSION = 1


class WeixinClient:
    """Long-poll Weixin client for pure text conversation."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        account_id: str,
        token: str,
        base_url: str,
        user_id: str,
        conversation_agent_id: str,
        subentry_id: str,
    ) -> None:
        self._hass = hass
        self._account_id = account_id
        self._token = token
        self._base_url = base_url
        self._user_id = user_id
        self._conversation_agent_id = conversation_agent_id
        self._subentry_id = subentry_id
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._status = "disconnected"
        self._context_tokens: dict[str, str] = {}
        self._store: Store[dict[str, str]] = Store(hass, _STORE_VERSION, f"cn_im_hub_wechat_{subentry_id}")
        self._sync_buf = ""

    @property
    def status(self) -> str:
        return self._status

    async def start(self) -> None:
        data = await self._store.async_load() or {}
        self._sync_buf = str(data.get("get_updates_buf") or "")
        self._stopping = False
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._status = "disconnected"

    async def send_text(self, _: str, __: str, ___: str) -> None:
        raise RuntimeError("Weixin provider does not support direct send_message without active context")

    async def _run(self) -> None:
        consecutive_failures = 0
        next_timeout_ms = 35_000
        while not self._stopping:
            self._status = "connected" if consecutive_failures == 0 else "reconnecting"
            try:
                resp = await async_get_updates(
                    self._hass,
                    base_url=self._base_url,
                    token=self._token,
                    get_updates_buf=self._sync_buf,
                    timeout_ms=next_timeout_ms,
                )
                consecutive_failures = 0
                if isinstance(resp.get("longpolling_timeout_ms"), int) and resp["longpolling_timeout_ms"] > 0:
                    next_timeout_ms = int(resp["longpolling_timeout_ms"])
                new_buf = str(resp.get("get_updates_buf") or "")
                if new_buf and new_buf != self._sync_buf:
                    self._sync_buf = new_buf
                    await self._store.async_save({"get_updates_buf": self._sync_buf})
                for message in resp.get("msgs") or []:
                    if not isinstance(message, dict):
                        continue
                    await self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                consecutive_failures += 1
                self._status = "error"
                _LOGGER.warning("Weixin long-poll error (%s): %s", self._account_id, err)
                await asyncio.sleep(30 if consecutive_failures >= 3 else 2)
        self._status = "disconnected"

    async def _handle_message(self, message: dict[str, Any]) -> None:
        from_user_id = str(message.get("from_user_id") or "").strip()
        if not from_user_id:
            return
        text = extract_text_body(message)
        if not text:
            return
        context_token = str(message.get("context_token") or "").strip()
        if context_token:
            self._context_tokens[from_user_id] = context_token

        command = parse_command(text)
        if command is None:
            return
        reply = await execute_command(
            self._hass,
            command,
            conversation_id=f"wechat:{self._account_id}:{from_user_id}",
            agent_id=self._conversation_agent_id or None,
        )
        if not reply:
            return
        resolved_context = self._context_tokens.get(from_user_id)
        if not resolved_context:
            _LOGGER.warning("Weixin context_token missing for user %s", from_user_id)
            return
        await async_send_weixin_text(
            self._hass,
            base_url=self._base_url,
            token=self._token,
            to_user_id=from_user_id,
            context_token=resolved_context,
            text=reply,
        )


async def async_validate_config(_: HomeAssistant, config: dict[str, Any]) -> None:
    token = str(config.get(CONF_WECHAT_TOKEN, "")).strip()
    account_id = str(config.get(CONF_WECHAT_ACCOUNT_ID, "")).strip()
    if not token or not account_id:
        raise ValueError("wechat_token and wechat_account_id are required")


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    client = WeixinClient(
        hass,
        account_id=str(config.get(CONF_WECHAT_ACCOUNT_ID, "")).strip(),
        token=str(config.get(CONF_WECHAT_TOKEN, "")).strip(),
        base_url=str(config.get(CONF_WECHAT_BASE_URL, WECHAT_DEFAULT_BASE_URL)).strip() or WECHAT_DEFAULT_BASE_URL,
        user_id=str(config.get(CONF_WECHAT_USER_ID, "")).strip(),
        conversation_agent_id=agent_id,
        subentry_id=subentry_id,
    )
    await client.start()

    async def _send(target: str, message: str, target_type: str) -> None:
        await client.send_text(target, message, target_type)

    return ProviderRuntime(
        key=PROVIDER_WECHAT,
        title="WeChat",
        subentry_id=subentry_id,
        client=client,
        stop=client.stop,
        send_text=_send,
        status=lambda: client.status,
    )


def _build_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_WECHAT_TOKEN, default=current.get(CONF_WECHAT_TOKEN, "")): str,
            vol.Required(CONF_WECHAT_ACCOUNT_ID, default=current.get(CONF_WECHAT_ACCOUNT_ID, "")): str,
            vol.Optional(CONF_WECHAT_BASE_URL, default=current.get(CONF_WECHAT_BASE_URL, WECHAT_DEFAULT_BASE_URL)): str,
        }
    )


PROVIDER_SPEC = ProviderSpec(
    key=PROVIDER_WECHAT,
    title="WeChat",
    schema_builder=_build_schema,
    validate_config=async_validate_config,
    setup_provider=async_setup_provider,
    flow_handler=WeixinProviderSubentryFlow,
)
