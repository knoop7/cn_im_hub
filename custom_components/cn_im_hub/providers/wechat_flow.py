"""WeChat-specific subentry flow."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.config_entries import SubentryFlowResult

from ..const import (
    CONF_WECHAT_API_KEY,
    CONF_WECHAT_ENVIRONMENT,
    CONF_WECHAT_GUID,
    CONF_WECHAT_JWT_TOKEN,
    CONF_WECHAT_LOGIN_KEY,
    CONF_WECHAT_TOKEN,
    CONF_WECHAT_USER_ID,
    CONF_WECHAT_WS_URL,
)
from ..provider_flow import BaseProviderSubentryFlow
from ..wechat_auth import (
    WeChatLoginContext,
    async_exchange_code_for_channel_token,
    async_generate_contact_link,
    async_is_invite_verified,
    async_perform_device_binding,
    async_poll_qr_for_code,
    async_prepare_qr_login,
    async_submit_invite_code,
    build_qr_data_url,
)

_LOGGER = logging.getLogger(__name__)


class WeChatProviderSubentryFlow(BaseProviderSubentryFlow):
    """Multi-step WeChat login and bind flow."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        existing = [
            subentry
            for subentry in self._get_entry().subentries.values()
            if subentry.subentry_type == self._provider_spec.key
        ]
        if existing:
            return self.async_abort(reason="already_configured")
        self._current = {
            CONF_WECHAT_GUID: uuid4().hex,
            CONF_WECHAT_ENVIRONMENT: "production",
        }
        await self._async_prepare_wechat_auth()
        return await self.async_step_auth_wait(None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._current = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_set_options(user_input)

    async def async_step_set_options(
        self, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {key: str(value).strip() for key, value in user_input.items()}
            data[CONF_WECHAT_TOKEN] = data.get(CONF_WECHAT_TOKEN) or str(self._current.get(CONF_WECHAT_TOKEN, ""))
            try:
                await self._provider_spec.validate_config(self.hass, data)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Provider validation failed (%s): %s", self._provider_spec.key, err)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_complete(data)
            self._current = data

        return self.async_show_form(
            step_id="set_options",
            data_schema=self._provider_spec.schema_builder(self._current),
            errors=errors,
        )

    async def async_step_auth_wait(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        placeholders = {
            "oauth_url": str(self._current.get("wechat_oauth_url", "")).strip(),
            "qr_image_url": str(self._current.get("wechat_qr_image_url", "")).strip(),
            "qr_markdown": f"![WeChat QR]({self._current.get('wechat_qr_data_url', '')})"
            if self._current.get("wechat_qr_data_url")
            else "",
        }
        if user_input is None:
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                description_placeholders=placeholders,
            )

        try:
            login = await self._async_wait_and_exchange_wechat_login()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Wechat login exchange failed: %s", err)
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                errors={"base": "auth_not_confirmed"},
                description_placeholders=placeholders,
            )

        self._current[CONF_WECHAT_TOKEN] = login.channel_token
        self._current[CONF_WECHAT_JWT_TOKEN] = login.jwt_token
        self._current[CONF_WECHAT_USER_ID] = login.user_id
        self._current[CONF_WECHAT_LOGIN_KEY] = login.login_key
        self._current[CONF_WECHAT_WS_URL] = login.ws_url
        if login.api_key:
            self._current[CONF_WECHAT_API_KEY] = login.api_key

        try:
            invite_verified = await async_is_invite_verified(
                self.hass,
                guid=str(self._current.get(CONF_WECHAT_GUID) or ""),
                login=login,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Check WeChat invite verification failed: %s", err)
            invite_verified = True

        self._current[CONF_WECHAT_JWT_TOKEN] = login.jwt_token
        if not invite_verified:
            return await self.async_step_invite_code(None)
        return await self._async_finish_wechat_login(login)

    async def async_step_invite_code(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            invite_code = str(user_input.get("invite_code") or "").strip()
            if not invite_code:
                errors["base"] = "invite_code_required"
            else:
                login = WeChatLoginContext(
                    channel_token=str(self._current.get(CONF_WECHAT_TOKEN) or ""),
                    jwt_token=str(self._current.get(CONF_WECHAT_JWT_TOKEN) or ""),
                    user_id=str(self._current.get(CONF_WECHAT_USER_ID) or ""),
                    login_key=str(self._current.get(CONF_WECHAT_LOGIN_KEY) or ""),
                    api_key=str(self._current.get(CONF_WECHAT_API_KEY) or ""),
                    ws_url=str(self._current.get(CONF_WECHAT_WS_URL) or ""),
                )
                try:
                    await async_submit_invite_code(
                        self.hass,
                        guid=str(self._current.get(CONF_WECHAT_GUID) or ""),
                        login=login,
                        invite_code=invite_code,
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Submit WeChat invite code failed: %s", err)
                    errors["base"] = "invalid_invite_code"
                else:
                    self._current[CONF_WECHAT_JWT_TOKEN] = login.jwt_token
                    return await self._async_finish_wechat_login(login)

        return self.async_show_form(
            step_id="invite_code",
            data_schema=vol.Schema({vol.Required("invite_code"): str}),
            errors=errors,
        )

    async def async_step_bind_wait(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        contact_url = str(self._current.get("wechat_contact_url", "")).strip()
        placeholders = {
            "contact_url": contact_url,
            "contact_qr_markdown": f"![Contact QR]({self._current.get('wechat_contact_qr', '')})"
            if self._current.get("wechat_contact_qr")
            else "",
        }

        if user_input is None:
            return self.async_show_form(
                step_id="bind_wait",
                data_schema=vol.Schema({}),
                description_placeholders=placeholders,
            )

        login = WeChatLoginContext(
            channel_token=str(self._current.get(CONF_WECHAT_TOKEN) or ""),
            jwt_token=str(self._current.get(CONF_WECHAT_JWT_TOKEN) or ""),
            user_id=str(self._current.get(CONF_WECHAT_USER_ID) or ""),
            login_key=str(self._current.get(CONF_WECHAT_LOGIN_KEY) or ""),
            api_key=str(self._current.get(CONF_WECHAT_API_KEY) or ""),
            ws_url=str(self._current.get(CONF_WECHAT_WS_URL) or ""),
        )
        bind_result = await async_perform_device_binding(
            self.hass,
            guid=str(self._current.get(CONF_WECHAT_GUID) or ""),
            login=login,
            contact_url=contact_url or None,
            timeout_seconds=300,
        )
        self._current[CONF_WECHAT_JWT_TOKEN] = login.jwt_token
        if not bind_result.success:
            return self.async_show_form(
                step_id="bind_wait",
                data_schema=vol.Schema({}),
                errors={"base": "bind_not_confirmed"},
                description_placeholders=placeholders,
            )

        data = {
            CONF_WECHAT_TOKEN: str(self._current.get(CONF_WECHAT_TOKEN, "")),
            CONF_WECHAT_GUID: str(self._current.get(CONF_WECHAT_GUID, "")),
            CONF_WECHAT_JWT_TOKEN: str(self._current.get(CONF_WECHAT_JWT_TOKEN, "")),
            CONF_WECHAT_USER_ID: str(self._current.get(CONF_WECHAT_USER_ID, "")),
            CONF_WECHAT_LOGIN_KEY: str(self._current.get(CONF_WECHAT_LOGIN_KEY, "")),
            CONF_WECHAT_WS_URL: str(self._current.get(CONF_WECHAT_WS_URL, "")),
            CONF_WECHAT_ENVIRONMENT: str(self._current.get(CONF_WECHAT_ENVIRONMENT, "production")),
        }
        if self._current.get(CONF_WECHAT_API_KEY):
            data[CONF_WECHAT_API_KEY] = str(self._current.get(CONF_WECHAT_API_KEY, ""))
        return await self._async_complete(data)

    async def _async_prepare_wechat_auth(self) -> None:
        guid = str(self._current.get(CONF_WECHAT_GUID) or uuid4().hex)
        self._current[CONF_WECHAT_GUID] = guid
        prepared = await async_prepare_qr_login(self.hass, guid)
        self._current["wechat_state"] = prepared["state"]
        self._current["wechat_oauth_url"] = prepared["auth_url"]
        self._current["wechat_qr_uuid"] = prepared["qr_uuid"]
        self._current["wechat_qr_image_url"] = prepared["qr_image_url"]
        self._current["wechat_qr_data_url"] = prepared.get("qr_data_url", "")

    async def _async_wait_and_exchange_wechat_login(self) -> WeChatLoginContext:
        guid = str(self._current.get(CONF_WECHAT_GUID) or "")
        state = str(self._current.get("wechat_state") or "")
        qr_uuid = str(self._current.get("wechat_qr_uuid") or "")
        if not guid or not state or not qr_uuid:
            raise ValueError("wechat auth context missing")
        code = await async_poll_qr_for_code(self.hass, qr_uuid=qr_uuid, timeout_seconds=180)
        return await async_exchange_code_for_channel_token(
            self.hass,
            guid=guid,
            code=code,
            state=state,
        )

    async def _async_finish_wechat_login(self, login: WeChatLoginContext) -> SubentryFlowResult:
        try:
            contact_url = await async_generate_contact_link(
                self.hass,
                guid=str(self._current.get(CONF_WECHAT_GUID) or ""),
                login=login,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Generate contact link failed: %s", err)
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                errors={"base": "cannot_connect"},
                description_placeholders={
                    "oauth_url": str(self._current.get("wechat_oauth_url", "")).strip(),
                    "qr_image_url": str(self._current.get("wechat_qr_image_url", "")).strip(),
                    "qr_markdown": f"![WeChat QR]({self._current.get('wechat_qr_data_url', '')})"
                    if self._current.get("wechat_qr_data_url")
                    else "",
                },
            )

        self._current[CONF_WECHAT_JWT_TOKEN] = login.jwt_token
        self._current["wechat_contact_url"] = contact_url
        self._current["wechat_contact_qr"] = build_qr_data_url(contact_url)
        return await self.async_step_bind_wait(None)
