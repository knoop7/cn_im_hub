# 中国即时通信合集

把中国常见即时通信平台聚合到一个 Home Assistant 集成中。

## 设计目标

- 默认添加集成时不启用任何服务
- 在集成选项中选择并配置需要的服务
- 各服务复用同一套消息路由与会话处理逻辑，便于扩展

## 当前支持

- Feishu
- WeCom
- QQ（WebSocket 网关）
- DingTalk（Stream 模式）

## 接入原则

- 不接入需要 HTTP 回调且依赖公网暴露的模式
- 优先使用长连接 / Stream / WebSocket 模式

## 在 Home Assistant 里的设置

### 1) 安装集成

1. 将本仓库部署到 HA 的 `custom_components/cn_im_hub`。
2. 重启 Home Assistant。
3. 进入 `设置 -> 设备与服务 -> 添加集成`，搜索 `中国即时通信合集`。
4. 添加时通过下拉列表选择一次全局 `agent_id`（后续所有平台共用）。

### 2) 首次添加行为

- 首次添加只创建 Hub，不会自动启用任何 IM 平台。

### 3) 在集成页面添加服务（Subentry）

1. 进入 `设置 -> 设备与服务 -> 中国即时通信合集`。
2. 在该集成页面点击“添加服务/添加子项”（不同 HA 版本文案略有差异）。
3. 选择要添加的平台：`Feishu` / `WeCom` / `QQ` / `DingTalk`。
4. 填写该平台凭据并保存。
5. 每个平台是一个独立服务项，可单独进入设置更新或删除。

注意：`agent_id` 是集成级必填项，不需要每个平台重复填写。

### 4) HA 服务

- `cn_im_hub.send_message`
  - 参数：`provider`、`target`、`target_type`、`message`
- `cn_im_hub.test_conversation`
  - 参数：`provider`、`text`

## 平台后端设置

说明：以下步骤按你最初参考的 `ha-feishu`、`ha_wecom` 与 Hello Claw 第三章整理，统一采用“主动外连（WebSocket/Stream）”，不要求公网回调地址。

### Feishu（飞书）

1. 在飞书开放平台创建企业自建应用。
2. 获取 `App ID` 与 `App Secret`。
3. 在“应用能力”中启用机器人（Bot）。
4. 在“事件订阅”中选择“长连接接收事件（WebSocket）”。
5. 添加事件：`im.message.receive_v1`。
6. 在“权限管理”中至少授予消息收发相关权限（例如 `im:message:readonly`、`im:message:send_as_bot`）。
7. 发布应用（企业内可用）。
8. 在 HA 填写：`app_id`、`app_secret`。

参考截图（来自 `ha-feishu`）：

![飞书-创建应用](docs/images/feishu/feishu-step2-create-app.png)
![飞书-获取凭据](docs/images/feishu/feishu-step3-credentials.png)
![飞书-权限管理](docs/images/feishu/feishu-step4-permissions.png)
![飞书-事件订阅](docs/images/feishu/feishu-step6-event-subscription.png)

### WeCom（企业微信）

1. 在企业微信管理后台进入“智能机器人”。
2. 创建机器人并选择 `API` 模式。
3. 接入方式选择“长连接”。
4. 获取并保存 `bot_id` 与 `secret`。
5. 确认机器人具备收发消息能力。
6. 不配置 webhook 公网回调。
7. 在 HA 填写：`bot_id`、`secret`。

参考截图（来自 `ha_wecom`）：

![企微-创建机器人](docs/images/wecom/wecom-setup-1-create-bot.png)
![企微-接入方式](docs/images/wecom/wecom-setup-3-enter-chat.png)
![企微-BotID与Secret](docs/images/wecom/wecom-setup-2-bot-id-secret.png)

### QQ（QQ 开放平台机器人）

以下步骤来自 Hello Claw 第三章（QQ 机器人部分），按本集成可用字段对应：

1. 打开 QQ 开放平台 OpenClaw 接入页并登录：
   `https://q.qq.com/qqbot/openclaw/login.html`
2. 点击“创建机器人”，设置机器人名称与头像。
3. 创建完成后记录 `AppID` 与 `AppSecret`。
4. 在 QQ 平台侧确认机器人已发布可用。
5. 本集成采用 Gateway WebSocket，不配置 HTTP 回调地址。
6. 在 HA 的 QQ 子服务里填写：`qq_app_id`、`qq_client_secret`。

参考截图（来自 Hello Claw）：

![QQ-注册登录](docs/images/qq/qq-bot-register.png)
![QQ-部署信息](docs/images/qq/qq-bot-deploy-browser.png)
![QQ-聊天测试](docs/images/qq/qq-bot-chat.jpg)

### DingTalk（钉钉）

1. 在钉钉开放平台创建企业内部应用并启用机器人能力。
2. 获取 `Client ID` 与 `Client Secret`。
3. 开启 Stream 模式（事件通过长连接接入）。
4. 不使用 webhook 回调模式。
5. 开通机器人消息收发所需权限。
6. 在 HA 填写：`dingtalk_client_id`、`dingtalk_client_secret`。

## 联调检查清单

- HA 端已选全局 `agent_id`，且该 agent 可正常对话。
- 平台服务已作为独立 subentry 添加成功。
- 平台凭据正确，且后台已发布/启用机器人能力。
- 网络可从 HA 主动访问平台接口（飞书、企微、QQ、钉钉）。
- 不配置公网回调 URL（本集成按主动外连设计）。

## 参考来源

- Hello Claw 第三章（QQ 机器人流程）：
  `https://datawhalechina.github.io/hello-claw/cn/adopt/chapter3/`
- ha-feishu（飞书后台配置与截图）：
  `https://github.com/ha-china/ha-feishu`
- ha_wecom（企微后台配置与截图）：
  `https://github.com/ha-china/ha_wecom`

## 目标地址格式（send_message）

- `feishu`：`target_type` 常用 `chat_id`，`target` 填 chat_id
- `wecom`：`target` 填 chatid 或可达目标
- `qq`：建议使用 `user:<openid>` / `group:<group_openid>` / `channel:<channel_id>`
- `dingtalk`：`target_type=user` 填用户 ID；`target_type=group` 填群会话 ID

## 对话方式

- 仅支持自然语言对话（不再支持 `ha:` 命令前缀）。
- 消息会统一转到集成级配置的 `agent_id` 对应的 HA conversation agent。
