"""Constants for CN IM Hub."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cn_im_hub"

PROVIDER_FEISHU: Final = "feishu"
PROVIDER_WECOM: Final = "wecom"
PROVIDER_QQ: Final = "qq"
PROVIDER_DINGTALK: Final = "dingtalk"
PROVIDER_XIAOYI: Final = "xiaoyi"
PROVIDERS: Final = (PROVIDER_FEISHU, PROVIDER_WECOM, PROVIDER_QQ, PROVIDER_DINGTALK, PROVIDER_XIAOYI)

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

CONF_XIAOYI_AK: Final = "xiaoyi_ak"
CONF_XIAOYI_SK: Final = "xiaoyi_sk"
CONF_XIAOYI_AGENT_ID: Final = "xiaoyi_agent_id"
CONF_XIAOYI_WS_URL_1: Final = "xiaoyi_ws_url_1"
CONF_XIAOYI_WS_URL_2: Final = "xiaoyi_ws_url_2"

XIAOYI_DEFAULT_WS_URL_1: Final = "wss://hag.cloud.huawei.com/openclaw/v1/ws/link"
XIAOYI_DEFAULT_WS_URL_2: Final = "wss://116.63.174.231/openclaw/v1/ws/link"

SERVICE_SEND_MESSAGE: Final = "send_message"
SERVICE_TEST_CONVERSATION: Final = "test_conversation"

ATTR_PROVIDER: Final = "provider"
ATTR_TARGET: Final = "target"
ATTR_MESSAGE: Final = "message"
ATTR_TEXT: Final = "text"
ATTR_TARGET_TYPE: Final = "target_type"

DEFAULT_FEISHU_TARGET_TYPE: Final = "chat_id"

TARGET_TYPE_FEISHU_CHAT_ID: Final = "feishu:chat_id"
TARGET_TYPE_FEISHU_OPEN_ID: Final = "feishu:open_id"
TARGET_TYPE_FEISHU_USER_ID: Final = "feishu:user_id"
TARGET_TYPE_FEISHU_UNION_ID: Final = "feishu:union_id"
TARGET_TYPE_WECOM_CHATID: Final = "wecom:chatid"
TARGET_TYPE_QQ_USER: Final = "qq:user"
TARGET_TYPE_QQ_GROUP: Final = "qq:group"
TARGET_TYPE_QQ_CHANNEL: Final = "qq:channel"
TARGET_TYPE_DINGTALK_USER: Final = "dingtalk:user"
TARGET_TYPE_DINGTALK_GROUP: Final = "dingtalk:group"

TARGET_TYPE_OPTIONS: Final = (
    TARGET_TYPE_FEISHU_CHAT_ID,
    TARGET_TYPE_FEISHU_OPEN_ID,
    TARGET_TYPE_FEISHU_USER_ID,
    TARGET_TYPE_FEISHU_UNION_ID,
    TARGET_TYPE_WECOM_CHATID,
    TARGET_TYPE_QQ_USER,
    TARGET_TYPE_QQ_GROUP,
    TARGET_TYPE_QQ_CHANNEL,
    TARGET_TYPE_DINGTALK_USER,
    TARGET_TYPE_DINGTALK_GROUP,
)
