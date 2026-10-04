# QQ SDK 连通性验证

2026-10-03 已实测 token、Gateway、WebSocket、心跳和私聊本地 PNG 图片回复；用户已确认图片正常显示。证据和待验证范围见 [验证结果](验证结果.md)。

在项目根目录运行（兼容现有 `.env` 的 `QQ_APP_ID` 和 `QQ_APP_SECRET`，也支持验证计划中的名称）：

```bash
UV_CACHE_DIR=/tmp/kurumibot-uv-cache uv run --project validation/qqbot --locked --env-file .env python validation/qqbot/qqbot_probe.py --duration 120
```

默认回复 `pics_sample/1.png`；设置 `QQBOT_IMAGE_PATH` 可切换本地图片。支持群 @（`GROUP_AT_MESSAGE_CREATE`）和私聊（`C2C_MESSAGE_CREATE`），使用对应渠道上传本地图片并回复到原会话。会话和用户标识、入站及出站消息 ID 使用哈希关联。SDK 原始日志关闭，避免上传凭证或原始标识泄漏。会话状态在内存中保存，并使用线程锁；本次不验证跨进程会话恢复。

`--duration 1800` 可观察 30 分钟。退出码 0 仅表示至少收到一次连接就绪和一次心跳 ACK，不表示群聊回图或稳定性通过。Ctrl+C 正常关闭连接和 HTTP 客户端。

在日志出现 `ready` 时，私聊机器人发送“测试”，或从测试群 @ 机器人发送“截图测试”，并核对 QQ 客户端图片。API 发送成功仍记为客户端可见性未验证。其他场景和意外断线重连尚待后续验证；本探针尚未提供故障注入操作。
