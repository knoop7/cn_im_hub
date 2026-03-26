## 中文

- 大量优化 Home Assistant 主动向各个平台发送消息的体验，统一为单一 `channel` 选择方式。
- 新增并完善已知目标 ID 选择能力，可在各平台的 `target selector` 实体中直接选择已发现的目标。
- 个人微信能力升级为基于腾讯 `openclaw-weixin` 的扫码登录与长轮询纯文本对话实现。
- 增强个人微信、QQ、飞书、企业微信、钉钉等平台与 Home Assistant 之间的消息发送与目标管理体验。
- 优化小艺与其他平台的连接稳定性、服务定义和诊断展示。

## English

- Significantly improved the Home Assistant outbound messaging experience across all supported channels with a unified `channel` selector.
- Added and improved known target ID selection, allowing direct reuse of discovered targets through per-provider `target selector` entities.
- Upgraded personal WeChat to a Tencent `openclaw-weixin` based QR-login and long-poll pure-text implementation.
- Improved outbound messaging and target management for Personal WeChat, QQ, Feishu, WeCom, DingTalk, and related providers.
- Refined XiaoYi and other provider connection stability, service definitions, and diagnostic visibility.
