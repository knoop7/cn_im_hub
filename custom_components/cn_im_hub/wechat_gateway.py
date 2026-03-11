"""Internal AGP websocket gateway for WeChat personal service."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .command import execute_command, parse_command
from .const import DOMAIN


@dataclass(slots=True)
class WeChatBinding:
    """Runtime binding for one WeChat subentry."""

    subentry_id: str
    token: str
    agent_id: str
    connections: set[web.WebSocketResponse] = field(default_factory=set)


class WeChatGatewayManager:
    """Manage WeChat AGP websocket endpoint and bindings."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._bindings_by_token: dict[str, WeChatBinding] = {}
        self._bindings_by_subentry: dict[str, WeChatBinding] = {}
        self._registered = False

    def async_register_view(self) -> None:
        if self._registered:
            return
        self.hass.http.register_view(WeChatGatewayView(self))
        self._registered = True

    def register_binding(self, *, subentry_id: str, token: str, agent_id: str) -> None:
        existing = self._bindings_by_subentry.pop(subentry_id, None)
        if existing is not None:
            self._bindings_by_token.pop(existing.token, None)

        binding = WeChatBinding(subentry_id=subentry_id, token=token, agent_id=agent_id)
        self._bindings_by_subentry[subentry_id] = binding
        self._bindings_by_token[token] = binding

    def unregister_binding(self, subentry_id: str) -> None:
        binding = self._bindings_by_subentry.pop(subentry_id, None)
        if binding is None:
            return
        self._bindings_by_token.pop(binding.token, None)

    def get_binding(self, token: str) -> WeChatBinding | None:
        return self._bindings_by_token.get(token)

    def get_status(self, subentry_id: str) -> str:
        binding = self._bindings_by_subentry.get(subentry_id)
        if binding is None:
            return "disconnected"
        return "connected" if binding.connections else "ready"


class WeChatGatewayView(HomeAssistantView):
    """Websocket endpoint for AGP messages from personal WeChat bridge."""

    url = "/api/cn_im_hub/wechat/ws"
    name = "api:cn_im_hub:wechat_ws"
    requires_auth = False

    def __init__(self, manager: WeChatGatewayManager) -> None:
        self._manager = manager

    async def get(self, request: web.Request) -> web.StreamResponse:
        token = str(request.query.get("token", "")).strip()
        binding = self._manager.get_binding(token)
        if binding is None:
            return web.Response(status=401, text="invalid token")

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        binding.connections.add(ws)
        active: dict[str, asyncio.Task[None]] = {}

        try:
            async for msg in ws:
                if msg.type != web.WSMsgType.TEXT:
                    continue
                try:
                    envelope = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue

                method = str(envelope.get("method") or "")
                payload = envelope.get("payload") or {}
                prompt_id = str(payload.get("prompt_id") or "")

                if method == "session.cancel":
                    task = active.pop(prompt_id, None)
                    if task:
                        task.cancel()
                    await _send_prompt_response(
                        ws,
                        envelope,
                        payload,
                        stop_reason="cancelled",
                        text="",
                    )
                    continue

                if method != "session.prompt":
                    continue

                task = asyncio.create_task(
                    _handle_prompt(
                        self._manager.hass,
                        ws,
                        binding.agent_id,
                        envelope,
                    )
                )
                if prompt_id:
                    active[prompt_id] = task
                    task.add_done_callback(lambda _: active.pop(prompt_id, None))
        finally:
            for task in active.values():
                task.cancel()
            binding.connections.discard(ws)

        return ws


async def _handle_prompt(
    hass: HomeAssistant,
    ws: web.WebSocketResponse,
    agent_id: str,
    envelope: dict[str, Any],
) -> None:
    payload = envelope.get("payload") or {}
    text = _extract_text(payload.get("content") or [])
    try:
        command = parse_command(text)
        if command is None:
            reply = ""
        else:
            reply = await execute_command(
                hass,
                command,
                conversation_id=f"wechat:{payload.get('session_id', '')}",
                agent_id=agent_id,
            )
        await _send_prompt_response(
            ws,
            envelope,
            payload,
            stop_reason="end_turn",
            text=reply,
        )
    except asyncio.CancelledError:
        await _send_prompt_response(
            ws,
            envelope,
            payload,
            stop_reason="cancelled",
            text="",
        )
        raise
    except Exception as err:  # noqa: BLE001
        await _send_prompt_response(
            ws,
            envelope,
            payload,
            stop_reason="error",
            text="",
            error=f"{type(err).__name__}: {err}",
        )


async def _send_prompt_response(
    ws: web.WebSocketResponse,
    envelope: dict[str, Any],
    payload: dict[str, Any],
    *,
    stop_reason: str,
    text: str,
    error: str | None = None,
) -> None:
    if ws.closed:
        return
    resp_payload: dict[str, Any] = {
        "session_id": str(payload.get("session_id") or ""),
        "prompt_id": str(payload.get("prompt_id") or ""),
        "stop_reason": stop_reason,
    }
    if text:
        resp_payload["content"] = [{"type": "text", "text": text}]
    if error:
        resp_payload["error"] = error

    resp = {
        "msg_id": str(uuid4()),
        "guid": envelope.get("guid") or "",
        "user_id": envelope.get("user_id") or "",
        "method": "session.promptResponse",
        "payload": resp_payload,
    }
    await ws.send_json(resp)


def _extract_text(content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts).strip()


def get_wechat_gateway_manager(hass: HomeAssistant) -> WeChatGatewayManager:
    data = hass.data.setdefault(DOMAIN, {})
    manager = data.get("wechat_gateway")
    if isinstance(manager, WeChatGatewayManager):
        return manager
    manager = WeChatGatewayManager(hass)
    manager.async_register_view()
    data["wechat_gateway"] = manager
    return manager
