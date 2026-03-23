"""Weixin QR login flow."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SubentryFlowResult

from ..const import CONF_WECHAT_ACCOUNT_ID, CONF_WECHAT_BASE_URL, CONF_WECHAT_TOKEN, CONF_WECHAT_USER_ID, WECHAT_DEFAULT_BASE_URL
from ..provider_flow import BaseProviderSubentryFlow
from .wechat_auth import async_start_weixin_login, async_wait_weixin_login

_LOGGER = logging.getLogger(__name__)


class WeixinProviderSubentryFlow(BaseProviderSubentryFlow):
    """QR login based setup flow for Weixin channel."""

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
        self._current = {CONF_WECHAT_BASE_URL: WECHAT_DEFAULT_BASE_URL}
        await self._async_prepare_qr()
        return await self.async_step_auth_wait(None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._current = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_set_options(user_input)

    async def async_step_auth_wait(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        placeholders = {
            "qr_markdown": f"![Weixin QR]({self._current.get('wechat_qr_data_url', '')})"
            if self._current.get("wechat_qr_data_url")
            else "",
            "qr_url": str(self._current.get("wechat_qr_url", "")),
        }
        if user_input is None:
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                description_placeholders=placeholders,
            )
        try:
            result = await async_wait_weixin_login(
                self.hass,
                login=self._current["wechat_login_session"],
                base_url=str(self._current.get(CONF_WECHAT_BASE_URL, WECHAT_DEFAULT_BASE_URL)),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Weixin QR login wait failed: %s", err)
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                errors={"base": "auth_not_confirmed"},
                description_placeholders=placeholders,
            )

        data = {
            CONF_WECHAT_TOKEN: result.token,
            CONF_WECHAT_ACCOUNT_ID: result.account_id,
            CONF_WECHAT_USER_ID: result.user_id,
            CONF_WECHAT_BASE_URL: result.base_url or str(self._current.get(CONF_WECHAT_BASE_URL, WECHAT_DEFAULT_BASE_URL)),
        }
        return await self._async_complete(data)

    async def _async_prepare_qr(self) -> None:
        result = await async_start_weixin_login(
            self.hass,
            base_url=str(self._current.get(CONF_WECHAT_BASE_URL, WECHAT_DEFAULT_BASE_URL)),
        )
        self._current["wechat_login_session"] = result
        self._current["wechat_qr_url"] = result.qrcode_url
        self._current["wechat_qr_data_url"] = result.qrcode_data_url
