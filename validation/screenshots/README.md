# 截图验证工具

本目录记录 2026-10-01 的 30 页试验。结论见项目根目录的[截图验证报告](../../截图验证报告.md)。Python 3.12 与依赖通过 uv 管理，模型凭据从根目录 `.env` 的 `DEEPSEEK_API_KEY` 读取。

## 自动识别 → 全页人工修正

新修正工作台与原评分页面分别使用 8766、8765 端口。运行：

```bash
uv run --project validation/screenshots python validation/screenshots/editor_server.py
```

打开 <http://127.0.0.1:8766>。工具直接读取原 ZIP，覆盖全部 920 个图像文件，包含封面等需人工排除的页面，不必先解压全部原图。

- 左侧点框选择，拖动框移动，拖动四角调整；“画框追加”后在图上拖出新框。可删除、撤销，支持放大及滚动查看。
- 桌面布局左侧原图占 60%、右侧占 40%；默认“适合页面”完整显示原图，也可选“适合高度”“适合宽度”及百分比缩放（以左侧可视宽度为基准）。切页或调整窗口后自动重新适配，遮罩框与保存坐标保持一致。
- 右侧修改完整繁体台词，同格多个属于久留美的气泡合为一条；确认说话人，检查实时裁剪预览。
- “保存草稿”生成截图与记录，但不入库；“保存并完成本页复核”要求每条台词非空且说话人已确认。无素材页面删除全部框后也需点击完成复核。
- 未保存修改缓存在当前浏览器，切页后可恢复；保存到项目后，换浏览器或重启也可载入。保存修正不会改原图，也不会覆盖原评分记录。
- “生成报告与素材清单”写入 `corrections/人工修正报告.md` 和 `corrections/corrected_catalog.json`。清单只收已完成整页复核的素材；每条包含源页、台词、坐标、PNG 路径。截图在 `corrections/crops/`，逐页数据在 `corrections/pages/`，每次修正快照在 `corrections/history/`。

第二轮自动识别先做身份／逐气泡定位，再独立复核说话人、完整性和遗漏。加入真人判错的三类角色作负参考，按“分镜 + 人物 + 所有属于她的气泡”的范围并集加边距生成框；纯叫声排除，明确内心独白保留。模型归属和几何坐标仍需人工检查，不因自动成功就视为可入库。

在页面点击“继续全量自动识别”可启动／恢复处理；成功页面跳过，已完成的人工修正页面也跳过。批处理并发为 2，单阶段最多 2 次尝试、每次超时 120 秒；状态和响应保存在 `artifacts/round2/pages/`。自动识别不会覆盖人工保存的修正。切换页面可载入新到达的自动结果；已开始修改的浏览器草稿优先恢复。

命令行也可执行，避免与页面批处理同时启动：

```bash
# 只复测原 30 页；成功记录跳过
uv run --project validation/screenshots python validation/screenshots/auto_v2.py
# 全量识别，可断点继续
uv run --project validation/screenshots python validation/screenshots/auto_v2.py --all
# 本地回归检查，不访问模型
uv run --project validation/screenshots python validation/screenshots/test_corrections.py
```

旧真人评分中的清单与备注有少量不一致，新工作台会原样显示供检查；不会把旧清单自动当成最终台词。首次载入的框、实际调整、追加删除及保存版本均保留供后续评估。第二轮复测说明见[自动识别优化记录](../../自动识别优化记录.md)。

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

## OCR、说明与素材状态清单

新版工作台地址：http://127.0.0.1:8767 。当前 8766 服务仍承担已授权的全量识别；刷新旧地址会转到新版，并携带旧地址中尚未保存的浏览器草稿。两版读取同一份项目数据，升级期间只使用新版编辑。

启动新版：

```bash
UV_CACHE_DIR=/tmp/kurumibot-uv-cache uv run --project validation/screenshots python validation/screenshots/editor_server.py --port 8767
```

1. 调整、删除或追加框，确认说话人，点击“保存并完成本页框体复核”。台词可以暂空。也可勾选单条“裁剪框已复核”并保存草稿，让这条先进入批量处理。
2. 点击“一键补齐已复核素材”：仅处理项目里已保存、框与说话人已确认的素材。顺序为裁剪图 OCR → OpenCC 繁转简 → 根据图片和台词生成情绪标签、含义、场景；成功步骤下次跳过，失败步骤可重试。不需要等所有页面复核完成。
3. 右侧支持“识别当前框台词”“生成情绪与场景”、查看 OCR 原始识别和人工修改。框未确认不能调用。画框、拖动本身不调用 OCR；单条按钮会先保存本页未保存的修改。
4. “素材状态清单”显示全部已识别候选和人工新增素材，分别展示框体、OCR、标签、含义、场景及说明状态，可筛选待处理、失败和信息齐全素材，点击行中的素材按钮跳转。
5. AI 识别存疑时标记“待人工确认”，暂不生成说明；可修正台词并“确认台词准确”。AI 已生成说明与人工已确认说明分别显示。这里的“信息齐全”不代表 AI 结果已全部人工验收。

人工修改的台词不会被 OCR 覆盖，新的识别内容保留在“最新 OCR 原始识别”供对照。人工填写的标签和说明会保留，批量生成只补空缺。修改框会清除框体确认并将 OCR／说明标为待更新；修改台词会使已有说明待更新。保留的人工说明需要重新确认，批量操作不会擅自重写。

任务进度、失败信息与处理状态保存在 `corrections/enrichment_job.json` 及逐页记录。关闭浏览器不会中断服务端任务；关闭服务或重启电脑会中断，重新启动后点击批量按钮续跑。认证、余额等不可重试错误会停止整批任务。单个网络或输出错误记录为失败，继续其他素材。

`作品与角色背景.md` 是每次生成都发送的共同背景；`volume-context/01.md` 至 `06.md` 按卷号只注入对应卷背景。这些摘要的来源是官方作品、角色及卷简介，不是逐页剧情。提示词要求图片和确认台词优先，避免强行套用交易情境。

导出的 `corrected_catalog.json` 保留整页框体复核完成素材及所有处理字段，可能仍有未处理项；`ready_catalog.json` 仅含这些页面中图片、繁简台词、标签和说明齐全的素材。报告记录两者数量。仅确认了单条框的草稿可以先处理，但需完成整页复核后才进入正式导出。

验证：

```bash
UV_CACHE_DIR=/tmp/kurumibot-uv-cache uv run --project validation/screenshots python -m unittest discover -s validation/screenshots -p 'test_*.py'
```
