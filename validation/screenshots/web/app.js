const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const roles = {correct:'是久留美，且对白属于她',wrong:'角色或说话人错误',mixed_speakers:'混入其他人的对白',no_visible_character:'没有足够可辨认的久留美',uncertain:'仍无法确定，留待复核'};
const qualities = {direct:'直接可用',fix:'修改后可用',unusable:'不可用'};
const quotes = {correct:'完整且正确',incorrect:'漏字、错字或没有合并完整对白',uncertain:'暂时无法确定',na:'归属不正确／不确定，不评价'};
const ruleDefs = [
  {id:'partial',title:'局部人物算不算素材？',text:'眼睛特写、背影、头发或手部，可能能确认身份，但未必适合单独回复。',options:{recognizable:'单独看截图能认出久留美，就可以',face:'必须有清楚可辨的脸才收录'},examples:[['v06-p069','第 6 卷 0069 页'],['v05-p078','第 5 卷 0078 页']]},
  {id:'reaction',title:'口头反应如何处理？',text:'“嗚”“嗚哇燙燙燙”可能是口头表达；背景音效仍应排除。',options:{include:'角色发出的口头反应也收录',exclude:'不收纯叫声，只收有语义的台词'},examples:[['v05-p146','第 5 卷 0146 页']]},
  {id:'thought',title:'内心独白是否收录？',text:'当前助手对照包含明确属于久留美的内心独白。',options:{include:'收录明确属于她的内心独白',exclude:'只收她实际说出口的对白'},examples:[['v02-p085','第 2 卷 085 页'],['v04-p068','第 4 卷 0068 页']]}
];
let data, state, tab = 'rules', pageIndex = 0, candidateIndex = 0, timer = null, timerStarted = 0, toastTimeout;
let storageKey, storageOK = true;
const page = () => data.pages[pageIndex];
const pageReview = () => state.pages[page().id];
const candidate = () => page().candidates[candidateIndex];
const candidateReview = () => state.candidates[candidate().id];
const lines = (s) => s.split('\n').map(x => x.trim()).filter(Boolean);
const options = (values, selected) => `<option value="">请选择…</option>` + Object.entries(values).map(([v,t]) => `<option value="${v}" ${v === selected ? 'selected' : ''}>${esc(t)}</option>`).join('');
const select = (name, title, values, value) => `<label class="field"><span>${title}</span><select data-field="${name}">${options(values,value)}</select></label>`;

function notify(text) { $('#toast').textContent = text; $('#toast').style.display = 'block'; clearTimeout(toastTimeout); toastTimeout = setTimeout(() => $('#toast').style.display = 'none', 4500); }
function persist() {
  state.updated_at = new Date().toISOString();
  try { localStorage.setItem(storageKey, JSON.stringify(state)); storageOK = true; }
  catch { storageOK = false; }
  $('#save-status').textContent = storageOK ? '进度已存到当前浏览器 · 报告需点击保存' : '浏览器无法保存，请及时保存到项目';
  updateProgress();
}
function validCandidate(c) {
  const r = state.candidates[c.id], p = state.pages[c.page_id];
  return r.confirmed && !!roles[r.role] && !!quotes[r.quote_status] && !!qualities[r.A] && !!qualities[r.B]
    && (r.role !== 'correct' || (r.quote_status !== 'na' && r.matches.length > 0 && r.matches.every(q => lines(p.gold).includes(q))))
    && (r.role === 'correct' || (r.A === 'unusable' && r.B === 'unusable' && r.quote_status === 'na'))
    && (r.modification_seconds === '' || (!!r.modification_result && Number.isFinite(Number(r.modification_seconds)) && Number(r.modification_seconds) >= 0));
}
function allCandidates() { return data.pages.flatMap(p => p.candidates); }
function updateProgress() {
  $('#rule-progress').textContent = `${Object.values(state.rules).filter(Boolean).length}/3`;
  $('#page-progress').textContent = `${Object.values(state.pages).filter(r => r.confirmed).length}/30`;
  $('#candidate-progress').textContent = `${allCandidates().filter(validCandidate).length}/83`;
  if ($('#page-badge')) {$('#page-badge').textContent=pageReview().confirmed?'原页已确认':'原页待确认';$('#page-badge').classList.toggle('warning',!pageReview().confirmed);}
  if ($('#page-confirm-status')) $('#page-confirm-status').textContent=pageReview().confirmed?'已确认':'还未确认';
  if ($('#confirm-page')) $('#confirm-page').textContent=pageReview().confirmed?'重新确认原页清单':'确认原页清单';
  if ($('#confirm-candidate')) $('#confirm-candidate').textContent=validCandidate(candidate())?'已确认 · 保存修改':'确认本条';
}
function render() {
  document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  if (tab === 'rules') renderRules();
  else if (tab === 'review') renderReview();
  else renderReport();
  updateProgress();
}
function renderRules() {
  $('#main').innerHTML = `<div class="heading"><div><p class="eyebrow">STEP 01 / CRITERIA</p><h2>先统一“什么值得收录”</h2><p class="muted">这些选择会写入报告，作为下一轮提取与评审的依据。</p></div><span class="badge">所有选择初始为空</span></div>
    <div class="rule-grid">${ruleDefs.map((r,i) => `<section class="card"><div class="rule-number">0${i+1}</div><h3>${r.title}</h3><p class="muted">${r.text}</p><div class="choices">${Object.entries(r.options).map(([v,t]) => `<label class="choice"><input type="radio" name="${r.id}" data-rule="${r.id}" value="${v}" ${state.rules[r.id] === v ? 'checked' : ''}><span>${t}</span></label>`).join('')}</div><div class="divider"></div>${r.examples.map(([id,label]) => `<button data-jump="${id}">${label} ↗</button>`).join(' ')}</section>`).join('')}</div>
    <div class="callout"><b>还有三处说话人需要你判断。</b><p>第 2 卷 039 页“這樣真的很可惜哦”、100 页“沒辦法的…”、123 页“讓這傢伙消失吧！”。123 页的“那麼這樣好了！”也请一并核对。看不清时可查原 ZIP 的前后页；仍不能确定就保留待复核。</p><div class="toolbar">${['v02-p039','v02-p100','v02-p123'].map(id => `<button data-jump="${id}">${id} ↗</button>`).join('')}</div></div>
    <div class="card"><h3>按这个顺序进行</h3><p>① 查看原页，修正素材清单：一行代表一格的完整对白，漏选就补一行。</p><p>② 对照候选，分别判断说话人、文字和 A/B 截图，再点击“确认本条”。</p><p>③ 汇总中查看未完成项与修正任务，保存报告供下一轮操作。</p><p class="inline-note">A 保持严格一格；B 允许相邻分镜，但仍须保留可辨人物、完整对白，适合单独回复。</p><button class="primary" data-tab="review">开始逐页审核 →</button></div>`;
}
function renderReview() {
  const p = page(), r = pageReview(), c = candidate();
  $('#main').innerHTML = `<div class="heading"><div><p class="eyebrow">STEP 02 / PAGE ${pageIndex+1} OF 30</p><h2>第 ${p.volume} 卷 · ${esc(p.source_file)}</h2><p class="muted">先核对原页清单，再判断这一页的 ${p.candidates.length} 条候选。</p></div><span id="page-badge" class="badge ${r.confirmed?'':'warning'}">${r.confirmed?'原页已确认':'原页待确认'}</span></div>
    <div class="toolbar card"><label>切换页面 <select id="page-select">${data.pages.map((p,i) => `<option value="${i}" ${i===pageIndex?'selected':''}>${state.pages[p.id].confirmed?'✓ ':''}第 ${p.volume} 卷 / ${p.source_file} · ${p.candidates.length} 条候选</option>`).join('')}</select></label><button id="prev-page" ${pageIndex===0?'disabled':''}>上一页</button><button id="next-page" ${pageIndex===29?'disabled':''}>下一页</button><button id="timer-button">${timer?'暂停':'开始'}页面审核计时</button><span class="timer" id="timer-display">${r.review_seconds.toFixed(1)} 秒</span></div>
    <div class="page-layout"><section class="card source"><h3>原页 <span class="badge">点击放大</span></h3><img src="/${esc(p.path)}" alt="${p.id} 漫画原页" data-zoom="/${esc(p.path)}"><div class="image-caption"><span>${p.id} · ${p.dimensions.join(' × ')}</span><a href="/${esc(p.path)}" target="_blank" rel="noopener">新窗口打开 ↗</a></div></section>
    <div><section class="card"><h3>这页应该有哪些素材？</h3><p class="inline-note">以下文字来自助手初评，尚未经过你确认。只列按所选规则可收录的素材，每行一格，合并该格属于久留美的所有气泡；没有素材就留空。</p><label class="field"><span>完整素材清单</span><textarea id="gold" rows="${Math.max(3,lines(r.gold).length+1)}">${esc(r.gold)}</textarea></label>
      ${p.baseline.uncertain_quotes?.length ? `<div class="callout"><b>请重点确认说话人</b><p>${p.baseline.uncertain_quotes.map(esc).join(' ／ ')}</p><small>确认属于久留美且符合规则时，补入上面的清单。</small></div>` : ''}
      <p class="inline-note">原初评说明：${esc(p.baseline.notes)}</p><label class="field"><span>原页复核意见／仍不确定的内容</span><textarea id="page-notes" rows="2">${esc(r.notes)}</textarea></label><label class="checkline"><input type="checkbox" id="page-uncertain" ${r.uncertain?'checked':''}>这页仍有影响素材清单的不确定项</label><div class="footer-actions"><button class="primary" id="confirm-page">${r.confirmed?'重新确认原页清单':'确认原页清单'}</button><span id="page-confirm-status" class="muted">${r.confirmed?'已确认':'还未确认'}</span></div></section>
      <section class="card"><h3>逐条判断候选</h3><div class="candidate-tabs">${p.candidates.map((c,i) => `<button data-candidate="${i}" class="${i===candidateIndex?'selected':''} ${validCandidate(c)?'done':''}">候选 ${i+1}</button>`).join('')}</div>${c ? candidateForm(c) : '<div class="empty">模型没有输出候选。请检查原页是否确实没有素材；如有漏选，补到上方清单。</div>'}</section></div></div>`;
}
function candidateForm(c) {
  const r = candidateReview(), gold = lines(pageReview().gold);
  return `<p class="eyebrow">${c.id}</p><div class="quote">${esc(c.candidate.quote)}</div><p class="inline-note">候选适用场景：${esc(c.candidate.scene)}</p><div class="crop-grid">${['A','B'].map(v => `<figure>${c.crops[v].path ? `<img src="/${esc(c.crops[v].path)}" alt="候选 ${candidateIndex+1} 的 ${v} 截图" data-zoom="/${esc(c.crops[v].path)}">` : '<p>裁图失败</p>'}<figcaption>${v==='A'?'A · 严格一格':'B · 独立截图'}　点击放大</figcaption></figure>`).join('')}</div>
    ${select('role','1. 角色与说话人是否正确？',roles,r.role)}
    ${r.role==='correct' ? `<div class="field"><span>它找到了原页清单中的哪条素材？</span><small>部分台词也可算检出；若误合并不同格，可选多条。重复候选指向同一素材。</small><div class="matches">${gold.length ? gold.map(q => `<label><input type="checkbox" data-match="${esc(q)}" ${r.matches.includes(q)?'checked':''}><span>${esc(q)}</span></label>`).join('') : '请先在上方补充原页素材清单。'}</div></div>` : ''}
    ${select('quote_status','2. 模型文字是否完整正确？',quotes,r.quote_status)}<div class="field-grid">${select('A','3. A 截图的使用效果',qualities,r.A)}${select('B','4. B 截图的使用效果',qualities,r.B)}</div>
    <label class="field"><span>修改建议／判断依据</span><textarea data-field="notes" placeholder="例如：对白属于芽吹；向左扩大裁剪框保留妈妈气泡。">${esc(r.notes)}</textarea></label>
    <label class="field"><span>是否重复了本页另一条候选？</span><select data-field="duplicate_of"><option value="">不重复／未确定</option>${page().candidates.filter(x=>x.id!==c.id).map(x=>`<option value="${x.id}" ${r.duplicate_of===x.id?'selected':''}>${x.id}</option>`).join('')}</select></label>
    <details><summary>实际修改计时（可选；尚未修改就留空）</summary><p class="inline-note">本页面负责判断，不提供裁剪编辑。用自己的图片工具修改后，记录实际耗时、成功与否和修正图片路径；包括最后检查与保存的时间。</p><div class="field-grid"><label class="field"><span>实际修改耗时／秒</span><input type="number" min="0" step="0.1" data-field="modification_seconds" value="${esc(r.modification_seconds)}"></label>${select('modification_result','修改结果',{success:'修改成功',failed:'尝试后仍不可用'},r.modification_result)}</div><label class="field"><span>修正图片路径（可选）</span><input type="text" data-field="repair_path" value="${esc(r.repair_path)}"></label></details>
    <details><summary>查看助手初评（仅供参考）</summary><p>${esc(c.assistant_review.notes)}</p><p>归属：${esc(roles[c.assistant_review.role_status])}；A：${esc(qualities[c.assistant_review.A])}；B：${esc(qualities[c.assistant_review.B])}</p></details>
    <div class="footer-actions"><button id="confirm-candidate" class="primary">${validCandidate(c)?'已确认 · 保存修改':'确认本条'}</button><button id="next-pending">下一条未确认 →</button></div>`;
}
function pauseTimer() {
  if (!timer) return;
  state.pages[timer].review_seconds += (performance.now()-timerStarted)/1000;
  timer = null; persist();
}
function gotoPage(i) { pauseTimer(); pageIndex = i; candidateIndex = 0; tab = 'review'; render(); }
function summary() {
  const reviewed = allCandidates().filter(validCandidate), correct = reviewed.filter(c=>state.candidates[c.id].role==='correct');
  const confirmedPages = data.pages.filter(p=>state.pages[p.id].confirmed && !state.pages[p.id].uncertain);
  const matched = new Set(), gold = confirmedPages.flatMap(p=>lines(state.pages[p.id].gold).map(q=>`${p.id}\n${q}`));
  for (const c of correct) if (confirmedPages.some(p=>p.id===c.page_id)) for (const q of state.candidates[c.id].matches) matched.add(`${c.page_id}\n${q}`);
  const modifications = reviewed.map(c=>state.candidates[c.id]).filter(r=>r.modification_seconds!=='' && r.modification_result);
  const rate = (n,d) => d ? `${n} / ${d} = ${(100*n/d).toFixed(1)}%` : '未测量';
  return {reviewed,correct,confirmedPages,matched,gold,modifications,rate,
    precision:rate(correct.length,reviewed.length),recall:rate(matched.size,gold.length),
    quote:rate(correct.filter(c=>state.candidates[c.id].quote_status==='correct').length,correct.length),
    A:rate(reviewed.filter(c=>state.candidates[c.id].A==='direct').length,reviewed.length),
    B:rate(reviewed.filter(c=>state.candidates[c.id].B==='direct').length,reviewed.length)};
}
function reportText() {
  const s = summary(), all = allCandidates(), ruleCount = Object.values(state.rules).filter(Boolean).length;
  const complete = ruleCount===3 && data.pages.every(p=>state.pages[p.id].confirmed && !state.pages[p.id].uncertain) && s.reviewed.length===all.length && !s.reviewed.some(c=>state.candidates[c.id].role==='uncertain' || state.candidates[c.id].quote_status==='uncertain');
  const md = x => String(x ?? '').replace(/\|/g,'／').replace(/[\r\n]+/g,' ');
  const out = ['# 人工截图审核报告','',`生成时间：${new Date().toLocaleString('zh-CN')}。样本：30 页、83 条候选。`,`状态：${complete?'全部确认':'部分完成／仍有待确认项'}。真人原页确认 ${Object.values(state.pages).filter(r=>r.confirmed).length}/30；候选确认 ${s.reviewed.length}/83。`,'',
    '## 评审规则','',...ruleDefs.map(r=>`- ${r.title} ${r.options[state.rules[r.id]] || '尚未选择'}`),'',
    '## 真人指标','', '| 指标 | 结果 |','| --- | --- |',`| 候选准确率 | ${s.precision} |`,`| 检出召回率 | ${s.recall} |`,`| 台词完整正确率 | ${s.quote} |`,`| A 直接可用率 | ${s.A} |`,`| B 直接可用率 | ${s.B} |`,'',
    '准确率与裁图指标只统计已确认候选；不确定归属计入分母、不计正确。召回率只统计已确认且没有清单争议的原页，以完整对白为单位去重；候选仅检出部分对白也可匹配，不能视为可直接入库。未完成时不得据此判断整轮通过。','',
    '## 人工时间','',`- 页面审核：${Object.values(state.pages).filter(r=>r.review_timed).length} 页有计时记录；${Object.values(state.pages).some(r=>r.review_timed)?`累计 ${Object.values(state.pages).reduce((a,r)=>a+r.review_seconds,0).toFixed(1)} 秒`:'尚未测量'}（仅包括手动启动计时的部分，切页／隐藏页面自动暂停）。`,
    `- 已登记修改：${s.modifications.length} 条；${s.modifications.length ? `平均 ${(s.modifications.reduce((a,r)=>a+Number(r.modification_seconds),0)/s.modifications.length).toFixed(1)} 秒／条，失败尝试亦计入。` : '尚未测量修改时间。'}`,
    '- 修改时间由审核者填写；本页面不自动裁图，未填写不当作零秒。','', '## 原页清单与漏选补查',''];
  for (const p of data.pages) {
    const r=state.pages[p.id], human=lines(r.gold);
    out.push(`### ${p.id} / ${p.source_file}`,`状态：${r.confirmed?'已确认':'未确认'}${r.uncertain?'，仍有清单争议':''}；${r.review_timed?`计时 ${r.review_seconds.toFixed(1)} 秒`:'未计时'}。`,`原页：artifacts/pages/${p.id}.jpg`,r.notes?`复核意见：${r.notes}`:'复核意见：无','素材清单：',...human.map(q=>`- ${q}`));
    if (!human.length) out.push('- 无可收录素材');
    if (r.confirmed && !r.uncertain) for (const q of human) if (!s.matched.has(`${p.id}\n${q}`)) out.push(`- 待补查／未匹配：${q}（可能是漏选，也可能对应候选尚未审核）`);
    out.push('');
  }
  out.push('## 逐条审核结果','','| 候选 | 归属 | 文字 | A | B | 重复对象 | 修改秒数／结果 | 意见 |','| --- | --- | --- | --- | --- | --- | --- | --- |');
  for (const c of all) {
    const r=state.candidates[c.id];
    out.push(`| ${c.id} | ${validCandidate(c)?md(roles[r.role]):'未确认'} | ${md(quotes[r.quote_status])} | ${md(qualities[r.A])} | ${md(qualities[r.B])} | ${md(r.duplicate_of)} | ${r.modification_seconds!==''?md(`${r.modification_seconds} / ${r.modification_result==='success'?'成功':r.modification_result==='failed'?'失败':'未登记结果'}`):'未测量'} | ${md(r.notes)} |`);
  }
  out.push('','## 下一轮操作清单','');
  if (ruleCount<3) out.push('- 补全尚未选择的收录规则。');
  for (const p of data.pages) if (!state.pages[p.id].confirmed || state.pages[p.id].uncertain) out.push(`- 确认原页 ${p.id} 的素材清单及争议内容。`);
  for (const c of all) {
    const r=state.candidates[c.id];
    if (!validCandidate(c)) out.push(`- 待审核：${c.id}。`);
    else if (r.role!=='correct') out.push(`- ${r.role==='uncertain'?'确认归属':'角色／归属改进样例'}：${c.id}，${roles[r.role]}。${r.notes}`);
    else if (r.quote_status!=='correct' || r.A!=='direct' || r.B!=='direct' || r.duplicate_of) out.push(`- 修正／复测：${c.id}，文字=${quotes[r.quote_status]}，A=${qualities[r.A]}，B=${qualities[r.B]}${r.duplicate_of?`，重复 ${r.duplicate_of}`:''}。${r.notes}${r.repair_path?` 修正图片：${r.repair_path}`:''}`);
  }
  out.push('- 根据真人修改后的清单补查未匹配素材；复测保留原失败样本，并另选新增样本，分别报告。','- 下一轮结果另存，不覆盖首轮助手初评。','', '完整匹配关系、未确认选择与修改记录见同目录 review.json。');
  return out.join('\n')+'\n';
}
function renderReport() {
  const s = summary();
  $('#main').innerHTML = `<div class="heading"><div><p class="eyebrow">STEP 03 / HANDOFF</p><h2>把判断变成下一轮任务</h2><p class="muted">未确认项会明确保留，报告不会把部分完成当成整轮通过。</p></div><span class="badge ${s.reviewed.length===83?'':'warning'}">${s.reviewed.length} / 83 条已确认</span></div>
    <div class="metrics"><div class="metric"><strong>${s.reviewed.length}/83</strong><span>候选确认进度</span></div><div class="metric"><strong>${s.reviewed.length?(100*s.correct.length/s.reviewed.length).toFixed(1)+'%':'—'}</strong><span>已确认候选准确率</span></div><div class="metric"><strong>${s.reviewed.length?(100*s.reviewed.filter(c=>state.candidates[c.id].A==='direct').length/s.reviewed.length).toFixed(1)+'%':'—'}</strong><span>A 直接可用率</span></div><div class="metric"><strong>${s.reviewed.length?(100*s.reviewed.filter(c=>state.candidates[c.id].B==='direct').length/s.reviewed.length).toFixed(1)+'%':'—'}</strong><span>B 直接可用率</span></div></div>
    <div class="card"><div class="toolbar"><button id="download-md" class="primary">下载 Markdown 报告</button><button id="download-json">下载 JSON 审核记录</button><button data-tab="review">继续审核</button></div><p class="inline-note">“保存报告到项目”会写入 human-review/，保留历史 JSON 快照；下次打开可继续。下载文件可额外备份。</p></div><div class="card"><h3>报告预览</h3><pre id="report-preview" class="report-text"></pre></div>`;
  $('#report-preview').textContent=reportText();
}
function exportState() { return {...state, exported_at:new Date().toISOString(), reviewer:'human', schema_version:1}; }
function download(name, text, type) {
  const url=URL.createObjectURL(new Blob([text],{type})), a=document.createElement('a'); a.href=url; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function nextPending() {
  const all=allCandidates(), index=all.findIndex(c=>c.id===candidate()?.id);
  const remaining=[...all.slice(index+1),...all.slice(0,index+1)].find(c=>!validCandidate(c));
  if (!remaining) {pauseTimer();tab='report';render();return;}
  const i=data.pages.findIndex(p=>p.id===remaining.page_id); pauseTimer(); pageIndex=i;candidateIndex=page().candidates.findIndex(c=>c.id===remaining.id);render();
}

document.addEventListener('click', async event => {
  const b=event.target.closest('button, [data-zoom]'); if (!b || !data) return;
  if (b.dataset.tab) {pauseTimer(); tab=b.dataset.tab;render();}
  else if (b.dataset.jump) gotoPage(data.pages.findIndex(p=>p.id===b.dataset.jump));
  else if (b.dataset.candidate !== undefined) {candidateIndex=Number(b.dataset.candidate);render();}
  else if (b.dataset.zoom) {$('#large-image').src=b.dataset.zoom;$('#viewer').showModal();}
  else if (b.id==='close-viewer') $('#viewer').close();
  else if (b.id==='prev-page') gotoPage(pageIndex-1);
  else if (b.id==='next-page') gotoPage(pageIndex+1);
  else if (b.id==='timer-button') {if (timer) pauseTimer();else {timer=page().id;timerStarted=performance.now();pageReview().review_timed=true;persist();}render();}
  else if (b.id==='confirm-page') {pageReview().confirmed=true;persist();render();notify(pageReview().uncertain?'已记录；这页仍有争议，不纳入召回率基准。':'原页素材清单已确认。');}
  else if (b.id==='confirm-candidate') {
    const r=candidateReview();r.confirmed=true;
    if (!validCandidate(candidate())) {r.confirmed=false;notify('请补全四项判断；归属正确时需匹配素材清单。非正确归属的截图应为不可用，修改耗时需配合结果填写。');return;}
    persist();render();notify('本条已确认。');
  }
  else if (b.id==='next-pending') nextPending();
  else if (b.id==='download-md') {pauseTimer();download('人工截图审核报告.md',reportText(),'text/markdown;charset=utf-8');}
  else if (b.id==='download-json') {pauseTimer();download('人工截图审核记录.json',JSON.stringify(exportState(),null,2),'application/json');}
  else if (b.id==='save-project') {
    pauseTimer();persist();b.disabled=true;
    try { const response=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({review:exportState(),report:reportText()})});const result=await response.json();if (!response.ok) throw Error(result.error);notify('已保存到项目 human-review/，下一轮可直接读取。');$('#save-status').textContent='报告已保存到项目 · '+new Date().toLocaleTimeString('zh-CN'); }
    catch {notify('保存失败，浏览器进度仍保留。请检查服务或下载报告备份。');}
    finally {b.disabled=false;}
  }
});
document.addEventListener('input', event => {
  const el=event.target;if(!data) return;
  if(el.id==='gold') {
    pageReview().gold=el.value;pageReview().confirmed=false;
    for (const c of page().candidates) {state.candidates[c.id].matches=state.candidates[c.id].matches.filter(q=>lines(el.value).includes(q));state.candidates[c.id].confirmed=false;}
  } else if (el.id==='page-notes') {pageReview().notes=el.value;pageReview().confirmed=false;}
  else if (el.dataset.field && ['TEXTAREA','INPUT'].includes(el.tagName)) {candidateReview()[el.dataset.field]=el.value;candidateReview().confirmed=false;}
  else return;
  persist();
});
document.addEventListener('change', event => {
  const el=event.target;if(!data) return;
  if(el.dataset.rule) {
    state.rules[el.dataset.rule]=el.value;
    Object.values(state.pages).forEach(r=>r.confirmed=false);Object.values(state.candidates).forEach(r=>r.confirmed=false);
    persist();render();notify('规则已记录；已有判断需按新规则重新确认。');
  } else if (el.id==='page-select') gotoPage(Number(el.value));
  else if (el.id==='gold') {
    const matches=$('.matches');
    if(matches) matches.innerHTML=lines(pageReview().gold).map(q=>`<label><input type="checkbox" data-match="${esc(q)}" ${candidateReview().matches.includes(q)?'checked':''}><span>${esc(q)}</span></label>`).join('') || '请先在上方补充原页素材清单。';
  }
  else if (el.id==='page-uncertain') {pageReview().uncertain=el.checked;pageReview().confirmed=false;persist();}
  else if(el.dataset.match) {const r=candidateReview();r.matches=el.checked?[...new Set([...r.matches,el.dataset.match])]:r.matches.filter(q=>q!==el.dataset.match);r.confirmed=false;persist();}
  else if(el.dataset.field && el.tagName==='SELECT') {
    const r=candidateReview();r[el.dataset.field]=el.value;r.confirmed=false;
    if (el.dataset.field==='role' && el.value && el.value!=='correct') {r.quote_status='na';r.A='unusable';r.B='unusable';r.matches=[];}
    else if (el.dataset.field==='role' && el.value==='correct' && r.quote_status==='na') {r.quote_status='';r.A='';r.B='';}
    persist();render();
  }
});
document.addEventListener('visibilitychange',()=>{if(document.hidden){pauseTimer();if(data)render();}});
window.addEventListener('pagehide',pauseTimer);
setInterval(()=>{if(timer && $('#timer-display')) $('#timer-display').textContent=(state.pages[timer].review_seconds+(performance.now()-timerStarted)/1000).toFixed(1)+' 秒';},250);

async function init() {
  try {
    const response=await fetch('/api/data');if(!response.ok)throw Error('样本读取失败');data=await response.json();storageKey=`kurumi-review:${data.id}`;
    let browser=null;try {browser=JSON.parse(localStorage.getItem(storageKey)||'null');} catch {storageOK=false;}
    const serverResponse=await fetch('/api/review');if(!serverResponse.ok)throw Error('已保存记录读取失败');const server=await serverResponse.json();
    const saved=[browser,server].filter(x=>x?.dataset_id===data.id && x.schema_version===1 && x.pages && x.candidates).sort((a,b)=>String(b.updated_at).localeCompare(String(a.updated_at)))[0];
    state=saved || {schema_version:1,dataset_id:data.id,rules:{},pages:{},candidates:{},updated_at:new Date().toISOString()};
    for (const p of data.pages) {
      state.pages[p.id] ??= {gold:p.baseline.quotes.join('\n'),notes:'',uncertain:false,confirmed:false,review_seconds:0,review_timed:false};
      for(const c of p.candidates) state.candidates[c.id] ??= {role:'',quote_status:'',A:'',B:'',matches:[],notes:'',duplicate_of:'',modification_seconds:'',modification_result:'',repair_path:'',confirmed:false};
    }
    $('#save-project').disabled=false;persist();render();
  } catch(error) {$('#main').textContent='加载失败：'+error.message+'。请检查服务和首轮验证产物是否齐全。';$('#save-status').textContent='加载失败';}
}
init();
