"""WeChat OAuth helper for personal WeChat provider."""

from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import WECHAT_APP_ID, WECHAT_JPRX_GATEWAY, WECHAT_LOGIN_REDIRECT_URI

_DEFAULT_LOGIN_KEY = "m83qdao0AmE5"


def build_auth_url(state: str) -> str:
    return (
        "https://open.weixin.qq.com/connect/qrconnect"
        f"?appid={WECHAT_APP_ID}"
        f"&redirect_uri={WECHAT_LOGIN_REDIRECT_URI}"
        "&response_type=code"
        "&scope=snsapi_login"
        f"&state={state}#wechat_redirect"
    )


async def async_prepare_qr_login(hass: HomeAssistant, guid: str) -> dict[str, str]:
    """Prepare OAuth url and QR image url for scanning."""
    state = await async_get_login_state(hass, guid)
    auth_url = build_auth_url(state)
    session = async_get_clientsession(hass)
    async with session.get(
        auth_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )
        },
        timeout=20,
    ) as resp:
        html = await resp.text()

    match = re.search(r"/connect/qrcode/([a-zA-Z0-9_=-]+)", html)
    if not match:
        raise ValueError("Failed to extract QR uuid from WeChat auth page")
    uuid = match.group(1)
    qr_image_url = f"https://open.weixin.qq.com/connect/qrcode/{uuid}"

    return {
        "state": state,
        "auth_url": auth_url,
        "qr_uuid": uuid,
        "qr_image_url": qr_image_url,
    }


async def async_poll_qr_for_code(
    hass: HomeAssistant,
    *,
    qr_uuid: str,
    timeout_seconds: int = 120,
) -> str | None:
    """Poll WeChat QR login status until code is returned or timed out."""
    session = async_get_clientsession(hass)
    end_at = time.monotonic() + max(10, timeout_seconds)

    while time.monotonic() < end_at:
        poll_url = f"https://lp.open.weixin.qq.com/connect/l/qrconnect?uuid={qr_uuid}&_={int(time.time() * 1000)}"
        try:
            async with session.get(
                poll_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    )
                },
                timeout=35,
            ) as resp:
                text = await resp.text()
        except Exception:
            await asyncio.sleep(2)
            continue

        err_match = re.search(r"wx_errcode=(\d+)", text)
        code_match = re.search(r"wx_code='([^']*)'", text)
        err_code = int(err_match.group(1)) if err_match else 0
        wx_code = code_match.group(1).strip() if code_match else ""

        if err_code == 405 and wx_code:
            return wx_code
        if err_code in (402, 403):
            return None

        await asyncio.sleep(2)

    return None


def extract_code(value: str) -> str:
    text = value.strip().replace("\\?", "?").replace("\\=", "=").replace("\\&", "&")
    if "code=" in text:
        try:
            parsed = urlparse(text)
            code = parse_qs(parsed.query).get("code", [""])[0].strip()
            if code:
                return code
            fragment_code = parse_qs(parsed.fragment).get("code", [""])[0].strip()
            if fragment_code:
                return fragment_code
        except Exception:
            pass
        for segment in text.replace("?", "&").split("&"):
            if segment.startswith("code="):
                code = segment[5:].strip()
                if code:
                    return code
    return text


async def async_get_login_state(hass: HomeAssistant, guid: str) -> str:
    session = async_get_clientsession(hass)
    data = await _post_cmd(session, cmd_id="4050", guid=guid, payload={"guid": guid})
    state = _nested_get(data, "data", "resp", "data", "state") or _nested_get(data, "state")
    if isinstance(state, str) and state:
        return state
    return str(random.randint(1000, 99999))


async def async_exchange_code_for_channel_token(
    hass: HomeAssistant,
    *,
    guid: str,
    code: str,
    state: str,
) -> tuple[str, dict[str, Any]]:
    session = async_get_clientsession(hass)
    data = await _post_cmd(
        session,
        cmd_id="4026",
        guid=guid,
        payload={"guid": guid, "code": code, "state": state},
    )

    token = (
        _nested_get(data, "data", "resp", "data", "openclaw_channel_token")
        or _nested_get(data, "data", "openclaw_channel_token")
        or _nested_get(data, "openclaw_channel_token")
    )
    if not isinstance(token, str) or not token:
        raise ValueError("Unable to extract channel token from login response")

    user_info = _nested_get(data, "data", "resp", "data", "user_info")
    return token, user_info if isinstance(user_info, dict) else {}


async def _post_cmd(
    session: aiohttp.ClientSession,
    *,
    cmd_id: str,
    guid: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = f"{WECHAT_JPRX_GATEWAY}data/{cmd_id}/forward"
    headers = {
        "Content-Type": "application/json",
        "X-Version": "1",
        "X-Token": _DEFAULT_LOGIN_KEY,
        "X-Guid": guid,
        "X-Account": "1",
        "X-Session": "",
    }
    async with session.post(url, headers=headers, json=payload, timeout=20) as resp:
        data = await resp.json(content_type=None)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid login response type: {type(data).__name__}")
    common_code = (
        _nested_get(data, "data", "resp", "common", "code")
        or _nested_get(data, "data", "common", "code")
        or _nested_get(data, "resp", "common", "code")
        or _nested_get(data, "common", "code")
        or 0
    )
    ret = data.get("ret", 0)
    if ret == 0 or common_code == 0:
        return data
    message = (
        _nested_get(data, "data", "common", "message")
        or _nested_get(data, "resp", "common", "message")
        or _nested_get(data, "common", "message")
        or "request failed"
    )
    raise ValueError(f"Wechat auth request failed: {message}")


def _nested_get(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
