# KurumiBot 正式素材库

首版素材库 `library-v1` 于 2026-10-04 发布，按用户确认的最新保存结果纳入 **245 条**，初始全部启用。

| 卷号 | 素材数 |
| --- | ---: |
| 1 | 83 |
| 2 | 47 |
| 3 | 31 |
| 4 | 28 |
| 5 | 30 |
| 6 | 26 |

`library-v1.zip` 为部署包，约 92.4 MiB；将其完整解压到服务器即可供后续 Bot 读取。
包内包括 245 张 PNG、SQLite 数据库、JSON 清单、版本信息、文件校验值及读取示例。
数据库字段和运行时读取方法见 [库内说明](library-v1/README.md)。

确认保留 v01-p041 的人工台词“放棄FX”；第 3 卷封面已删除，不入库。
打包时最新数据中全部素材所在页面均已完成复核，920 页已复核；素材信息均齐全。
原始台词、裁剪框、标签、含义与场景保留保存结果，不再发起 AI 请求。
不包含漫画整页、历史记录、工作台代码、试验数据、API 密钥或 `.env`。

已验证：245 条唯一 ID、245 张有效图片、SQLite 完整性、全部启用素材可读取、数据与最新保存结果一致，以及解压后的全部 249 个文件校验值。
打包相关及现有测试共 27 项通过；Bot 消息收发接入留待后续开发。
工作台修正清单也已重新导出为 245 条可用素材。

部署前可验证压缩包：

```bash
cd materials
sha256sum -c library-v1.zip.sha256
unzip library-v1.zip
cd library-v1
sha256sum -c SHA256SUMS
```

`library.sqlite3` 是运行时主数据；后台修改不会自动同步到发布时的 JSON 快照。
后续工作台修改也不会影响本次发布包，需要重新发布。不要覆盖运行服务器上已人工编辑的数据库。
默认反应图尚未指定，接入 Bot 时需单独选定。

从项目根目录重新发布新版本：

```bash
UV_CACHE_DIR=/tmp/kurumibot-uv-cache uv run --project validation/screenshots python validation/screenshots/package_library.py --output materials/library-v2
```

脚本拒绝覆盖已有版本，并在有未完成素材或未完成整页复核时停止；经明确确认，可使用 `--include-reviewed-frames` 纳入已逐框复核、信息齐全的素材。
