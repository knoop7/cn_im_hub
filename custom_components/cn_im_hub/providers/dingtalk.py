"""DingTalk provider using Stream mode (no HTTP callback)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..command import execute_command, parse_command
from ..const import (
    CONF_DINGTALK_CLIENT_ID,
    CONF_DINGTALK_CLIENT_SECRET,
    PROVIDER_DINGTALK,
)
from ..models import ProviderRuntime
from .base import ProviderSpec

_LOGGER = logging.getLogger(__name__)
_OAUTH_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
_API_BASE = "https://api.dingtalk.com"


class DingTalkClient:
    def __init__(self, hass: HomeAssistant, client_id: str, client_secret: str, agent_id: str) -> None:
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._client_id = client_id
        self._client_secret = client_secret
        self._agent_id = agent_id
        self._status = "disconnected"
        self._task: asyncio.Task[None] | None = None
        self._token = ""
        self._token_expire = 0.0

    @property
    def status(self) -> str:
        return self._status

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_stream())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._status = "disconnected"

    async def send_text(self, target: str, text: str, target_type: str) -> None:
        token = await self._get_token()
        target = target.strip()
        if not target:
            raise ValueError("DingTalk target is required")

        if target_type == "user":
            path = "/v1.0/robot/oToMessages/batchSend"
            body = {
                "robotCode": self._client_id,
                "userIds": [target],
                "msgKey": "sampleText",
                "msgParam": '{"content":"%s"}' % text.replace('"', '\\"'),
            }
        else:
            path = "/v1.0/robot/groupMessages/send"
            body = {
                "robotCode": self._client_id,
                "openConversationId": target,
                "msgKey": "sampleText",
                "msgParam": '{"content":"%s"}' % text.replace('"', '\\"'),
            }

        async with self._session.post(
            f"{_API_BASE}{path}",
            headers={"x-acs-dingtalk-access-token": token},
            json=body,
            timeout=15,
        ) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"DingTalk send failed: {resp.status} {await resp.text()}")

    async def _run_stream(self) -> None:
        """Use official Stream SDK if available, without webhook mode."""
        self._status = "connecting"
        try:
            import dingtalk_stream

            outer = self

            class _Handler(dingtalk_stream.ChatbotHandler):
                async def process(self, callback):
                    incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                    text = str(getattr(getattr(incoming, "text", None), "content", "") or "").strip()
                    if not text:
                        return dingtalk_stream.AckMessage.STATUS_OK, "OK"

                    try:
                        command = parse_command(text)
                    except ValueError as err:
                        self.reply_text(f"Invalid command: {err}", incoming)
                        return dingtalk_stream.AckMessage.STATUS_OK, "OK"

                    if command is None:
                        return dingtalk_stream.AckMessage.STATUS_OK, "OK"

                    fut = asyncio.run_coroutine_threadsafe(
                        execute_command(
                            outer._hass,
                            command,
                            conversation_id="dingtalk:stream",
                            agent_id=outer._agent_id,
                        ),
                        outer._hass.loop,
                    )
                    try:
                        reply = fut.result(timeout=30)
                    except Exception as err:
                        _LOGGER.warning("DingTalk command execution failed: %s", err)
                        reply = f"Execution failed: {type(err).__name__}"

                    self.reply_text(reply, incoming)
                    return dingtalk_stream.AckMessage.STATUS_OK, "OK"

            credential = dingtalk_stream.Credential(self._client_id, self._client_secret)
            client = dingtalk_stream.DingTalkStreamClient(credential)
            client.register_callback_handler(dingtalk_stream.chatbot.ChatbotMessage.TOPIC, _Handler())
            self._status = "connected"
            await asyncio.to_thread(client.start_forever)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning("DingTalk stream not started: %s", err)
            self._status = "error"

    async def _get_token(self) -> str:
        now = asyncio.get_running_loop().time()
        if self._token and now < self._token_expire - 300:
            return self._token

        async with self._session.post(
            _OAUTH_URL,
            json={"appKey": self._client_id, "appSecret": self._client_secret},
            timeout=15,
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(f"DingTalk token fetch failed: {resp.status} {data}")

        token = str(data.get("accessToken") or "")
        if not token:
            raise RuntimeError(f"DingTalk accessToken missing: {data}")
        self._token = token
        self._token_expire = now + int(data.get("expireIn") or 7200)
        return token


async def async_validate_config(_: HomeAssistant, config: dict[str, Any]) -> None:
    client_id = str(config.get(CONF_DINGTALK_CLIENT_ID, "")).strip()
    client_secret = str(config.get(CONF_DINGTALK_CLIENT_SECRET, "")).strip()
    if not client_id or not client_secret:
        raise ValueError("dingtalk_client_id and dingtalk_client_secret are required")


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    client_id = str(config.get(CONF_DINGTALK_CLIENT_ID, "")).strip()
    client_secret = str(config.get(CONF_DINGTALK_CLIENT_SECRET, "")).strip()
    client = DingTalkClient(hass, client_id, client_secret, agent_id)
    await client.start()

    async def _send(target: str, message: str, target_type: str) -> None:
        mode = target_type if target_type in ("group", "user") else "group"
        await client.send_text(target, message, mode)

    return ProviderRuntime(
        key=PROVIDER_DINGTALK,
        title="DingTalk",
        subentry_id=subentry_id,
        client=client,
        stop=client.stop,
        send_text=_send,
        status=lambda: client.status,
    )


def _build_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_DINGTALK_CLIENT_ID, default=current.get(CONF_DINGTALK_CLIENT_ID, "")): str,
            vol.Required(CONF_DINGTALK_CLIENT_SECRET, default=current.get(CONF_DINGTALK_CLIENT_SECRET, "")): str,
        }
    )


PROVIDER_SPEC = ProviderSpec(
    key=PROVIDER_DINGTALK,
    title="DingTalk",
    schema_builder=_build_schema,
    validate_config=async_validate_config,
    setup_provider=async_setup_provider,
)
