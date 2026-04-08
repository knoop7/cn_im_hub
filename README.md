# 即时通信合集

把常见即时通信平台聚合到一个 Home Assistant 集成中。

## 当前支持

- Feishu
- WeCom
- QQ（WebSocket 网关）
- DingTalk（Stream 模式）
- WeChat（个人微信，**支持多人绑定**）
- XiaoYi（小艺 A2A WebSocket）

## 当前实现

- 添加集成时只配置一次全局 `agent_id`
- 各平台通过 subentry 独立添加、独立更新（个人微信支持添加多个账号）
- 消息统一走自然语言会话（conversation agent）

## 在 Home Assistant 里的设置

### 1) 安装集成

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-china&repository=cn_im_hub&category=integration)

1. 将本仓库部署到 HA 的 `custom_components/cn_im_hub`。
2. 重启 Home Assistant。
3. 进入 `设置 -> 设备与服务 -> 添加集成`，搜索 `即时通信合集`。
4. 添加时通过下拉列表选择一次全局 `agent_id`（后续所有平台共用）。

### 2) 首次添加行为

- 首次添加只创建 Hub，不会自动启用任何 IM 平台。

### 3) 在集成页面添加服务（Subentry）

1. 进入 `设置 -> 设备与服务 -> 即时通信合集`。
2. 在该集成页面点击“添加服务/添加子项”。
3. 选择要添加的平台：`Feishu` / `WeCom` / `QQ` / `DingTalk` / `WeChat`（个人微信） / `XiaoYi`。
4. 填写该平台凭据并保存。
5. 每个平台是一个独立服务项，可单独进入设置更新或删除。

注意：`agent_id` 是集成级必填项，不需要每个平台重复填写。

### 4) HA 服务

- `cn_im_hub.send_message`
  - 参数：`channel`、`target`、`message`、`camera_entity`、`wechat_account_id`（可选）

## 平台后端设置

说明：以下步骤均为当前已支持并已接入的配置方式。

### Feishu（飞书）

1. 创建企业自建应用。  
   ![飞书-创建应用](docs/images/feishu/feishu-step2-create-app.png)
2. 在“凭证与基础信息”获取 `App ID`、`App Secret`。  
   ![飞书-获取凭据](docs/images/feishu/feishu-step3-credentials.png)
3. 在“权限管理”授予消息收发权限（如 `im:message:readonly`、`im:message:send_as_bot`）。  
   ![飞书-权限管理](docs/images/feishu/feishu-step4-permissions.png)
4. 在“事件订阅”选择 WebSocket 长连接并添加 `im.message.receive_v1`，然后发布应用。  
   ![飞书-事件订阅](docs/images/feishu/feishu-step6-event-subscription.png)
5. 回到 HA 填写：`app_id`、`app_secret`。

### WeCom（企业微信）

1. 进入“智能机器人”创建机器人。  
   ![企微-创建机器人](docs/images/wecom/wecom-setup-1-create-bot.png)
2. 接入模式选 `API`，接入方式选“长连接”。  
   ![企微-接入方式](docs/images/wecom/wecom-setup-3-enter-chat.png)
3. 在详情页保存 `bot_id` 与 `secret`。  
   ![企微-BotID与Secret](docs/images/wecom/wecom-setup-2-bot-id-secret.png)
4. 确认机器人具备收发消息能力。
5. 回到 HA 填写：`bot_id`、`secret`。

### QQ（QQ 开放平台机器人）

（来自 Hello Claw 第三章，按本集成字段映射）

1. 打开 QQ 开放平台并登录：`https://q.qq.com/qqbot/openclaw/login.html`。  
   ![QQ-注册登录](docs/images/qq/qq-bot-register.png)
2. 点击“创建机器人”，完成后记录 `AppID` 与 `AppSecret`。  
   ![QQ-部署信息](docs/images/qq/qq-bot-deploy-browser.png)
3. 在 QQ 端做一次聊天验证，确认机器人已发布可用。  
   ![QQ-聊天测试](docs/images/qq/qq-bot-chat.jpg)
4. 本集成使用 Gateway WebSocket。
5. 回到 HA 填写：`qq_app_id`、`qq_client_secret`。

### DingTalk（钉钉）

（基于钉钉官方文档《将 OpenClaw 接入钉钉，创建你的 AI 助理员工》）

1. 登录钉钉开发者后台，进入“应用开发”创建应用。  
   ![钉钉-创建应用入口](docs/images/dingtalk/dingtalk-step1-create-app.png)
2. 填写应用基础信息并保存，确认应用已出现在应用列表。  
   ![钉钉-应用创建表单](docs/images/dingtalk/dingtalk-step1-app-form.png)
   ![钉钉-应用列表](docs/images/dingtalk/dingtalk-step1-app-list.png)
3. 在“凭证与基础信息”记录 `Client ID` 与 `Client Secret`。  
   ![钉钉-凭证与基础信息](docs/images/dingtalk/dingtalk-step1-credentials.png)
4. 进入应用详情，在“添加应用能力”中添加机器人能力，并在机器人配置中确认消息接收模式为 Stream。  
   ![钉钉-应用详情](docs/images/dingtalk/dingtalk-step2-app-detail.png)
   ![钉钉-添加机器人能力](docs/images/dingtalk/dingtalk-step2-add-bot-capability.png)
   ![钉钉-机器人配置Stream](docs/images/dingtalk/dingtalk-step2-bot-config-stream.png)
5. 在权限管理中添加权限：`Card.Streaming.Write`、`Card.Instance.Write`、`qyapi_robot_sendmsg`，然后创建新版本并发布。  
   ![钉钉-权限配置](docs/images/dingtalk/dingtalk-step3-permissions.png)
   ![钉钉-创建新版本](docs/images/dingtalk/dingtalk-step3-create-version.png)
   ![钉钉-版本详情](docs/images/dingtalk/dingtalk-step3-version-detail.png)
6. 把机器人添加到目标群进行联调，确认机器人可回复。  
   ![钉钉-群内添加机器人](docs/images/dingtalk/dingtalk-step6-add-bot-in-group.png)
   ![钉钉-机器人回复示例](docs/images/dingtalk/dingtalk-step6-bot-reply.png)
7. 回到 HA，在 DingTalk 子服务填写：
    - `dingtalk_client_id` = 钉钉 `Client ID`
    - `dingtalk_client_secret` = 钉钉 `Client Secret`

### XiaoYi（小艺）

1. 在 HA 集成页面添加 `XiaoYi` 子服务。
2. 填写 `Access Key`、`Secret Key`、`XiaoYi Agent ID`。
3. 集成会自动使用默认双 WebSocket 地址连接小艺服务。

### WeChat（个人微信）

重点：`个人微信支持多人绑定`（可在同一 Hub 下重复添加多个 WeChat 子服务）。

1. 在 HA 集成页面添加 `WeChat`（个人微信）子服务。
2. 页面会优先在弹窗中显示二维码；如果前端不渲染，也会同时显示二维码链接，可直接打开扫码。
3. 扫码确认后会保存当前微信账号凭据，并启用长轮询文本对话。
4. 如需绑定多个微信号，可重复“添加 `WeChat` 子服务 + 扫码登录”步骤；每次扫码会新增一个独立微信账号子服务。
5. 当前个人微信实现基于腾讯 `openclaw-weixin` 的登录与长轮询协议，只迁移纯文本对话，不包含图片/语音/文件能力。

### XiaoYi 后台创建流程（华为官方）

1. 登录小艺开放平台，进入智能体平台后点击左上角“+创建智能体”。  
   ![小艺-创建智能体入口](docs/images/xiaoyi/xiaoyi-step1-create-agent.png)
2. 按官方创建流程填写名称、头像、描述、分类和设备信息，选择 `OpenClaw` 模式创建智能体。  
   ![小艺-OpenClaw模式创建](docs/images/xiaoyi/xiaoyi-step2-openclaw-mode.png)
3. 进入“OpenClaw基础配置”，如果还没有凭证，先到“工作空间 -> 凭证”新建 Key，并立即保存生成的 `AK/SK`。  
   ![小艺-创建凭证](docs/images/xiaoyi/xiaoyi-step3-create-credential.png)
4. 回到智能体配置页，按官方页面完成服务器 channel 配置；除替换 `ak/sk` 外，其它默认配置不要随意修改，并记录智能体对应的 `agentId`。  
   ![小艺-channel配置](docs/images/xiaoyi/xiaoyi-step4-channel-config.png)
5. 如果你在 OpenClaw 环境中联调，官方示例是在 `openclaw.json` 顶层 `channels` 中新增 `xiaoyi` 配置。  
   ![小艺-openclaw配置示例](docs/images/xiaoyi/xiaoyi-step5-openclaw-json.png)
6. 完成配置后重启网关并查看日志；官方示例里以 `sent claw_bot_init message` 作为建立连接成功的标志。  
   ![小艺-openclaw日志](docs/images/xiaoyi/xiaoyi-step6-openclaw-logs.png)

说明：

- 本集成不是直接运行 OpenClaw，但小艺后台侧的创建逻辑是一致的：核心就是先创建 OpenClaw 模式智能体，再拿到 `AK/SK` 与 `agentId` 回填到 HA。
- 华为官方文档里还提到：每个账号最多创建 1 个 OpenClaw 模式智能体。
- 如果网页调试正常但设备侧还不可见，通常还需要在平台完成上架/发布流程。

常见踩坑（钉钉）：

- 已创建应用但收不到消息：通常是未安装到组织，或机器人未加入目标会话。
- 凭据正确但发送失败：优先检查权限是否完整，以及应用是否已发布。
- 能发不能收：优先检查是否启用了 Stream 长连接。

## 联调检查清单

- HA 端已选全局 `agent_id`，且该 agent 可正常对话。
- 平台服务已作为独立 subentry 添加成功。
- 平台凭据正确，且后台已发布/启用机器人能力。
- 网络可从 HA 主动访问平台接口（飞书、企微、QQ、钉钉）。
- 所有平台子服务均已在集成页面成功添加。

## 参考来源

- Hello Claw 第三章（QQ 机器人流程）：
  `https://datawhalechina.github.io/hello-claw/cn/adopt/chapter3/`
- ha-feishu（飞书后台配置与截图）：
  `https://github.com/ha-china/ha-feishu`
- ha_wecom（企微后台配置与截图）：
  `https://github.com/ha-china/ha_wecom`
- 钉钉官方文档（OpenClaw 接入）：
  `https://open.dingtalk.com/document/dingstart/build-dingtalk-ai-employees`
- 华为官方文档（OpenClaw 基础配置）：
  `https://developer.huawei.com/consumer/cn/doc/service/open-claw-base-0000002518704040`
- 华为官方文档（快速创建智能体）：
  `https://developer.huawei.com/consumer/cn/doc/service/quick-start-0000002469548009`
- 华为官方文档（OpenClaw 接入）：
  `https://developer.huawei.com/consumer/cn/doc/service/openclaw-0000002518410344`

## 目标地址格式（send_message）

- `channel` 直接选择发送通道与目标类型：`Feishu / chat_id`、`QQ / group`、`WeChat / user_id` 等
- 如果不想手填 `target`，可以先在对应平台的 `target selector` 实体里选择一个已知 ID，`send_message` 会自动优先使用当前已选目标
- 多个个人微信账号并存时，通常无需手填 `wechat_account_id`：系统会优先按 `target`（已知目标归属）或当前唯一已选 `target selector` 自动路由；仅在仍然歧义时再填写 `wechat_account_id`
- 如果填写 `camera_entity`，服务会先抓取该摄像头当前快照，再发图到目标会话；当前支持 `WeChat`、`WeCom`、`Feishu`、`QQ`、`DingTalk`
- 个人微信在使用 `camera_entity` 发图时也沿用同样的多账号路由逻辑：优先按 `wechat_account_id`，否则按 `target` / 当前已选 `target selector` 自动选择账号

## 对话方式

- 自然语言对话。
- 消息会统一转到集成级配置的 `agent_id` 对应的 HA conversation agent。
