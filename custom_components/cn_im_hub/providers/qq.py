"""QQ provider using WebSocket gateway (no HTTP callback)."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..command import execute_command, parse_command
from ..const import CONF_QQ_APP_ID, CONF_QQ_CLIENT_SECRET, PROVIDER_QQ
from ..known_targets import async_get_tracker
from ..models import ProviderRuntime
from .base import ProviderSpec

_LOGGER = logging.getLogger(__name__)
_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
_API_BASE = "https://api.sgroup.qq.com"
_INTENTS = (1 << 30) | (1 << 12) | (1 << 25)


class QQClient:
    def __init__(self, hass: HomeAssistant, app_id: str, client_secret: str, agent_id: str) -> None:
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._app_id = app_id
        self._client_secret = client_secret
        self._agent_id = agent_id
        self._task: asyncio.Task[None] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._status = "disconnected"
        self._token = ""
        self._token_expire = 0.0

    @property
    def status(self) -> str:
        return self._status

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self._status = "disconnected"

    async def send_text(self, target: str, text: str) -> None:
        token = await self._get_token()
        kind, ident = _split_target(target)
        if kind == "user":
            path = f"/v2/users/{ident}/messages"
            body: dict[str, Any] = {"content": text, "msg_type": 0}
        elif kind == "group":
            path = f"/v2/groups/{ident}/messages"
            body = {"content": text, "msg_type": 0}
        elif kind == "channel":
            path = f"/channels/{ident}/messages"
            body = {"content": text}
        else:
            raise ValueError("QQ target must be user:/group:/channel:")

        async with self._session.post(
            f"{_API_BASE}{path}",
            headers={"Authorization": f"QQBot {token}"},
            json=body,
            timeout=15,
        ) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"QQ send failed: {resp.status} {await resp.text()}")

    async def send_image(self, target: str, image_bytes: bytes, target_type: str) -> None:
        token = await self._get_token()
        if not image_bytes:
            raise ValueError("QQ image data is empty")
        kind = target_type.strip().lower() if target_type else _split_target(target)[0]
        ident = target.strip()
        if ":" in ident:
            kind, ident = _split_target(ident)
        file_info = await self._upload_image(token, ident, kind, image_bytes)
        if kind == "user":
            path = f"/v2/users/{ident}/messages"
        elif kind == "group":
            path = f"/v2/groups/{ident}/messages"
        else:
            raise ValueError("QQ image sending only supports user and group targets")
        body = {"msg_type": 7, "media": {"file_info": file_info}}
        async with self._session.post(
            f"{_API_BASE}{path}",
            headers={"Authorization": f"QQBot {token}"},
            json=body,
            timeout=30,
        ) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"QQ image send failed: {resp.status} {await resp.text()}")

    async def _upload_image(self, token: str, ident: str, kind: str, image_bytes: bytes) -> str:
        if kind == "user":
            path = f"/v2/users/{ident}/files"
        elif kind == "group":
            path = f"/v2/groups/{ident}/files"
        else:
            raise ValueError("QQ image upload only supports user and group targets")
        body = {
            "file_type": 1,
            "srv_send_msg": False,
            "file_data": base64.b64encode(image_bytes).decode("ascii"),
        }
        async with self._session.post(
            f"{_API_BASE}{path}",
            headers={"Authorization": f"QQBot {token}"},
            json=body,
            timeout=30,
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(f"QQ image upload failed: {resp.status} {data}")
        file_info = str(data.get("file_info") or "")
        if not file_info:
            raise RuntimeError(f"QQ image upload missing file_info: {data}")
        return file_info

    async def _run(self) -> None:
        while True:
            self._status = "connecting"
            try:
                token = await self._get_token()
                gateway = await self._get_gateway(token)
                self._ws = await self._session.ws_connect(gateway, heartbeat=30)
                self._status = "connected"
                async for msg in self._ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    await self._handle_payload(json.loads(msg.data))
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("QQ loop error: %s", err)
                self._status = "error"
            finally:
                if self._ws and not self._ws.closed:
                    await self._ws.close()
                self._ws = None
                if self._status != "error":
                    self._status = "disconnected"
            await asyncio.sleep(3)

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        if payload.get("op") == 10:
            await self._identify()
            return
        if payload.get("op") != 0:
            return

        event_type = str(payload.get("t") or "")
        data = payload.get("d") or {}
        parsed = _parse_inbound(event_type, data)
        if not parsed:
            return
        text, target = parsed

        try:
            command = parse_command(text)
        except ValueError as err:
            await self.send_text(target, f"Invalid command: {err}")
            return
        if command is None:
            return

        try:
            reply = await execute_command(
                self._hass,
                command,
                conversation_id=f"qq:{target}",
                agent_id=self._agent_id,
            )
        except Exception as err:
            _LOGGER.exception("QQ command execution failed: %s", err)
            reply = f"Execution failed: {type(err).__name__}"

        await self.send_text(target, reply)

    async def _identify(self) -> None:
        token = await self._get_token()
        if self._ws and not self._ws.closed:
            await self._ws.send_json({"op": 2, "d": {"token": f"QQBot {token}", "intents": _INTENTS, "shard": [0, 1]}})

    async def _get_gateway(self, token: str) -> str:
        async with self._session.get(f"{_API_BASE}/gateway", headers={"Authorization": f"QQBot {token}"}, timeout=15) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(f"QQ gateway fetch failed: {resp.status} {data}")
        url = str(data.get("url") or "")
        if not url:
            raise RuntimeError("QQ gateway url missing")
        return url

    async def _get_token(self) -> str:
        now = asyncio.get_running_loop().time()
        if self._token and now < self._token_expire - 300:
            return self._token
        async with self._session.post(_TOKEN_URL, json={"appId": self._app_id, "clientSecret": self._client_secret}, timeout=15) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(f"QQ token fetch failed: {resp.status} {data}")
        token = str(data.get("access_token") or "")
        if not token:
            raise RuntimeError("QQ access_token missing")
        self._token = token
        self._token_expire = now + int(data.get("expires_in") or 7200)
        return token


def _split_target(target: str) -> tuple[str, str]:
    if ":" not in target:
        return "", target.strip()
    kind, ident = target.split(":", 1)
    return kind.strip().lower(), ident.strip()


def _normalize_outbound_target(target: str, target_type: str) -> str:
    target = target.strip()
    if ":" in target:
        return target
    if target_type in ("user", "group", "channel"):
        return f"{target_type}:{target}"
    return target


def _parse_inbound(event_type: str, data: dict[str, Any]) -> tuple[str, str] | None:
    text = str(data.get("content") or "").strip()
    if not text:
        return None
    if event_type == "C2C_MESSAGE_CREATE":
        user = str((data.get("author") or {}).get("user_openid") or "")
        return (text, f"user:{user}") if user else None
    if event_type == "GROUP_AT_MESSAGE_CREATE":
        group = str(data.get("group_openid") or "")
        return (text, f"group:{group}") if group else None
    if event_type in ("AT_MESSAGE_CREATE", "DIRECT_MESSAGE_CREATE"):
        channel = str(data.get("channel_id") or "")
        return (text, f"channel:{channel}") if channel else None
    return None


async def async_validate_config(_: HomeAssistant, config: dict[str, Any]) -> None:
    app_id = str(config.get(CONF_QQ_APP_ID, "")).strip()
    client_secret = str(config.get(CONF_QQ_CLIENT_SECRET, "")).strip()
    if not app_id or not client_secret:
        raise ValueError("qq_app_id and qq_client_secret are required")


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    app_id = str(config.get(CONF_QQ_APP_ID, "")).strip()
    client_secret = str(config.get(CONF_QQ_CLIENT_SECRET, "")).strip()
    client = QQClient(hass, app_id, client_secret, agent_id)
    tracker = await async_get_tracker(hass, subentry_id)

    original_handle_payload = client._handle_payload

    async def _handle_payload_with_tracking(payload: dict[str, Any]) -> None:
        if payload.get("op") == 0:
            event_type = str(payload.get("t") or "")
            data = payload.get("d") or {}
            parsed = _parse_inbound(event_type, data)
            if parsed:
                _, target = parsed
                kind, ident = _split_target(target)
                await tracker.async_record(
                    provider=PROVIDER_QQ,
                    target=ident or target,
                    target_type=kind,
                    display_name=str((data.get("author") or {}).get("username") or ident or target),
                )
        await original_handle_payload(payload)

    client._handle_payload = _handle_payload_with_tracking
    await client.start()

    async def _send(target: str, message: str, target_type: str) -> None:
        await client.send_text(_normalize_outbound_target(target, target_type), message)

    async def _send_image(target: str, image_bytes: bytes, target_type: str) -> None:
        await client.send_image(_normalize_outbound_target(target, target_type), image_bytes, target_type)

    return ProviderRuntime(
        key=PROVIDER_QQ,
        title="QQ",
        subentry_id=subentry_id,
        client=client,
        stop=client.stop,
        send_text=_send,
        status=lambda: client.status,
        known_targets=tracker.snapshot,
        selected_target=tracker.selected_target,
        select_target=tracker.async_select_target,
        send_image=_send_image,
    )


def _build_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_QQ_APP_ID, default=current.get(CONF_QQ_APP_ID, "")): str,
            vol.Required(CONF_QQ_CLIENT_SECRET, default=current.get(CONF_QQ_CLIENT_SECRET, "")): str,
        }
    )


PROVIDER_SPEC = ProviderSpec(
    key=PROVIDER_QQ,
    title="QQ",
    schema_builder=_build_schema,
    validate_config=async_validate_config,
    setup_provider=async_setup_provider,
)
