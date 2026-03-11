"""Constants for CN IM Hub."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cn_im_hub"

PROVIDER_FEISHU: Final = "feishu"
PROVIDER_WECOM: Final = "wecom"
PROVIDER_QQ: Final = "qq"
PROVIDER_DINGTALK: Final = "dingtalk"
PROVIDER_WECHAT: Final = "wechat"
PROVIDERS: Final = (PROVIDER_FEISHU, PROVIDER_WECOM, PROVIDER_QQ, PROVIDER_DINGTALK, PROVIDER_WECHAT)

CONF_ENABLED_PROVIDERS: Final = "enabled_providers"
CONF_PROVIDERS: Final = "providers"
CONF_AGENT_ID: Final = "agent_id"

CONF_FEISHU_APP_ID: Final = "app_id"
CONF_FEISHU_APP_SECRET: Final = "app_secret"

CONF_WECOM_BOT_ID: Final = "bot_id"
CONF_WECOM_SECRET: Final = "secret"

CONF_QQ_APP_ID: Final = "qq_app_id"
CONF_QQ_CLIENT_SECRET: Final = "qq_client_secret"

CONF_DINGTALK_CLIENT_ID: Final = "dingtalk_client_id"
CONF_DINGTALK_CLIENT_SECRET: Final = "dingtalk_client_secret"

CONF_WECHAT_TOKEN: Final = "wechat_token"
CONF_WECHAT_GUID: Final = "wechat_guid"

WECHAT_JPRX_GATEWAY: Final = "https://jprx.m.qq.com/"
WECHAT_LOGIN_REDIRECT_URI: Final = "https://security.guanjia.qq.com/login"
WECHAT_APP_ID: Final = "wx9d11056dd75b7240"
WECHAT_DEFAULT_WS_URL: Final = "wss://mmgrcalltoken.3g.qq.com/agentwss"

SERVICE_SEND_MESSAGE: Final = "send_message"
SERVICE_TEST_CONVERSATION: Final = "test_conversation"

ATTR_PROVIDER: Final = "provider"
ATTR_TARGET: Final = "target"
ATTR_MESSAGE: Final = "message"
ATTR_TEXT: Final = "text"
ATTR_TARGET_TYPE: Final = "target_type"

DEFAULT_FEISHU_TARGET_TYPE: Final = "chat_id"
