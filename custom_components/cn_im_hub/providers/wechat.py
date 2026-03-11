"""WeChat (personal) provider via agentwsserver websocket."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from uuid import uuid4

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..command import execute_command, parse_command
from ..const import CONF_WECHAT_TOKEN, CONF_WECHAT_WS_URL, PROVIDER_WECHAT
from ..models import ProviderRuntime

_LOGGER = logging.getLogger(__name__)


class WeChatWsClient:
    """AGP websocket client for WeChat access gateway."""

    def __init__(self, hass: HomeAssistant, ws_url: str, token: str, agent_id: str) -> None:
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._ws_url = ws_url
        self._token = token
        self._agent_id = agent_id
        self._task: asyncio.Task[None] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._status = "disconnected"
        self._active: dict[str, asyncio.Task[str]] = {}

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
        for task in self._active.values():
            task.cancel()
        self._active.clear()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self._status = "disconnected"

    async def send_text(self, _: str, __: str, ___: str) -> None:
        raise RuntimeError("WeChat provider does not support direct send_message API")

    async def _run(self) -> None:
        while True:
            self._status = "connecting"
            try:
                url = self._build_url(self._ws_url, self._token)
                self._ws = await self._session.ws_connect(url, heartbeat=30)
                self._status = "connected"
                async for msg in self._ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    payload = json.loads(msg.data)
                    await self._handle_inbound(payload)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("WeChat websocket loop error: %s", err)
                self._status = "error"
            finally:
                if self._ws and not self._ws.closed:
                    await self._ws.close()
                self._ws = None
                for task in self._active.values():
                    task.cancel()
                self._active.clear()
                if self._status != "error":
                    self._status = "disconnected"
            await asyncio.sleep(3)

    async def _handle_inbound(self, envelope: dict) -> None:
        method = str(envelope.get("method") or "")
        payload = envelope.get("payload") or {}
        prompt_id = str(payload.get("prompt_id") or "")

        if method == "session.cancel":
            task = self._active.pop(prompt_id, None)
            if task:
                task.cancel()
            await self._send_prompt_response(
                envelope,
                payload,
                stop_reason="cancelled",
                text="",
            )
            return

        if method != "session.prompt":
            return

        content = payload.get("content") or []
        text = _extract_text(content)
        if not text:
            await self._send_prompt_response(
                envelope,
                payload,
                stop_reason="end_turn",
                text="",
            )
            return

        task = asyncio.create_task(self._process_prompt(text, envelope))
        self._active[prompt_id] = task
        task.add_done_callback(lambda _: self._active.pop(prompt_id, None))

    async def _process_prompt(self, text: str, envelope: dict) -> str:
        payload = envelope.get("payload") or {}
        try:
            command = parse_command(text)
            if command is None:
                reply = ""
            else:
                reply = await execute_command(
                    self._hass,
                    command,
                    conversation_id=f"wechat:{payload.get('session_id', '')}",
                    agent_id=self._agent_id,
                )
            await self._send_prompt_response(envelope, payload, stop_reason="end_turn", text=reply)
            return reply
        except asyncio.CancelledError:
            await self._send_prompt_response(envelope, payload, stop_reason="cancelled", text="")
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("WeChat prompt handling failed: %s", err)
            await self._send_prompt_response(
                envelope,
                payload,
                stop_reason="error",
                text="",
                error=f"{type(err).__name__}: {err}",
            )
            return ""

    async def _send_prompt_response(
        self,
        envelope: dict,
        payload: dict,
        *,
        stop_reason: str,
        text: str,
        error: str | None = None,
    ) -> None:
        if not self._ws or self._ws.closed:
            return
        response_payload: dict = {
            "session_id": str(payload.get("session_id") or ""),
            "prompt_id": str(payload.get("prompt_id") or ""),
            "stop_reason": stop_reason,
        }
        if text:
            response_payload["content"] = [{"type": "text", "text": text}]
        if error:
            response_payload["error"] = error

        resp = {
            "msg_id": str(uuid4()),
            "guid": envelope.get("guid") or "",
            "user_id": envelope.get("user_id") or "",
            "method": "session.promptResponse",
            "payload": response_payload,
        }
        await self._ws.send_json(resp)

    @staticmethod
    def _build_url(ws_url: str, token: str) -> str:
        url = ws_url.strip()
        if "?" in url:
            return f"{url}&token={token}"
        return f"{url}?token={token}"


async def async_validate_config(hass: HomeAssistant, config: dict[str, str]) -> None:
    ws_url = str(config.get(CONF_WECHAT_WS_URL, "")).strip()
    token = str(config.get(CONF_WECHAT_TOKEN, "")).strip()
    if not ws_url or not token:
        raise ValueError("wechat_ws_url and wechat_token are required")

    session = async_get_clientsession(hass)
    test_url = WeChatWsClient._build_url(ws_url, token)
    try:
        ws = await session.ws_connect(test_url, timeout=8)
        await ws.close()
    except Exception as err:  # noqa: BLE001
        raise ValueError(f"websocket auth/connect failed: {err}") from err


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, str],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    ws_url = str(config.get(CONF_WECHAT_WS_URL, "")).strip()
    token = str(config.get(CONF_WECHAT_TOKEN, "")).strip()
    client = WeChatWsClient(hass, ws_url, token, agent_id)
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


def _extract_text(content: list[dict]) -> str:
    chunks = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            chunks.append(str(block.get("text") or ""))
    return "\n".join(chunks).strip()
