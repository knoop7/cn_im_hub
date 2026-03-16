"""WeChat OAuth helper for personal WeChat provider."""

from __future__ import annotations

import asyncio
import base64
import random
import re
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp
import segno
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import WECHAT_APP_ID, WECHAT_DEFAULT_WS_URL, WECHAT_JPRX_GATEWAY, WECHAT_LOGIN_REDIRECT_URI

_DEFAULT_LOGIN_KEY = "m83qdao0AmE5"
_DEFAULT_OPEN_KFID = "wkzLlJLAAAfbxEV3ZcS-lHZxkaKmpejQ"


class TokenExpiredError(Exception):
    """Token expired from qclaw api."""


@dataclass(slots=True)
class WeChatLoginContext:
    channel_token: str
    jwt_token: str
    user_id: str
    login_key: str
    api_key: str = ""
    ws_url: str = WECHAT_DEFAULT_WS_URL


@dataclass(slots=True)
class DeviceBindResult:
    success: bool
    message: str


class QClawAPI:
    """QClaw JPRX API wrapper."""

    def __init__(self, session: aiohttp.ClientSession, guid: str, jwt_token: str = "") -> None:
        self._session = session
        self._guid = guid
        self.login_key = _DEFAULT_LOGIN_KEY
        self.jwt_token = jwt_token
        self.user_id = ""

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Version": "1",
            "X-Token": self.login_key,
            "X-Guid": self._guid,
            "X-Account": self.user_id or "1",
            "X-Session": "",
        }
        if self.jwt_token:
            headers["X-OpenClaw-Token"] = self.jwt_token
        return headers

    async def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {**(body or {}), "web_version": "1.4.0", "web_env": "release"}
        async with self._session.post(
            f"{WECHAT_JPRX_GATEWAY}{path}",
            headers=self._headers(),
            json=payload,
            timeout=30,
        ) as resp:
            new_token = resp.headers.get("X-New-Token")
            if new_token:
                self.jwt_token = new_token
            data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            raise ValueError("invalid qclaw response")

        common_code = (
            nested(data, "data", "resp", "common", "code")
            or nested(data, "data", "common", "code")
            or nested(data, "resp", "common", "code")
            or nested(data, "common", "code")
            or 0
        )
        if common_code == 21004:
            raise TokenExpiredError("login expired")

        ret = data.get("ret", 0)
        if ret == 0 or common_code == 0:
            return data

        message = (
            nested(data, "data", "common", "message")
            or nested(data, "resp", "common", "message")
            or nested(data, "common", "message")
            or "request failed"
        )
        raise ValueError(str(message))

    async def get_wx_login_state(self) -> dict[str, Any]:
        return await self._post("data/4050/forward", {"guid": self._guid})

    async def wx_login(self, code: str, state: str) -> dict[str, Any]:
        return await self._post("data/4026/forward", {"guid": self._guid, "code": code, "state": state})

    async def create_api_key(self) -> dict[str, Any]:
        return await self._post("data/4055/forward", {})

    async def check_invite_code(self, user_id: str) -> dict[str, Any]:
        return await self._post("data/4056/forward", {"user_id": user_id})

    async def submit_invite_code(self, user_id: str, code: str) -> dict[str, Any]:
        return await self._post("data/4057/forward", {"user_id": user_id, "code": code})

    async def refresh_channel_token(self) -> str | None:
        data = await self._post("data/4058/forward", {})
        return first_not_none(
            nested(data, "data", "resp", "data", "openclaw_channel_token"),
            nested(data, "data", "openclaw_channel_token"),
            nested(data, "openclaw_channel_token"),
        )

    async def generate_contact_link(self, open_kf_id: str = _DEFAULT_OPEN_KFID) -> dict[str, Any]:
        return await self._post(
            "data/4018/forward",
            {
                "guid": self._guid,
                "user_id": int(self.user_id or 0),
                "open_id": open_kf_id,
                "contact_type": "open_kfid",
            },
        )

    async def query_device_by_guid(self) -> dict[str, Any]:
        return await self._post("data/4019/forward", {"guid": self._guid})


def build_auth_url(state: str) -> str:
    params = urlencode(
        {
            "appid": WECHAT_APP_ID,
            "redirect_uri": WECHAT_LOGIN_REDIRECT_URI,
            "response_type": "code",
            "scope": "snsapi_login",
            "state": state,
        }
    )
    return f"https://open.weixin.qq.com/connect/qrconnect?{params}#wechat_redirect"


def build_qr_data_url(text: str) -> str:
    out = BytesIO()
    segno.make(text).save(out, kind="png", scale=6, border=2)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


async def async_fetch_qr_uuid(hass: HomeAssistant, auth_url: str) -> str:
    session = async_get_clientsession(hass)
    async with session.get(auth_url, timeout=20) as resp:
        html = await resp.text()
    match = re.search(r"/connect/qrcode/([a-zA-Z0-9_=-]+)", html)
    if not match:
        raise ValueError("failed to extract qr uuid")
    return match.group(1)


async def async_fetch_qr_image_data_url(hass: HomeAssistant, qr_uuid: str) -> str:
    session = async_get_clientsession(hass)
    qr_image_url = f"https://open.weixin.qq.com/connect/qrcode/{qr_uuid}"
    async with session.get(qr_image_url, timeout=20) as resp:
        image_bytes = await resp.read()
    return "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")


async def async_prepare_qr_login(hass: HomeAssistant, guid: str) -> dict[str, str]:
    state = await async_get_login_state(hass, guid)
    auth_url = build_auth_url(state)
    uuid = await async_fetch_qr_uuid(hass, auth_url)
    qr_image_url = f"https://open.weixin.qq.com/connect/qrcode/{uuid}"
    qr_data_url = await async_fetch_qr_image_data_url(hass, uuid)
    return {
        "state": state,
        "auth_url": auth_url,
        "qr_uuid": uuid,
        "qr_image_url": qr_image_url,
        "qr_data_url": qr_data_url,
    }


async def async_poll_qr_for_code(hass: HomeAssistant, *, qr_uuid: str, timeout_seconds: int = 180) -> str:
    session = async_get_clientsession(hass)
    end_at = time.monotonic() + timeout_seconds
    while time.monotonic() < end_at:
        poll_url = f"https://lp.open.weixin.qq.com/connect/l/qrconnect?uuid={qr_uuid}&_={int(time.time() * 1000)}"
        async with session.get(poll_url, timeout=35) as resp:
            text = await resp.text()
        err_match = re.search(r"wx_errcode=(\d+)", text)
        code_match = re.search(r"wx_code='([^']*)'", text)
        err_code = int(err_match.group(1)) if err_match else 0
        wx_code = code_match.group(1).strip() if code_match else ""
        if err_code == 405 and wx_code:
            return wx_code
        if err_code in (402, 403):
            raise ValueError("wechat login expired")
        await asyncio.sleep(2)
    raise TimeoutError("wechat login timeout")


def extract_code(value: str) -> str:
    text = value.strip().replace("\\?", "?").replace("\\=", "=").replace("\\&", "&")
    if "code=" not in text:
        return text
    parsed = urlparse(text)
    from_query = parse_qs(parsed.query).get("code", [""])[0].strip()
    if from_query:
        return from_query
    from_fragment = parse_qs(parsed.fragment).get("code", [""])[0].strip()
    if from_fragment:
        return from_fragment
    for segment in text.replace("?", "&").split("&"):
        if segment.startswith("code="):
            code = segment[5:].strip()
            if code:
                return code
    return text


async def async_get_login_state(hass: HomeAssistant, guid: str) -> str:
    session = async_get_clientsession(hass)
    api = QClawAPI(session, guid)
    result = await api.get_wx_login_state()
    state = first_not_none(nested(result, "data", "resp", "data", "state"), nested(result, "state"))
    if isinstance(state, str) and state:
        return state
    return str(random.randint(1000, 99999))


async def async_exchange_code_for_channel_token(
    hass: HomeAssistant,
    *,
    guid: str,
    code: str,
    state: str,
) -> WeChatLoginContext:
    session = async_get_clientsession(hass)
    api = QClawAPI(session, guid)
    result = await api.wx_login(code, state)
    login_data = first_not_none(nested(result, "data", "resp", "data"), nested(result, "data"), result)
    if not isinstance(login_data, dict):
        raise ValueError("invalid wechat login response")

    channel_token = str(first_not_none(login_data.get("openclaw_channel_token"), nested(login_data, "data", "openclaw_channel_token")) or "")
    jwt_token = str(first_not_none(login_data.get("token"), nested(login_data, "jwt_token")) or "")
    user_info = login_data.get("user_info") if isinstance(login_data.get("user_info"), dict) else {}
    user_id = str(first_not_none(user_info.get("user_id"), login_data.get("user_id")) or "")
    login_key = str(first_not_none(user_info.get("loginKey"), user_info.get("login_key"), _DEFAULT_LOGIN_KEY))
    if not channel_token:
        raise ValueError("unable to extract channel token")

    api.jwt_token = jwt_token
    api.user_id = user_id
    api.login_key = login_key

    api_key = ""
    try:
        key_result = await api.create_api_key()
        api_key = str(first_not_none(nested(key_result, "data", "resp", "data", "key"), nested(key_result, "data", "key"), nested(key_result, "key")) or "")
    except Exception:
        api_key = ""

    return WeChatLoginContext(
        channel_token=channel_token,
        jwt_token=api.jwt_token,
        user_id=user_id,
        login_key=login_key,
        api_key=api_key,
        ws_url=WECHAT_DEFAULT_WS_URL,
    )


async def async_generate_contact_link(hass: HomeAssistant, *, guid: str, login: WeChatLoginContext) -> str:
    session = async_get_clientsession(hass)
    api = QClawAPI(session, guid, login.jwt_token)
    api.user_id = login.user_id
    api.login_key = login.login_key
    link_result = await api.generate_contact_link()
    login.jwt_token = api.jwt_token
    data = first_not_none(nested(link_result, "data", "resp", "data"), nested(link_result, "data"), link_result)
    if not isinstance(data, dict):
        raise ValueError("invalid contact link response")
    contact_url = first_not_none(data.get("url"), nested(data, "contact_url"), nested(data, "redirect_url"))
    if not contact_url:
        raise ValueError("service did not return contact url")
    return str(contact_url)


async def async_perform_device_binding(
    hass: HomeAssistant,
    *,
    guid: str,
    login: WeChatLoginContext,
    contact_url: str | None = None,
    timeout_seconds: int = 300,
) -> DeviceBindResult:
    if not contact_url:
        contact_url = await async_generate_contact_link(hass, guid=guid, login=login)
    session = async_get_clientsession(hass)
    api = QClawAPI(session, guid, login.jwt_token)
    api.user_id = login.user_id
    api.login_key = login.login_key
    end = time.monotonic() + timeout_seconds
    while time.monotonic() < end:
        data = await api.query_device_by_guid()
        login.jwt_token = api.jwt_token
        payload = first_not_none(nested(data, "data", "resp", "data"), nested(data, "data"), data)
        if isinstance(payload, dict):
            bound = first_not_none(payload.get("bound"), payload.get("is_bind"), payload.get("bind_status"))
            if bound in (True, 1, "1", "true"):
                return DeviceBindResult(True, "device bind success")
        await asyncio.sleep(5)
    return DeviceBindResult(False, "device bind timeout")


async def async_refresh_channel_token(hass: HomeAssistant, *, guid: str, login: WeChatLoginContext) -> str | None:
    session = async_get_clientsession(hass)
    api = QClawAPI(session, guid, login.jwt_token)
    api.user_id = login.user_id
    api.login_key = login.login_key
    token = await api.refresh_channel_token()
    login.jwt_token = api.jwt_token
    return token


async def async_is_invite_verified(hass: HomeAssistant, *, guid: str, login: WeChatLoginContext) -> bool:
    if not login.user_id:
        return True
    session = async_get_clientsession(hass)
    api = QClawAPI(session, guid, login.jwt_token)
    api.user_id = login.user_id
    api.login_key = login.login_key
    result = await api.check_invite_code(login.user_id)
    login.jwt_token = api.jwt_token
    verified = first_not_none(
        nested(result, "data", "resp", "data", "already_verified"),
        nested(result, "data", "already_verified"),
        nested(result, "already_verified"),
    )
    return bool(verified)


async def async_submit_invite_code(
    hass: HomeAssistant,
    *,
    guid: str,
    login: WeChatLoginContext,
    invite_code: str,
) -> None:
    session = async_get_clientsession(hass)
    api = QClawAPI(session, guid, login.jwt_token)
    api.user_id = login.user_id
    api.login_key = login.login_key
    await api.submit_invite_code(login.user_id, invite_code)
    login.jwt_token = api.jwt_token


def nested(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None
