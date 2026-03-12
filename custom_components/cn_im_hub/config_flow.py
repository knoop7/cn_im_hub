"""Config flow for CN IM Hub."""

from __future__ import annotations

import logging
from typing import Any

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
    CONF_XIAOYI_AGENT_ID,
    CONF_XIAOYI_AK,
    CONF_XIAOYI_SK,
    CONF_WECOM_BOT_ID,
    CONF_WECOM_SECRET,
    DOMAIN,
    PROVIDER_DINGTALK,
    PROVIDER_FEISHU,
    PROVIDER_QQ,
    PROVIDER_XIAOYI,
    PROVIDER_WECOM,
)
from .providers.dingtalk import async_validate_config as validate_dingtalk
from .providers.feishu import async_validate_config as validate_feishu
from .providers.qq import async_validate_config as validate_qq
from .providers.wecom import async_validate_config as validate_wecom
from .providers.xiaoyi import async_validate_config as validate_xiaoyi

_LOGGER = logging.getLogger(__name__)


async def _get_preferred_agent_id(hass) -> str:
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
                    title="",
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
            PROVIDER_XIAOYI: ProviderSubentryFlow,
        }


class OptionsFlowHandler(OptionsFlow):
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
    _current: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    async def _validate(self, data: dict[str, str]) -> None:
        if self._subentry_type == PROVIDER_FEISHU:
            await validate_feishu(self.hass, data)
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
        if self._subentry_type == PROVIDER_XIAOYI:
            await validate_xiaoyi(self.hass, data)

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
        if self._subentry_type == PROVIDER_XIAOYI:
            return vol.Schema(
                {
                    vol.Required(CONF_XIAOYI_AK, default=current.get(CONF_XIAOYI_AK, "")): str,
                    vol.Required(CONF_XIAOYI_SK, default=current.get(CONF_XIAOYI_SK, "")): str,
                    vol.Required(CONF_XIAOYI_AGENT_ID, default=current.get(CONF_XIAOYI_AGENT_ID, "")): str,
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
        return await self.async_step_set_options(user_input)

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
            data = {k: str(v).strip() for k, v in user_input.items()}
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
                        PROVIDER_XIAOYI: "XiaoYi",
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
