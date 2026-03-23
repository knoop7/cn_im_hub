"""Tencent Weixin OpenClaw auth and API helpers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from uuid import uuid4

import aiohttp
import segno
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import WECHAT_DEFAULT_BASE_URL

_QR_LONG_POLL_TIMEOUT_MS = 35_000
_DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
_DEFAULT_API_TIMEOUT_MS = 15_000
_DEFAULT_ILINK_BOT_TYPE = "3"


@dataclass(slots=True)
class WeixinLoginSession:
    session_key: str
    qrcode: str
    qrcode_url: str
    qrcode_data_url: str


@dataclass(slots=True)
class WeixinLoginResult:
    connected: bool
    message: str
    token: str = ""
    account_id: str = ""
    base_url: str = ""
    user_id: str = ""


def _random_wechat_uin() -> str:
    digest = hashlib.sha256(uuid4().bytes).digest()[:4]
    value = int.from_bytes(digest, "big", signed=False)
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _build_headers(body: str, token: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": _random_wechat_uin(),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _api_post(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    token: str | None = None,
    timeout_ms: int = _DEFAULT_API_TIMEOUT_MS,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    body = json.dumps(payload, ensure_ascii=False)
    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    async with session.post(url, data=body.encode("utf-8"), headers=_build_headers(body, token), timeout=timeout) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"{endpoint} {resp.status}: {raw}")
    data = json.loads(raw) if raw else {}
    if not isinstance(data, dict):
        raise ValueError(f"invalid {endpoint} response")
    return data


async def async_start_weixin_login(
    hass: HomeAssistant,
    *,
    base_url: str = WECHAT_DEFAULT_BASE_URL,
    session_key: str | None = None,
) -> WeixinLoginSession:
    session = async_get_clientsession(hass)
    url = f"{base_url.rstrip('/')}/ilink/bot/get_bot_qrcode?bot_type={_DEFAULT_ILINK_BOT_TYPE}"
    timeout = aiohttp.ClientTimeout(total=_DEFAULT_API_TIMEOUT_MS / 1000)
    async with session.get(url, timeout=timeout) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"get_bot_qrcode {resp.status}: {raw}")
    data = json.loads(raw) if raw else {}
    qrcode = str(data.get("qrcode") or "")
    qrcode_url = str(data.get("qrcode_img_content") or "")
    if not qrcode or not qrcode_url:
        raise ValueError("failed to fetch login QR code")
    return WeixinLoginSession(
        session_key=session_key or uuid4().hex,
        qrcode=qrcode,
        qrcode_url=qrcode_url,
        qrcode_data_url=build_qr_data_url(qrcode_url),
    )


async def async_wait_weixin_login(
    hass: HomeAssistant,
    *,
    login: WeixinLoginSession,
    base_url: str = WECHAT_DEFAULT_BASE_URL,
    timeout_ms: int = 480_000,
) -> WeixinLoginResult:
    session = async_get_clientsession(hass)
    deadline = asyncio.get_running_loop().time() + max(timeout_ms / 1000, 1)
    while asyncio.get_running_loop().time() < deadline:
        url = f"{base_url.rstrip('/')}/ilink/bot/get_qrcode_status?qrcode={login.qrcode}"
        timeout = aiohttp.ClientTimeout(total=_QR_LONG_POLL_TIMEOUT_MS / 1000)
        try:
            async with session.get(url, headers={"iLink-App-ClientVersion": "1"}, timeout=timeout) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"get_qrcode_status {resp.status}: {raw}")
            data = json.loads(raw) if raw else {}
        except TimeoutError:
            data = {"status": "wait"}
        status = str(data.get("status") or "wait")
        if status == "confirmed":
            account_id = str(data.get("ilink_bot_id") or "")
            token = str(data.get("bot_token") or "")
            user_id = str(data.get("ilink_user_id") or "")
            resolved_base_url = str(data.get("baseurl") or base_url)
            if not account_id or not token:
                raise ValueError("login confirmed but token/account id missing")
            return WeixinLoginResult(
                connected=True,
                message="与微信连接成功",
                token=token,
                account_id=account_id,
                base_url=resolved_base_url,
                user_id=user_id,
            )
        if status == "expired":
            raise ValueError("wechat login QR expired")
        await asyncio.sleep(2)
    raise TimeoutError("wechat login timeout")


async def async_get_updates(
    hass: HomeAssistant,
    *,
    base_url: str,
    token: str,
    get_updates_buf: str,
    timeout_ms: int = _DEFAULT_LONG_POLL_TIMEOUT_MS,
) -> dict[str, Any]:
    session = async_get_clientsession(hass)
    try:
        return await _api_post(
            session,
            base_url=base_url,
            endpoint="ilink/bot/getupdates",
            payload={"get_updates_buf": get_updates_buf, "base_info": {"channel_version": "ha-cn-im-hub"}},
            token=token,
            timeout_ms=timeout_ms,
        )
    except TimeoutError:
        return {"ret": 0, "msgs": [], "get_updates_buf": get_updates_buf}


async def async_send_weixin_text(
    hass: HomeAssistant,
    *,
    base_url: str,
    token: str,
    to_user_id: str,
    context_token: str,
    text: str,
) -> str:
    session = async_get_clientsession(hass)
    client_id = f"cn_im_hub_{uuid4().hex}"
    await _api_post(
        session,
        base_url=base_url,
        endpoint="ilink/bot/sendmessage",
        payload={
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": context_token,
            },
            "base_info": {"channel_version": "ha-cn-im-hub"},
        },
        token=token,
    )
    return client_id


def extract_text_body(message: dict[str, Any]) -> str:
    item_list = message.get("item_list") or []
    if not isinstance(item_list, list):
        return ""
    for item in item_list:
        if not isinstance(item, dict):
            continue
        if item.get("type") == 1:
            text_item = item.get("text_item") or {}
            text = text_item.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        if item.get("type") == 3:
            voice_item = item.get("voice_item") or {}
            text = voice_item.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def build_qr_data_url(text: str) -> str:
    out = BytesIO()
    segno.make(text).save(out, kind="png", scale=6, border=2)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")
