# 截图验证工具

本目录记录 2026-10-01 的 30 页试验。结论见项目根目录的[截图验证报告](../../截图验证报告.md)。Python 3.12 与依赖通过 uv 管理，模型凭据从根目录 `.env` 的 `DEEPSEEK_API_KEY` 读取。

## 本地人工审核页面

在项目根目录运行（不调用模型、不读取密钥）：

```bash
uv run --project validation/screenshots python validation/screenshots/review_server.py
```

浏览器打开 <http://127.0.0.1:8765>。如端口已占用，可加 `--port 8766`。

1. 确认三项收录规则，再核对争议台词的说话人。
2. 逐页核对原页；素材清单一行一格，漏选就补充，误选就删除。点击“确认原页清单”。
3. 分别选择候选归属、文字完整性、A/B 使用效果。归属正确时勾选对应清单素材，点击“确认本条”。修改意见和重复候选也会写入报告。
4. 页面审核计时手动启动，切页或隐藏页面时暂停；实际修图需用图片工具完成，修改秒数与结果可手动登记。未填写的时间不会当成零秒。
5. 点击“保存报告到项目”，生成 `human-review/review.json` 与 `human-review/人工截图审核报告.md`，并保留每次保存的 JSON 快照。下一轮操作应读取该目录；首轮助手结果不被覆盖。

当前浏览器会自动保存进度；重新启动服务时，优先恢复浏览器或项目中更新时间较新的记录。更换端口或浏览器前先保存到项目。页面也支持分别下载 Markdown 和 JSON 备份。尚未确认的项保留为待审，汇总只统计已经确认的候选；整轮未完成时不能据局部指标判定通过。

修改收录规则会取消已有的“已确认”状态，保留选择和意见供重新检查。修改某页素材清单也会取消该页候选确认，并移除失效的匹配关系，避免按照旧清单计算指标。原页标记仍有争议时，不纳入召回率基准。

服务只监听本机，只提供审核页面与所需图片。报告中的“修改后可用”是审核判断，不表示已经实际修好；实际修正图片路径可另外登记。

## 本地重算（不调用 API）

在项目根目录运行：

```bash
uv sync --project validation/screenshots
uv run --project validation/screenshots python validation/screenshots/probe.py crops
uv run --project validation/screenshots python validation/screenshots/score.py
```

`score.py` 使用 `review.csv` 中的助手视觉评分，核验源文件、响应与截图，再生成 `results/`；不会自动进行真人审核，也不会测量真人耗时。编辑评分后可重新计算。

## API 调用

以下命令会访问 DeepSeek，其中 `smoke` 与 `run` 会产生模型用量：

```bash
uv run --project validation/screenshots python validation/screenshots/probe.py models
uv run --project validation/screenshots python validation/screenshots/probe.py smoke
uv run --project validation/screenshots python validation/screenshots/probe.py run
```

`run` 读取已固定的 `artifacts/sample_manifest.json`，顺序处理；对应 `raw/` 文件已存在时跳过，不覆盖已有结果。需要复测时应另行保存一轮产物，避免与首轮混合。对已有失败记录也会跳过，复测前应明确管理该记录。

`preview` 命令只解压各卷预览页并生成联系图。最终清单和视觉对照是调用前人工选样步骤的产物，不由 `preview` 自动建立。本轮另选的第 4 卷 0067 页及最终抽样清单已保存于本地 `artifacts/`。

`results/` 保存可分享的来源元数据、逐条评分与汇总；`artifacts/` 保存漫画图片、响应和裁剪结果，`.gitignore` 将其排除。仅检出代码而没有这些本地产物，不能重算本轮结果。工具未修改原始 ZIP 或项目需求。

评分中 `gold_indexes` 使用对照页面台词列表的零起始索引，`|` 表示一条候选包含不同格的多个对照项；`direct`、`fix`、`unusable` 分别表示直接可用、修改后可能可用、当前不可用。所有质量评分均需真人确认。
