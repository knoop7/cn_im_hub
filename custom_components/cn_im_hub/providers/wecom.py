"""WeCom provider implementation."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import uuid
from io import BytesIO
from typing import Any
from urllib.parse import urlencode

import aiohttp
import segno
import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..command import execute_command, parse_command
from ..const import CONF_WECOM_BOT_ID, CONF_WECOM_SECRET, PROVIDER_WECOM
from ..known_targets import async_get_tracker
from ..models import ProviderRuntime
from ..provider_flow import BaseProviderSubentryFlow
from .base import ProviderSpec

_LOGGER = logging.getLogger(__name__)

WS_URL = "wss://openws.work.weixin.qq.com"
CMD_SUBSCRIBE = "aibot_subscribe"
CMD_HEARTBEAT = "ping"
CMD_SEND_MSG = "aibot_send_msg"
CMD_RESPOND_MSG = "aibot_respond_msg"
CMD_RESPOND_WELCOME = "aibot_respond_welcome_msg"
CMD_MSG_CALLBACK = "aibot_msg_callback"
CMD_EVENT_CALLBACK = "aibot_event_callback"
CMD_UPLOAD_MEDIA_INIT = "aibot_upload_media_init"
CMD_UPLOAD_MEDIA_CHUNK = "aibot_upload_media_chunk"
CMD_UPLOAD_MEDIA_FINISH = "aibot_upload_media_finish"
EVENT_ENTER_CHAT = "enter_chat"
_UPLOAD_CHUNK_SIZE = 512 * 1024
_QR_GENERATE_URL = "https://work.weixin.qq.com/ai/qc/generate"
_QR_QUERY_URL = "https://work.weixin.qq.com/ai/qc/query_result"
_QR_CODE_PAGE = "https://work.weixin.qq.com/ai/qc/gen?source=wecom-cli&scode="


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
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

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
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
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

    async def send_image(self, target: str, image_bytes: bytes) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        if not image_bytes:
            raise ValueError("wecom image data is empty")
        media_id = await self._upload_media(image_bytes, media_type="image", filename="camera.jpg")
        payload = {
            "cmd": CMD_SEND_MSG,
            "headers": {"req_id": f"{CMD_SEND_MSG}_{uuid.uuid4().hex[:16]}"},
            "body": {"chatid": target, "msgtype": "image", "image": {"media_id": media_id}},
        }
        await self._send_with_reply(payload)

    async def _send_with_reply(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        req_id = str(payload.get("headers", {}).get("req_id") or "").strip()
        if not req_id:
            raise ValueError("req_id is required")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        try:
            await self._ws.send_json(payload)
            frame = await asyncio.wait_for(future, timeout=15)
        finally:
            self._pending.pop(req_id, None)
        if isinstance(frame.get("errcode"), int) and frame["errcode"] != 0:
            raise RuntimeError(f"wecom {payload.get('cmd')} failed: {frame.get('errcode')} {frame.get('errmsg')}")
        return frame

    async def _upload_media(self, file_bytes: bytes, *, media_type: str, filename: str) -> str:
        total_size = len(file_bytes)
        total_chunks = max(1, (total_size + _UPLOAD_CHUNK_SIZE - 1) // _UPLOAD_CHUNK_SIZE)
        init_frame = await self._send_with_reply(
            {
                "cmd": CMD_UPLOAD_MEDIA_INIT,
                "headers": {"req_id": f"{CMD_UPLOAD_MEDIA_INIT}_{uuid.uuid4().hex[:16]}"},
                "body": {
                    "type": media_type,
                    "filename": filename,
                    "total_size": total_size,
                    "total_chunks": total_chunks,
                    "md5": hashlib.md5(file_bytes).hexdigest(),
                },
            }
        )
        upload_id = str((init_frame.get("body") or {}).get("upload_id") or "")
        if not upload_id:
            raise ValueError("wecom upload init missing upload_id")

        for chunk_index in range(total_chunks):
            start = chunk_index * _UPLOAD_CHUNK_SIZE
            end = min(start + _UPLOAD_CHUNK_SIZE, total_size)
            chunk = file_bytes[start:end]
            await self._send_with_reply(
                {
                    "cmd": CMD_UPLOAD_MEDIA_CHUNK,
                    "headers": {"req_id": f"{CMD_UPLOAD_MEDIA_CHUNK}_{uuid.uuid4().hex[:16]}"},
                    "body": {
                        "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "base64_data": base64.b64encode(chunk).decode("ascii"),
                    },
                }
            )

        finish_frame = await self._send_with_reply(
            {
                "cmd": CMD_UPLOAD_MEDIA_FINISH,
                "headers": {"req_id": f"{CMD_UPLOAD_MEDIA_FINISH}_{uuid.uuid4().hex[:16]}"},
                "body": {"upload_id": upload_id},
            }
        )
        media_id = str((finish_frame.get("body") or {}).get("media_id") or "")
        if not media_id:
            raise ValueError("wecom upload finish missing media_id")
        return media_id

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
                        req_id = str(frame.get("headers", {}).get("req_id") or "").strip()
                        if req_id and req_id in self._pending:
                            future = self._pending[req_id]
                            if not future.done():
                                future.set_result(frame)
                            continue
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
    if body.get("msgtype") == "voice":
        content = str((body.get("voice") or {}).get("content") or "").strip()
        return content
    return str(body.get("content", "")).strip()


def _extract_reply_target(body: dict[str, Any]) -> str:
    sender = body.get("from", {})
    return sender.get("userid") or body.get("from_userid") or body.get("userid") or body.get("chatid") or "@all"


async def async_validate_config(_: HomeAssistant, config: dict[str, Any]) -> None:
    bot_id = str(config.get(CONF_WECOM_BOT_ID, "")).strip()
    secret = str(config.get(CONF_WECOM_SECRET, "")).strip()
    if not bot_id or not secret:
        raise ValueError("bot_id and secret are required")


async def _async_fetch_wecom_qr(hass: HomeAssistant) -> tuple[str, str]:
    session = async_get_clientsession(hass)
    params = {"source": "wecom-cli", "plat": 3}
    async with session.get(_QR_GENERATE_URL, params=params, timeout=15) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            raise RuntimeError(f"WeCom QR generate failed: {resp.status} {data}")
    payload = data.get("data") or {}
    scode = str(payload.get("scode") or "").strip()
    auth_url = str(payload.get("auth_url") or "").strip()
    if not scode or not auth_url:
        raise ValueError(f"WeCom QR generate missing fields: {data}")
    return scode, auth_url


async def _async_query_wecom_qr_result(hass: HomeAssistant, scode: str) -> dict[str, str] | None:
    session = async_get_clientsession(hass)
    url = f"{_QR_QUERY_URL}?{urlencode({'scode': scode})}"
    async with session.get(url, timeout=15) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            raise RuntimeError(f"WeCom QR query failed: {resp.status} {data}")
    payload = data.get("data") or {}
    if payload.get("status") != "success":
        return None
    bot_info = payload.get("bot_info") or {}
    bot_id = str(bot_info.get("botid") or "").strip()
    secret = str(bot_info.get("secret") or "").strip()
    if not bot_id or not secret:
        raise ValueError(f"WeCom QR success missing bot credentials: {data}")
    return {CONF_WECOM_BOT_ID: bot_id, CONF_WECOM_SECRET: secret}


def _build_qr_data_url(text: str) -> str:
    out = BytesIO()
    segno.make(text).save(out, kind="png", scale=6, border=2)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


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
    tracker = await async_get_tracker(hass, subentry_id)

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
        await tracker.async_record(
            provider=PROVIDER_WECOM,
            target=target,
            target_type="chatid",
            display_name=str(body.get("chat_name") or body.get("sender_name") or target),
        )
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

    async def _send_image(target: str, image_bytes: bytes, _: str) -> None:
        await client.send_image(target or "@all", image_bytes)

    return ProviderRuntime(
        key=PROVIDER_WECOM,
        title="WeCom",
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
            vol.Required(CONF_WECOM_BOT_ID, default=current.get(CONF_WECOM_BOT_ID, "")): str,
            vol.Required(CONF_WECOM_SECRET, default=current.get(CONF_WECOM_SECRET, "")): str,
        }
    )


class WeComProviderSubentryFlow(BaseProviderSubentryFlow):
    _provider_spec: ProviderSpec

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._current = {}
        return await self.async_step_setup_method(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._current = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_manual(user_input)

    async def async_step_setup_method(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return self.async_show_menu(
            step_id="setup_method",
            menu_options=["qr_prepare", "manual"],
        )

    async def async_step_qr_prepare(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        try:
            scode, auth_url = await _async_fetch_wecom_qr(self.hass)
        except Exception:  # noqa: BLE001
            return self.async_abort(reason="cannot_connect")
        self._current["wecom_scode"] = scode
        self._current["wecom_auth_url"] = auth_url
        self._current["wecom_qr_data_url"] = _build_qr_data_url(auth_url)
        return await self.async_step_qr_wait(None)

    async def async_step_qr_wait(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            scode = str(self._current.get("wecom_scode") or "").strip()
            if not scode:
                return await self.async_step_qr_prepare(None)
            try:
                data = await _async_query_wecom_qr_result(self.hass, scode)
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                if data is None:
                    errors["base"] = "auth_not_confirmed"
                else:
                    return await self._async_complete(data)

        scode = str(self._current.get("wecom_scode") or "")
        auth_url = str(self._current.get("wecom_auth_url") or "")
        description_placeholders = {
            "qr_url": auth_url,
            "qr_markdown": f"![WeCom QR]({self._current.get('wecom_qr_data_url', '')})"
            if self._current.get("wecom_qr_data_url")
            else (f"![WeCom QR]({_QR_CODE_PAGE}{scode})" if scode else ""),
        }
        return self.async_show_form(
            step_id="qr_wait",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {key: str(value).strip() for key, value in user_input.items()}
            try:
                await self._provider_spec.validate_config(self.hass, data)
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                return await self._async_complete(data)
            self._current.update(data)

        return self.async_show_form(
            step_id="manual",
            data_schema=self._provider_spec.schema_builder(self._current),
            errors=errors,
        )

    async def _async_complete(self, data: dict[str, str]) -> SubentryFlowResult:
        title = self._build_entry_title(data)
        if self._is_new:
            return self.async_create_entry(title=title, data=data)
        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            data=data,
        )

    @staticmethod
    def _build_entry_title(data: dict[str, str]) -> str:
        bot_id = str(data.get(CONF_WECOM_BOT_ID, "")).strip()
        if bot_id:
            return f"WeCom ({bot_id})"
        return "WeCom"


PROVIDER_SPEC = ProviderSpec(
    key=PROVIDER_WECOM,
    title="WeCom",
    schema_builder=_build_schema,
    validate_config=async_validate_config,
    setup_provider=async_setup_provider,
    flow_handler=WeComProviderSubentryFlow,
)
