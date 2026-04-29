"""DingTalk provider using Stream mode (no HTTP callback)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..command import execute_command, parse_command
from ..const import (
    CONF_DINGTALK_CLIENT_ID,
    CONF_DINGTALK_CLIENT_SECRET,
    PROVIDER_DINGTALK,
)
from ..known_targets import async_get_tracker
from ..models import ProviderRuntime
from .base import ProviderSpec

_LOGGER = logging.getLogger(__name__)
_OAUTH_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
_API_BASE = "https://api.dingtalk.com"
_OAPI_BASE = "https://oapi.dingtalk.com"


def _extract_stream_text(data: dict[str, Any]) -> str:
    msgtype = str(data.get("msgtype") or "text").strip().lower()
    if msgtype == "text":
        return str(((data.get("text") or {}).get("content") or "")).strip()
    if msgtype == "audio":
        content = data.get("content") if isinstance(data.get("content"), dict) else {}
        recognition = str(content.get("recognition") or (data.get("audio") or {}).get("recognition") or "").strip()
        return recognition
    return ""


def _extract_stream_sender_and_target(data: dict[str, Any]) -> tuple[str, str, str]:
    sender_id = str(data.get("senderStaffId") or data.get("sender_staff_id") or data.get("senderId") or data.get("sender_id") or "").strip()
    conversation_id = str(data.get("conversationId") or data.get("conversation_id") or "").strip()
    display_name = str(data.get("senderNick") or data.get("sender_nick") or sender_id or conversation_id).strip()
    if sender_id:
        return sender_id, "user", display_name
    return conversation_id or "group", "group", display_name


def _build_conversation_id(data: dict[str, Any]) -> str:
    sender_id = str(data.get("senderStaffId") or data.get("sender_staff_id") or data.get("senderId") or data.get("sender_id") or "").strip()
    conversation_id = str(data.get("conversationId") or data.get("conversation_id") or "").strip()
    if sender_id:
        return f"dingtalk:user:{sender_id}"
    if conversation_id:
        return f"dingtalk:group:{conversation_id}"
    return "dingtalk:stream"


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
        self._oapi_token = ""
        self._oapi_token_expire = 0.0

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

    async def send_image(self, target: str, image_bytes: bytes, target_type: str) -> None:
        token = await self._get_token()
        media_id = await self._upload_image(image_bytes)
        target = target.strip()
        if not target:
            raise ValueError("DingTalk target is required")

        msg_param = json.dumps({"photoURL": media_id}, ensure_ascii=False)

        if target_type == "user":
            path = "/v1.0/robot/oToMessages/batchSend"
            body = {
                "robotCode": self._client_id,
                "userIds": [target],
                "msgKey": "sampleImageMsg",
                "msgParam": msg_param,
            }
        else:
            path = "/v1.0/robot/groupMessages/send"
            body = {
                "robotCode": self._client_id,
                "openConversationId": target,
                "msgKey": "sampleImageMsg",
                "msgParam": msg_param,
            }

        async with self._session.post(
            f"{_API_BASE}{path}",
            headers={"x-acs-dingtalk-access-token": token},
            json=body,
            timeout=30,
        ) as resp:
            if resp.status >= 400:
                error_text = await resp.text()
                _LOGGER.error("DingTalk image send failed: %s, response: %s", resp.status, error_text)
                raise RuntimeError(f"DingTalk image send failed: {resp.status} {error_text}")

    async def _run_stream(self) -> None:
        """Use official Stream SDK if available, without webhook mode."""
        self._status = "connecting"
        try:
            import dingtalk_stream

            outer = self

            class _Handler(dingtalk_stream.ChatbotHandler):
                async def process(self, callback):
                    raw_data = callback.data if isinstance(callback.data, dict) else {}
                    incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                    text = _extract_stream_text(raw_data)
                    conversation_id = _build_conversation_id(raw_data)
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
                            conversation_id=conversation_id,
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

    async def _get_oapi_token(self) -> str:
        now = asyncio.get_running_loop().time()
        if self._oapi_token and now < self._oapi_token_expire - 300:
            return self._oapi_token

        async with self._session.get(
            f"{_OAPI_BASE}/gettoken",
            params={"appkey": self._client_id, "appsecret": self._client_secret},
            timeout=15,
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400 or int(data.get("errcode") or 0) != 0:
                raise RuntimeError(f"DingTalk OAPI token fetch failed: {resp.status} {data}")

        token = str(data.get("access_token") or "")
        if not token:
            raise RuntimeError(f"DingTalk OAPI access_token missing: {data}")
        self._oapi_token = token
        self._oapi_token_expire = now + int(data.get("expires_in") or 7200)
        return token

    async def _upload_image(self, image_bytes: bytes) -> str:
        if not image_bytes:
            raise ValueError("DingTalk image data is empty")
        token = await self._get_oapi_token()
        form = aiohttp.FormData()
        form.add_field("media", image_bytes, filename="camera.jpg", content_type="image/jpeg")
        async with self._session.post(
            f"{_OAPI_BASE}/media/upload",
            params={"access_token": token, "type": "image"},
            data=form,
            timeout=60,
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(f"DingTalk image upload failed: HTTP {resp.status} {data}")
            errcode = int(data.get("errcode") or 0)
            if errcode != 0:
                errmsg = data.get("errmsg", "Unknown error")
                raise RuntimeError(f"DingTalk image upload failed: errcode={errcode}, errmsg={errmsg}")
        media_id = str(data.get("media_id") or "")
        if not media_id:
            raise RuntimeError(f"DingTalk image upload missing media_id: {data}")
        return media_id


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
    tracker = await async_get_tracker(hass, subentry_id)

    original_run_stream = client._run_stream

    async def _run_stream_with_tracking() -> None:
        import dingtalk_stream

        outer = client

        class _TrackingHandler(dingtalk_stream.ChatbotHandler):
            async def process(self, callback):
                raw_data = callback.data if isinstance(callback.data, dict) else {}
                incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                target, target_type, display_name = _extract_stream_sender_and_target(raw_data)
                fut = asyncio.run_coroutine_threadsafe(
                    tracker.async_record(
                        provider=PROVIDER_DINGTALK,
                        target=target,
                        target_type=target_type,
                        display_name=display_name,
                    ),
                    outer._hass.loop,
                )
                try:
                    fut.result(timeout=10)
                except Exception as err:
                    _LOGGER.debug("DingTalk tracker record failed: %s", err)
                return await original_handler.process(callback)

        # fallback to original implementation if sdk unavailable
        try:
            credential = dingtalk_stream.Credential(outer._client_id, outer._client_secret)
            original_handler = None
            class _Handler(dingtalk_stream.ChatbotHandler):
                async def process(self, callback):
                    raw_data = callback.data if isinstance(callback.data, dict) else {}
                    incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                    text = _extract_stream_text(raw_data)
                    conversation_id = _build_conversation_id(raw_data)
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
                            conversation_id=conversation_id,
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
            original_handler = _Handler()
            tracking_handler = _TrackingHandler()
            tracking_handler.reply_text = original_handler.reply_text  # type: ignore[attr-defined]
            sdk_client = dingtalk_stream.DingTalkStreamClient(credential)
            sdk_client.register_callback_handler(dingtalk_stream.chatbot.ChatbotMessage.TOPIC, tracking_handler)
            outer._status = "connected"
            await asyncio.to_thread(sdk_client.start_forever)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning("DingTalk stream not started: %s", err)
            outer._status = "error"

    client._run_stream = _run_stream_with_tracking
    await client.start()

    async def _send(target: str, message: str, target_type: str) -> None:
        mode = target_type if target_type in ("group", "user") else "group"
        await client.send_text(target, message, mode)

    async def _send_image(target: str, image_bytes: bytes, target_type: str) -> None:
        mode = target_type if target_type in ("group", "user") else "group"
        await client.send_image(target, image_bytes, mode)

    return ProviderRuntime(
        key=PROVIDER_DINGTALK,
        title="DingTalk",
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
