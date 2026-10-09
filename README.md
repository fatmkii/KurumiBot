# KurumiBot 后台

Python 3.12 / uv，正式素材库 SQLite + Codex OAuth Proxy + 腾讯 qqbot-agent-sdk 1.2.2。
当前版本响应 QQ 私聊和群里的 @ 消息，每条新消息选一张台词图片回复；普通群消息不触发回复。
管理后台提供运行概览、对话记录、AI 用量及估算费用、素材预览与修正、连接配置。

## 启动与消息测试

在项目根目录执行：

```bash
uv sync --locked
# 已有 .env 含 QQ_APP_ID、QQ_APP_SECRET、CODEX_OAUTH_PROXY_API_KEY 时无需复制。
# 首次部署才执行：cp .env.example .env，并填写密钥。
uv run --locked --env-file .env python -m kurumibot run
```

看到 `ready` 后私聊机器人，例如：

- 今天 A 股会怎么走？我想抄底！
- 我刚买完就跌了，亏麻了。
- 老板又让我加班，烦死了。
- 明天考试，现在慌得不行。
- 这次一定能赚大钱，我要梭哈！

群聊测试：把机器人加入测试群，使用 QQ 的 @ 功能选中机器人，再发送上面的语句。
程序只接收 `GROUP_AT_MESSAGE_CREATE` 与 `C2C_MESSAGE_CREATE`，使用事件中的群 openid 或用户 openid
向原会话上传和回复图片。普通群消息、频道消息、入群通知均不触发 AI 或图片回复。
群 @ 前缀从选图文本中移除；只 @ 不带文字时回复图库随机图片。
同一用户的频率限制跨会话共享（以 QQ 事件提供的用户 openid 为准），不同群和私聊按各自会话去重。
群聊实际准入、@ 事件接收及 QQ 客户端图片显示需入群后验证，不能用私聊成功代替群聊验证。

群 @ 接入后，Bot、管理接口及浏览器测试共 68 项通过；覆盖群事件解析、发送到原群、
空 @ 兜底、跨群及私聊去重、单用户限流、群发送重试和后台群聊标识。真实群聊显示仍待入群测试。

同一用户两条测试至少间隔 **5 秒**。收到 `message_finished` 且 `status=sent` 表示 QQ API 确认发送，
仍需在 QQ 客户端检查图片是否显示、台词是否接得上。Ctrl+C 正常停机。
只启动一个实例；文件锁会阻止共享历史库的第二个 Bot 实例。

选图预览（会真实调用 Codex OAuth Proxy 并记录用量，不向 QQ 发消息）：

```bash
uv run --locked --env-file .env python -m kurumibot select '我刚买完就跌了，亏麻了'
```

输出选中 ID、台词、场景、理由、图片路径、用量和耗时。首版把全部已启用且文件存在的候选文本交给模型，
不上传图片，不做在线 OCR。返回 ID 必须属于本次候选集合，发送前再次查 SQLite 的启用状态及文件。
使用 [Codex OAuth Proxy](https://github.com/dvcrn/codex-oauth-proxy) 的 OpenAI 兼容 Chat Completions 接口，
地址固定为 `http://127.0.0.1:9879/v1`，默认模型 `gpt-6-luna-low`，请求 JSON 输出。
启动 Bot 前需先启动代理；`CODEX_OAUTH_PROXY_API_KEY` 应与代理的 `ADMIN_API_KEY` 一致。
可通过代理的 `/v1/models` 查询可用模型，用 `CODEX_OAUTH_PROXY_MODEL` 更换。

## 配置及运行规则

密钥只放 `.env`，参考 `.env.example`；修改后需重启。兼容原探针的 `QQBOT_APP_ID` / `QQBOT_CLIENT_SECRET`。
相对路径均以项目根目录为基准。兜底从已启用且文件存在的图库图片中随机选择，优先避开该会话最近 10 次回复的图片。

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `CODEX_OAUTH_PROXY_API_KEY` | 无 | 本机代理的 API 密钥 |
| `CODEX_OAUTH_PROXY_MODEL` | `gpt-6-luna-low` | 代理支持的模型 ID，可包含推理强度后缀 |
| `KURUMI_LIBRARY` | `materials/library-v1` | 含 SQLite 和 images 的素材目录 |
| `KURUMI_DEFAULT_IMAGE` | `materials/library-v1/images/v06-p088-manual-9c6ecdf5-9300-4006-8a48-c51279382a9e.png` | 兼容旧配置，回复不再使用固定兜底 |
| `KURUMI_HISTORY` | `data/history.sqlite3` | 对话及用量数据库 |
| `KURUMI_AI_TIMEOUT` | `30` | AI 请求总超时（秒） |
| `KURUMI_SEND_TIMEOUT` | `60` | 每次上传 + 发送总超时（秒） |
| `KURUMI_AI_CONCURRENCY` | `2` | 同时处理消息上限，包括选图和发送 |
| `KURUMI_USER_INTERVAL` | `5` | 同一用户处理间隔（秒） |
| `KURUMI_SEND_ATTEMPTS` | `2` | 图片上传/发送最多尝试次数（1～3） |

空消息、无匹配、AI 超时/请求失败/无效输出、选中素材停用或文件缺失均走图库随机兜底。
图库无可用图片时记录 `no_available_images`，不发送图片并继续服务。
每次选图向 AI 提供同一私聊或群聊最近 10 次成功回复（留言、图片 ID、场景、理由及兜底原因），
按从旧到新排列，重启后仍可读取。失败、限流和处理中记录不计入；不同会话互不混用。
提示词鼓励荒诞联想、反差和跨场景玩梗，优先避免近期重复，明显更贴切时仍允许重复。
AI 校验失败会保留固定原因：`invalid_selection_id`（返回 ID 不在候选中）、
`invalid_selection_metadata`（场景或理由字段无效）、`selection_output_truncated`（输出达到长度限制）、
`incomplete_selection`（返回未正常结束）。对话的兜底原因会加 `ai_` 前缀；旧的 `ai_ValueError` 记录无法还原具体校验项。
限流及并发超限直接跳过并记录，不调用 AI 或回复。发送失败有限重试，沿用同一入站 `msg_id` 和 `msg_seq=1`；
接口超时可能出现已发送但未确认的情况，应用无法保证网络故障下平台的严格 exactly-once。

`conversations` 唯一约束按渠道、会话及入站消息 ID 去重，覆盖重连和重启。
已经处理、失败、跳过或进程中断的消息均不会自动重新选图/回复；需要发一条新消息重试。
SDK 管理 WebSocket 心跳及自动重连，会话状态仅在当前进程保存。长期稳定性及群聊尚待实测。
停止时先关闭 WebSocket，给处理中消息 5 秒收尾，随后取消并记录中断。

## 查看处理记录

`data/history.sqlite3` 的 `conversations` 保存留言、素材 ID、图片路径、兜底原因、发送状态、次数和总耗时；
`ai_usage` 保存模型、候选数、场景判断、选图理由、实际 token 用量、AI 耗时和失败类型。
`select` 预览的用量记录 `conversation_id` 为空。管理页在供应商返回用量时记录 token；Codex 模型不套用 DeepSeek 费率，费用显示未知。
用户、会话及消息标识以哈希保存。对话正文会存储在本机历史库中；不要提交或公开 `data/`。

```bash
uv run --locked python - <<'PY'
import sqlite3
db = sqlite3.connect('data/history.sqlite3')
db.row_factory = sqlite3.Row
for row in db.execute('SELECT id,content,status,material_id,fallback_reason,error_type,elapsed_seconds FROM conversations ORDER BY id DESC LIMIT 20'):
    print(dict(row))
for row in db.execute('SELECT conversation_id,model,scene,reason,usage_json,elapsed_seconds,error_type FROM ai_usage ORDER BY id DESC LIMIT 20'):
    print(dict(row))
PY
```

控制台只输出受控结构化记录，不输出留言正文、原始 QQ 标识、密钥、token、上传凭证或异常正文。
SDK 原始日志关闭。`data/` 和 `.env` 已忽略。

## 验证与部署

```bash
uv run --locked pytest
```

默认测试包括 Playwright 浏览器验证，需先安装 Chromium：`uv run --locked playwright install chromium`。
只跑 Bot 与管理后台接口测试可执行 `uv run --locked pytest -m 'not browser'`。
使用系统现有 Chromium 时可设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 为其可执行文件绝对路径。
接口与浏览器测试在本机回环地址启动临时服务，仅使用临时素材库、历史库和虚构凭据。
测试覆盖持久去重、选图响应校验、AI 失败及超时、兜底、发送前素材重检、限流、并发和有限发送重试，
以及后台登录、权限校验、配置保存、素材编辑、手机与桌面布局。
QQ 客户端显示及匹配趣味性需要人工私聊及群 @ 验证。

2026-10-04 开发验证：22 项自动化测试通过，正式图库 245 条均可读取。
真实 DeepSeek 预览中，“我刚买完就跌了，亏麻了”选到“开什么玩笑！！是谁说的能涨的！！还我2000万円！！”，
“老板又让我加班，烦死了”选到“呜呜…我想辞职…”，各耗时约 1.7 秒。
首版全量候选请求约 4.95 万输入 token，实际用量已入库；待私聊反馈后再决定是否缩小候选范围。
正式后台已实测 WebSocket READY、心跳 ACK 及正常退出，用户已确认私聊图片回复正常。
2026-10-04 管理后台验证完成，Bot、API 及浏览器测试共 49 项通过。
正式数据页面已通过真实浏览器只读检查，桌面及 375px 手机截图位于本机 `data/admin-preview/`。

### Ubuntu 一键部署

适用于运行 systemd 的 Ubuntu 22.04 / 24.04 服务器。使用有 sudo 权限的普通用户克隆并运行，
Bot 与后台也使用该用户；项目目录需允许该用户读写。首次部署：

```bash
git clone <你的仓库地址> KurumiBot
cd KurumiBot
cp .env.example .env
nano .env
# 填好 QQ_APP_ID、QQ_APP_SECRET、CODEX_OAUTH_PROXY_API_KEY 后执行：
bash deploy/install.sh
```

若服务器没有 `192.168.x.x` 网卡，在 `.env` 增加 `KURUMI_ADMIN_HOST=服务器实际私有IPv4`。
脚本不会自动开放防火墙；访问后台时需确保访问端到该地址的 TCP 10963 可达。
Bot 只需要出站 HTTPS / WebSocket 连接。

脚本仅在系统依赖缺失时调用 apt 安装，已安装 ca-certificates、curl、Supervisor 时跳过 apt，
避免被系统中其他待配置的软件包阻塞。依赖安装失败则停止并保留 apt/dpkg 错误，需先修复系统软件包状态。
脚本安装 uv 和 Python 3.12，按 `uv.lock` 安装生产依赖，校验素材压缩包的 SHA256，
自动解压到 `data/materials/library-v1`，并将 `.env` 中原有默认素材及兜底路径迁移到该目录。
已有默认素材数据库的人工修改通过 SQLite backup 保留；自定义素材路径则仅校验，不迁移。
之后检查必填密钥、已启用素材的图片及后台网卡地址，生成后台密码与 Supervisor 配置，
启动 `kurumibot` 和 `kurumibot-admin`，设为开机自启及异常退出自动重启。
uv 安装方式参考 [官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)。

访问脚本输出的 `http://服务器IP:10963`，用户名默认 `admin`；密码查看本机 `.env` 的
`KURUMI_ADMIN_PASSWORD`。`.env` 权限设为 600，日志不打印密钥或密码。
Supervisor 的 RUNNING 表示进程已启动；QQ 是否连通还需查看 Bot 日志中的 `ready`，然后发送私聊或群 @ 测试。

```bash
sudo supervisorctl status kurumibot kurumibot-admin
sudo supervisorctl tail kurumibot
sudo supervisorctl tail kurumibot-admin
# 后台修改连接配置后，重启 Bot 生效：
sudo supervisorctl restart kurumibot
# 停止两个进程：
sudo supervisorctl stop kurumibot kurumibot-admin
```

更新代码后再次运行脚本即可重新同步依赖并重启两个服务：

```bash
git pull --ff-only
bash deploy/install.sh
```

重复部署不会覆盖 `data/materials/library-v1`，不会重置后台密码，也不会清空历史记录。
升级素材包需另行迁移，脚本不会自动用新包替换已经编辑过的运行库。
部署前勿同时手动运行另一份 Bot。请备份 `.env` 和整个 `data/`，不要只备份仓库内的发布数据库。
Supervisor 配置安装在 `/etc/supervisor/conf.d/kurumibot.conf`，两个日志位于
`/var/log/kurumibot.log` 和 `/var/log/kurumibot-admin.log`，每个日志最多 10 MB、保留 3 份轮转文件。

`.gitignore` 排除 `.env` 及其备份、虚拟环境、Python 缓存、运行数据、日志、SQLite 临时文件、
原漫画及解压后的发布图片；`.env.example`、`uv.lock`、部署脚本、发布 ZIP 与校验文件保留在仓库。
因此新服务器无需手工上传图片，也不会把后台编辑和对话记录带入版本控制。

## 管理后台

管理页与 Bot 是独立进程，不会因页面重启而中断私聊。

```bash
# 自动寻找本机实际的 192.168.x.x 地址并监听 10963。
uv run --locked --env-file .env python -m kurumibot.admin
# 或明确指定本机实际的 IPv4（WSL 开发环境示例，地址可能随重启变化）。
uv run --locked --env-file .env python -m kurumibot.admin --host 172.19.124.247
```

也可在 `.env` 设置 `KURUMI_ADMIN_HOST`。没有 192.168 地址且未指定 host 时拒绝启动，
不会自动改为监听所有网卡。服务固定使用 10963 端口，地址必须是实际可绑定的私有 IPv4。
在浏览器访问 `http://服务器IP:10963`。

首次启动会为缺失的 `KURUMI_ADMIN_USERNAME` / `KURUMI_ADMIN_PASSWORD` 自动补值。
默认用户名 `admin`，随机密码仅保存在 `.env` 的 `KURUMI_ADMIN_PASSWORD`，启动日志不打印密码。
打开本机 `.env` 取得密码；也可手动设定用户名密码，然后重启管理页。
网页登录会话有效期 8 小时，重启管理页或退出登录使会话失效。

五个页面：

- **运行概览**：启用素材数、消息处理情况、AI 累计 token、估算费用、最近回复和各卷素材分布。
  Bot 状态依据进程锁显示，仅表示进程运行，不能代替 QQ 连接状态检查。
- **对话记录**：按留言或发送状态筛选，预览实际回复图，查看场景、选图理由、耗时、重试次数及失败或兜底原因。
- **AI 用量**：显示私聊、群 @ 与命令行预览的模型、实际输入/输出 token、缓存命中、耗时和失败原因。
  历史 DeepSeek 调用按 [2026-10-04 官方报价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)
  的高峰费率保守估算，未包含低峰折扣；历史记录也采用这份报价，仅供参考，不是供应商账单。
  没有 token 用量或未知模型的调用显示费用未知；累计值不包含这些调用。
- **台词素材库**：按台词、情绪、场景、卷号及启用状态筛选，预览原图，修正繁体与简体台词、情绪标签、含义、场景及启用状态。
  修改直接写入运行时 `library.sqlite3`，下一条新消息立即使用；不修改原图裁剪与追溯信息。
- **连接与配置**：填写 Codex OAuth Proxy 模型 ID，更新代理 API 密钥、QQ App ID 和 Client Secret。
  只显示脱敏值，输入框始终为空；留空保留已有值。修改保存到 `.env`，需另行重启 Bot 后生效。

页面提供刷新按钮读取最新记录，时间按浏览器本地时区显示。
图片及数据接口需要登录；写接口校验会话和 CSRF token，并拦截跨来源请求。
登录失败提示不区分用户名和密码，登录尝试设有频率上限。
配置写入会保留其他环境变量及注释，原子替换 `.env` 并设权限为 600，不输出秘密值。
服务器运行用户需有权限读写 `.env`、历史库及素材 SQLite。

修改素材会使发布包原有的数据库校验值失效；`catalog.json` 仍是发布快照，不会同步更新。
不要重新解压旧发布包覆盖编辑后的库。迁移时应备份当前 SQLite，Bot 与管理页使用相同的素材路径和历史路径。
一键部署会为 `kurumibot` 与 `kurumibot-admin` 生成 Supervisor 配置，两个服务可分别管理。
