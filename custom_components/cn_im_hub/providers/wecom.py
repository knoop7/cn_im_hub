"""WeCom provider implementation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..command import execute_command, parse_command
from ..const import CONF_WECOM_BOT_ID, CONF_WECOM_SECRET, PROVIDER_WECOM
from ..models import ProviderRuntime

_LOGGER = logging.getLogger(__name__)

WS_URL = "wss://openws.work.weixin.qq.com"
CMD_SUBSCRIBE = "aibot_subscribe"
CMD_HEARTBEAT = "ping"
CMD_SEND_MSG = "aibot_send_msg"
CMD_RESPOND_MSG = "aibot_respond_msg"
CMD_RESPOND_WELCOME = "aibot_respond_welcome_msg"
CMD_MSG_CALLBACK = "aibot_msg_callback"
CMD_EVENT_CALLBACK = "aibot_event_callback"
EVENT_ENTER_CHAT = "enter_chat"


class WeComWsClient:
    def __init__(self, hass: HomeAssistant, bot_id: str, secret: str) -> None:
        self.hass = hass
        self.bot_id = bot_id
        self.secret = secret
        self._session = async_get_clientsession(hass)
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._running = False
        self._authenticated = False
        self._callback: Any = None

    @property
    def status(self) -> str:
        if self._authenticated:
            return "authenticated"
        if self._ws is not None and not self._ws.closed:
            return "connected"
        return "disconnected"

    def set_message_callback(self, callback: Any) -> None:
        self._callback = callback

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._runner_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._runner_task:
            self._runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner_task
            self._runner_task = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self._authenticated = False

    async def send_markdown(self, target: str, message: str) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        payload = {
            "cmd": CMD_SEND_MSG,
            "headers": {"req_id": f"{CMD_SEND_MSG}_{uuid.uuid4().hex[:16]}"},
            "body": {"chatid": target, "msgtype": "markdown", "markdown": {"content": message}},
        }
        await self._ws.send_json(payload)

    async def reply_markdown(self, callback_req_id: str, message: str) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        payload = {
            "cmd": CMD_RESPOND_MSG,
            "headers": {"req_id": callback_req_id},
            "body": {"msgtype": "markdown", "markdown": {"content": message}},
        }
        await self._ws.send_json(payload)

    async def reply_welcome(self, callback_req_id: str, message: str) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        payload = {
            "cmd": CMD_RESPOND_WELCOME,
            "headers": {"req_id": callback_req_id},
            "body": {"msgtype": "markdown", "markdown": {"content": message}},
        }
        await self._ws.send_json(payload)

    async def reply_via_response_url(self, response_url: str, message: str) -> None:
        payload = {"msgtype": "markdown", "markdown": {"content": message}}
        async with self._session.post(response_url, json=payload, timeout=15) as resp:
            _ = await resp.text()

    async def _run(self) -> None:
        while self._running:
            try:
                self._ws = await self._session.ws_connect(WS_URL, heartbeat=60)
                await self._ws.send_json(
                    {
                        "cmd": CMD_SUBSCRIBE,
                        "headers": {"req_id": f"{CMD_SUBSCRIBE}_{uuid.uuid4().hex[:16]}"},
                        "body": {"bot_id": self.bot_id, "secret": self.secret},
                    }
                )
                self._authenticated = True
                while self._running and self._ws and not self._ws.closed:
                    msg = await self._ws.receive()
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        frame = json.loads(msg.data)
                        if self._callback:
                            await self._callback(frame)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                        break
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("WeCom websocket loop error: %s", err)
            finally:
                self._authenticated = False
                if self._ws and not self._ws.closed:
                    await self._ws.close()
                self._ws = None
            if self._running:
                await asyncio.sleep(3)


def _extract_text(body: dict[str, Any]) -> str:
    if body.get("msgtype") == "text":
        return body.get("text", {}).get("content", "").strip()
    return str(body.get("content", "")).strip()


def _extract_reply_target(body: dict[str, Any]) -> str:
    sender = body.get("from", {})
    return sender.get("userid") or body.get("from_userid") or body.get("userid") or body.get("chatid") or "@all"


async def async_validate_config(_: HomeAssistant, config: dict[str, Any]) -> None:
    bot_id = str(config.get(CONF_WECOM_BOT_ID, "")).strip()
    secret = str(config.get(CONF_WECOM_SECRET, "")).strip()
    if not bot_id or not secret:
        raise ValueError("bot_id and secret are required")


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    bot_id = str(config.get(CONF_WECOM_BOT_ID, "")).strip()
    secret = str(config.get(CONF_WECOM_SECRET, "")).strip()
    client = WeComWsClient(hass, bot_id, secret)

    async def _handle_inbound(frame: dict[str, Any]) -> None:
        cmd = frame.get("cmd")
        if cmd not in (CMD_MSG_CALLBACK, CMD_EVENT_CALLBACK):
            return
        callback_req_id = frame.get("headers", {}).get("req_id", "")
        body = frame.get("body", {})
        response_url = body.get("response_url", "")

        if cmd == CMD_EVENT_CALLBACK:
            event_type = body.get("event", {}).get("eventtype") or body.get("eventtype")
            if event_type == EVENT_ENTER_CHAT and callback_req_id:
                with contextlib.suppress(Exception):
                    await client.reply_welcome(callback_req_id, "已连接 Home Assistant，你可以直接发送问题或控制指令。")
            return

        text = _extract_text(body)
        if not text:
            return

        target = _extract_reply_target(body)
        try:
            command = parse_command(text)
        except ValueError as err:
            reply = f"Invalid command: {err}"
        else:
            if command is None:
                return
            reply = await execute_command(
                hass,
                command,
                conversation_id=f"wecom:{target}",
                agent_id=agent_id,
            )

        if response_url:
            with contextlib.suppress(Exception):
                await client.reply_via_response_url(response_url, reply)
                return
        if callback_req_id:
            with contextlib.suppress(Exception):
                await client.reply_markdown(callback_req_id, reply)
                return
        with contextlib.suppress(Exception):
            await client.send_markdown(target, reply)

    client.set_message_callback(_handle_inbound)
    await client.start()

    async def _send(target: str, message: str, _: str) -> None:
        await client.send_markdown(target or "@all", message)

    return ProviderRuntime(
        key=PROVIDER_WECOM,
        title="WeCom",
        subentry_id=subentry_id,
        client=client,
        stop=client.stop,
        send_text=_send,
        status=lambda: client.status,
    )
