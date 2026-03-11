"""Config flow for CN IM Hub."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow as HAConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AGENT_ID,
    CONF_DINGTALK_CLIENT_ID,
    CONF_DINGTALK_CLIENT_SECRET,
    CONF_FEISHU_APP_ID,
    CONF_FEISHU_APP_SECRET,
    CONF_QQ_APP_ID,
    CONF_QQ_CLIENT_SECRET,
    CONF_WECHAT_GUID,
    CONF_WECHAT_TOKEN,
    CONF_WECOM_BOT_ID,
    CONF_WECOM_SECRET,
    DOMAIN,
    PROVIDER_DINGTALK,
    PROVIDER_FEISHU,
    PROVIDER_QQ,
    PROVIDER_WECHAT,
    PROVIDER_WECOM,
)
from .providers.dingtalk import async_validate_config as validate_dingtalk
from .providers.feishu import async_validate_config as validate_feishu
from .providers.qq import async_validate_config as validate_qq
from .providers.wechat import async_validate_config as validate_wechat
from .providers.wecom import async_validate_config as validate_wecom
from .wechat_auth import (
    async_exchange_code_for_channel_token,
    async_get_login_state,
    async_poll_qr_for_code,
    async_prepare_qr_login,
    build_auth_url,
)

_LOGGER = logging.getLogger(__name__)


async def _get_preferred_agent_id(hass) -> str:
    """Get preferred Assist pipeline conversation agent id."""
    try:
        from homeassistant.components.assist_pipeline.pipeline import async_get_pipeline

        pipeline = async_get_pipeline(hass)
        if isinstance(pipeline.conversation_engine, str) and pipeline.conversation_engine:
            return pipeline.conversation_engine
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Unable to resolve preferred assist pipeline: %r", err)
    return ""


def _agent_selector(hass) -> selector.ConversationAgentSelector:
    return selector.ConversationAgentSelector({"language": hass.config.language})


class ConfigFlow(HAConfigFlow, domain=DOMAIN):
    """One-step hub setup; providers are added as subentries."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        preferred_agent = await _get_preferred_agent_id(self.hass)
        if user_input is not None:
            agent_id = str(user_input.get(CONF_AGENT_ID, "")).strip()
            if not agent_id:
                errors["base"] = "agent_id_required"
            else:
                return self.async_create_entry(
                    title="中国即时通信合集",
                    data={},
                    options={CONF_AGENT_ID: agent_id},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_AGENT_ID, default=preferred_agent): _agent_selector(self.hass)}
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> "OptionsFlowHandler":
        return OptionsFlowHandler(config_entry)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {
            PROVIDER_FEISHU: ProviderSubentryFlow,
            PROVIDER_WECOM: ProviderSubentryFlow,
            PROVIDER_QQ: ProviderSubentryFlow,
            PROVIDER_DINGTALK: ProviderSubentryFlow,
            PROVIDER_WECHAT: ProviderSubentryFlow,
        }


class OptionsFlowHandler(OptionsFlow):
    """Only manage global agent at options level."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        preferred_agent = await _get_preferred_agent_id(self.hass)
        current = str(
            self._config_entry.options.get(
                CONF_AGENT_ID,
                self._config_entry.data.get(CONF_AGENT_ID, preferred_agent),
            )
        ).strip()

        if user_input is not None:
            agent_id = str(user_input.get(CONF_AGENT_ID, "")).strip()
            if not agent_id:
                errors["base"] = "agent_id_required"
            else:
                return self.async_create_entry(title="", data={CONF_AGENT_ID: agent_id})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_AGENT_ID, default=current): _agent_selector(self.hass)}
            ),
            errors=errors,
        )


class ProviderSubentryFlow(ConfigSubentryFlow):
    """Add/edit provider credentials as one subentry per provider type."""

    _current: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    async def _validate(self, data: dict[str, str]) -> None:
        if self._subentry_type == PROVIDER_FEISHU:
            if not data.get(CONF_FEISHU_APP_ID) or not data.get(CONF_FEISHU_APP_SECRET):
                raise ValueError("app_id and app_secret are required")
            return
        if self._subentry_type == PROVIDER_WECOM:
            await validate_wecom(self.hass, data)
            return
        if self._subentry_type == PROVIDER_QQ:
            await validate_qq(self.hass, data)
            return
        if self._subentry_type == PROVIDER_DINGTALK:
            await validate_dingtalk(self.hass, data)
            return
        if self._subentry_type == PROVIDER_WECHAT:
            await validate_wechat(self.hass, data)

    def _schema(self, current: dict[str, Any]) -> vol.Schema:
        if self._subentry_type == PROVIDER_FEISHU:
            return vol.Schema(
                {
                    vol.Required(CONF_FEISHU_APP_ID, default=current.get(CONF_FEISHU_APP_ID, "")): str,
                    vol.Required(CONF_FEISHU_APP_SECRET, default=current.get(CONF_FEISHU_APP_SECRET, "")): str,
                }
            )
        if self._subentry_type == PROVIDER_WECOM:
            return vol.Schema(
                {
                    vol.Required(CONF_WECOM_BOT_ID, default=current.get(CONF_WECOM_BOT_ID, "")): str,
                    vol.Required(CONF_WECOM_SECRET, default=current.get(CONF_WECOM_SECRET, "")): str,
                }
            )
        if self._subentry_type == PROVIDER_QQ:
            return vol.Schema(
                {
                    vol.Required(CONF_QQ_APP_ID, default=current.get(CONF_QQ_APP_ID, "")): str,
                    vol.Required(CONF_QQ_CLIENT_SECRET, default=current.get(CONF_QQ_CLIENT_SECRET, "")): str,
                }
            )
        if self._subentry_type == PROVIDER_WECHAT:
            return vol.Schema(
                {
                    vol.Required(CONF_WECHAT_TOKEN, default=current.get(CONF_WECHAT_TOKEN, "")): str,
                }
            )
        return vol.Schema(
            {
                vol.Required(CONF_DINGTALK_CLIENT_ID, default=current.get(CONF_DINGTALK_CLIENT_ID, "")): str,
                vol.Required(
                    CONF_DINGTALK_CLIENT_SECRET,
                    default=current.get(CONF_DINGTALK_CLIENT_SECRET, ""),
                ): str,
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        existing = [
            s
            for s in self._get_entry().subentries.values()
            if s.subentry_type == self._subentry_type
        ]
        if existing:
            return self.async_abort(reason="already_configured")
        self._current = {}
        if self._subentry_type == PROVIDER_WECHAT:
            self._current[CONF_WECHAT_GUID] = uuid4().hex
            await self._async_prepare_wechat_auth()
            return await self.async_step_auth_wait(None)
        return await self.async_step_set_options(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._current = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_set_options(user_input)

    async def async_step_auth_wait(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show QR and exchange token automatically in background."""
        await self._async_prepare_wechat_auth()
        auth_url = str(self._current.get("wechat_oauth_url", "")).strip()
        qr_image_url = str(self._current.get("wechat_qr_image_url", "")).strip()

        placeholders = {
            "oauth_url": auth_url,
            "qr_image_url": qr_image_url,
        }

        progress_task = self.async_get_progress_task()
        if progress_task is None:
            progress_task = self.hass.async_create_task(self._async_wait_and_exchange_wechat_token())

        if not progress_task.done():
            return self.async_show_progress(
                step_id="auth_wait",
                progress_action="wechat_qr_login",
                description_placeholders=placeholders,
                progress_task=progress_task,
            )

        try:
            token = progress_task.result()
        except Exception as err:
            _LOGGER.warning("Wechat login exchange failed: %s", err)
            self.async_cancel_progress_task()
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                errors={"base": "auth_not_confirmed"},
                description_placeholders=placeholders,
            )

        self._current[CONF_WECHAT_TOKEN] = token
        return self.async_show_progress_done(next_step_id="set_options")

    async def _async_wait_and_exchange_wechat_token(self) -> str:
        guid = str(self._current.get(CONF_WECHAT_GUID) or "")
        state = str(self._current.get("wechat_state") or "")
        qr_uuid = str(self._current.get("wechat_qr_uuid") or "")
        if not guid or not state or not qr_uuid:
            raise ValueError("wechat auth context missing")

        code = await async_poll_qr_for_code(self.hass, qr_uuid=qr_uuid, timeout_seconds=180)
        if not code:
            raise ValueError("wechat auth not confirmed")

        token, _ = await async_exchange_code_for_channel_token(
            self.hass,
            guid=guid,
            code=code,
            state=state,
        )
        return token

    async def _async_prepare_wechat_auth(self) -> None:
        guid = str(self._current.get(CONF_WECHAT_GUID) or uuid4().hex)
        self._current[CONF_WECHAT_GUID] = guid

        state = str(self._current.get("wechat_state", "")).strip()
        auth_url = str(self._current.get("wechat_oauth_url", "")).strip()
        qr_uuid = str(self._current.get("wechat_qr_uuid", "")).strip()
        if state and auth_url and qr_uuid:
            return

        try:
            prepared = await async_prepare_qr_login(self.hass, guid)
        except Exception as err:
            _LOGGER.warning("Prepare WeChat OAuth failed: %s", err)
            state = await async_get_login_state(self.hass, guid)
            auth_url = build_auth_url(state)
            qr_uuid = ""
            qr_image_url = ""
        else:
            state = prepared["state"]
            auth_url = prepared["auth_url"]
            qr_uuid = prepared["qr_uuid"]
            qr_image_url = prepared["qr_image_url"]

        self._current["wechat_state"] = state
        self._current["wechat_oauth_url"] = auth_url
        self._current["wechat_qr_uuid"] = qr_uuid
        self._current["wechat_qr_image_url"] = qr_image_url

    async def async_step_set_options(
        self, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {k: str(v).strip() for k, v in user_input.items()}
            if self._subentry_type == PROVIDER_WECHAT:
                data[CONF_WECHAT_TOKEN] = data.get(CONF_WECHAT_TOKEN) or str(
                    self._current.get(CONF_WECHAT_TOKEN, "")
                )
            try:
                await self._validate(data)
            except Exception as err:
                _LOGGER.warning("Provider validation failed (%s): %s", self._subentry_type, err)
                errors["base"] = "cannot_connect"
            else:
                if self._is_new:
                    title_map = {
                        PROVIDER_FEISHU: "Feishu",
                        PROVIDER_WECOM: "WeCom",
                        PROVIDER_QQ: "QQ",
                        PROVIDER_DINGTALK: "DingTalk",
                        PROVIDER_WECHAT: "WeChat",
                    }
                    return self.async_create_entry(
                        title=title_map.get(self._subentry_type, self._subentry_type),
                        data=data,
                    )
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    data=data,
                )
            self._current = data

        return self.async_show_form(
            step_id="set_options",
            data_schema=self._schema(self._current),
            errors=errors,
        )
