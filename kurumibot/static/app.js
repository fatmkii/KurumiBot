'use strict';
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = (value) => Number(value || 0).toLocaleString('zh-CN');
const seconds = (value) => value == null ? '—' : `${Number(value).toFixed(2)} s`;
const money = (value) => value == null ? '未知' : `¥ ${Number(value).toFixed(4)}`;
const time = (value) => value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '—';
const statuses = {sent:'已发送',failed:'发送失败',processing:'处理中',rate_limited:'频率限制',busy:'并发已满',interrupted:'处理已中断'};
const badge = (status) => `<span class="badge ${esc(status)}">${esc(statuses[status] || status)}</span>`;
const pages = {
  overview:['OVERVIEW','运行概览','从一句消息，到一张恰到好处的图片。'],
  conversations:['CONVERSATIONS','对话记录','看看它接住了什么，又是怎样回应的。'],
  usage:['MODEL USAGE','AI 用量','每次判断的耗时、用量与估算费用。'],
  materials:['MATERIAL LIBRARY','台词素材库','打磨每一句反应，让下一次接话更贴切。'],
  settings:['CONNECTIONS','连接与配置','管理模型与连接凭据，保存后重启 Bot 生效。']
};
const state = {csrf:'', authenticated:false, page:'overview', pagination:1, filters:{}, items:[], request:0, restartNeeded:false};
let toastTimer;

function toast(message) {
  $('#toast').textContent = message; $('#toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}
function showLogin() {
  state.authenticated = false; state.csrf = ''; state.request++;
  $('#workspace').hidden = true; $('#login-screen').hidden = false;
  if ($('#detail-dialog').open) $('#detail-dialog').close();
  $('#username').focus();
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials:'same-origin', ...options,
    headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf,...options.headers}
  });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') showLogin();
    throw new Error(result.error || '请求未完成，请稍后再试');
  }
  return result;
}
function empty(title = '这里还没有记录', description = '私聊机器人或在群里 @ 它，新的记录就会出现在这里。') {
  return `<div class="empty"><h2>${esc(title)}</h2><p>${esc(description)}</p></div>`;
}
function paginator(data) {
  const totalPages = Math.max(1, Math.ceil(data.total / data.size));
  return `<div class="pagination"><span>共 ${number(data.total)} 条 · 每页 ${data.size} 条</span><div class="pagination-controls"><button class="secondary" data-action="previous" ${data.page <= 1 ? 'disabled':''}>上一页</button><span class="mono">${data.page} / ${totalPages}</span><button class="secondary" data-action="next" ${data.page >= totalPages ? 'disabled':''}>下一页</button></div></div>`;
}
function conversationTable(items) {
  if (!items.length) return empty();
  return `<div class="table-wrap"><table><thead><tr><th>收到时间</th><th>用户留言</th><th>回复图片</th><th>发送结果</th><th>总耗时</th><th>查看</th></tr></thead><tbody>${items.map(item => `<tr>
    <td class="time">${esc(time(item.received_at))}<div class="subtext">${item.scope==='group'?'群 @ · '+esc(item.chat_hash.slice(-6)):'私聊'} · ${esc(item.user_hash.slice(-6))}</div></td>
    <td class="message-cell"><div class="message-text">${esc(item.content || '（空消息）')}</div>${item.scene ? `<div class="subtext">${esc(item.scene)}</div>`:''}</td>
    <td>${item.has_image ? `<button class="thumb-button" data-action="reply-preview" data-id="${item.id}" aria-label="预览第 ${item.id} 条回复图片"><img src="/api/conversations/${item.id}/image" alt="机器人回复的台词截图" loading="lazy"></button>`:'<span class="muted">—</span>'}</td>
    <td>${badge(item.status)}${item.fallback_reason ? '<div class="subtext">默认反应图</div>':''}</td><td class="numeric">${seconds(item.elapsed_seconds)}</td>
    <td><button class="text-button" data-action="conversation" data-id="${item.id}">详情 ↗</button></td></tr>`).join('')}</tbody></table></div>`;
}
function overview(data) {
  state.items = data.recent;
  const m = data.materials, c = data.conversations, a = data.ai;
  const latest = data.recent.find(item => item.status === 'sent' && item.has_image);
  $('.sidebar-note .mono').textContent = `${m.total} FRAMES · VOL. 01—06`;
  return `<div class="metrics">
    <article class="metric"><div class="metric-label">可用素材 <span class="mono">01</span></div><div class="metric-number">${number(m.enabled)}<span class="subtext"> / ${number(m.total)}</span></div><div class="metric-foot">${m.total - m.enabled} 条已停用 · 6 卷漫画</div></article>
    <article class="metric"><div class="metric-label">已收到消息 <span class="mono">02</span></div><div class="metric-number">${number(c.total)}</div><div class="metric-foot">${number(c.sent)} 次发送成功 · ${number(c.failed)} 次失败</div></article>
    <article class="metric"><div class="metric-label">AI 累计用量 <span class="mono">03</span></div><div class="metric-number">${number(a.tokens)}</div><div class="metric-foot">tokens · ${number(a.calls)} 次调用，含选图预览</div></article>
    <article class="metric metric-accent"><div class="metric-label">累计估算费用 <span class="mono">04</span></div><div class="metric-number">${money(a.estimated_cost)}</div><div class="metric-foot">人民币 · ${a.unknown_cost_calls} 次调用用量未知</div></article>
  </div><div class="overview-grid">
    <section class="panel"><div class="panel-head"><div><p class="eyebrow">LATEST REACTION</p><h2>最近的一次接话</h2></div><span class="badge ${data.bot_running?'enabled':'disabled'}">${data.bot_running?'Bot 进程运行中':'Bot 未运行'}</span></div>
    ${latest ? `<div class="reply-frame"><img src="/api/conversations/${latest.id}/image" alt="最近发送的漫画台词图片"></div><div class="reply-copy"><span class="eyebrow">${esc(time(latest.received_at))}</span><p class="quote">“${esc(latest.content)}”</p><p class="muted">${esc(latest.reason || latest.fallback_reason || '已发送图片回复')}</p><button class="text-button" data-action="conversation" data-id="${latest.id}">查看这次回复 ↗</button></div>` : empty('第一张反应图，等你触发','私聊机器人，开始一次久留美式的接话。')}</section>
    <section class="panel"><div class="panel-head"><div><p class="eyebrow">THE COLLECTION</p><h2>素材库分布</h2></div><a class="text-button" href="#materials">管理 ↗</a></div><div class="panel-body"><p class="muted">六卷漫画，${number(m.total)} 种反应。</p>${data.volumes.map(v=>`<div class="volume-row"><span class="mono">VOL. ${String(v.volume).padStart(2,'0')}</span><progress max="${Math.max(...data.volumes.map(row=>row.total),1)}" value="${v.enabled}" aria-label="第 ${v.volume} 卷已启用 ${v.enabled} 条素材"></progress><span class="count">${v.enabled} / ${v.total} 条</span></div>`).join('')}<div class="notice section-gap"><strong>让下一次回复更贴切</strong>素材台词、情绪和适用场景可直接修正；停用后不再参与选图。</div><p class="cost-note">${esc(data.price_note)} <a href="${esc(data.price_source)}" target="_blank" rel="noopener">官方报价 ↗</a></p></div></section>
  </div><section class="panel section-gap"><div class="panel-head"><div><p class="eyebrow">RECENT CONVERSATIONS</p><h2>最近对话</h2></div><a class="text-button" href="#conversations">全部记录 ↗</a></div>${conversationTable(data.recent)}</section>`;
}
function filters(kind) {
  const f = state.filters;
  if (kind === 'conversations') return `<form id="filters" class="filters"><div class="field grow"><label for="search">搜索留言</label><input id="search" name="q" value="${esc(f.q)}" placeholder="输入用户留言中的关键词"></div><div class="field"><label for="status">发送结果</label><select id="status" name="status"><option value="">全部结果</option>${Object.entries(statuses).map(([k,v])=>`<option value="${k}" ${f.status===k?'selected':''}>${v}</option>`).join('')}</select></div><button class="primary">查询记录</button></form>`;
  if (kind === 'usage') return `<form id="filters" class="filters"><div class="field"><label for="errors">调用结果</label><select id="errors" name="errors"><option value="">全部调用</option><option value="1" ${f.errors==='1'?'selected':''}>仅失败调用</option></select></div><button class="secondary">应用筛选</button></form>`;
  return `<form id="filters" class="filters"><div class="field grow"><label for="search">搜索台词或场景</label><input id="search" name="q" value="${esc(f.q)}" placeholder="台词、情绪、场景，或素材 ID"></div><div class="field"><label for="volume">漫画卷号</label><select id="volume" name="volume"><option value="">全部卷号</option>${[1,2,3,4,5,6].map(v=>`<option value="${v}" ${f.volume===String(v)?'selected':''}>第 ${v} 卷</option>`).join('')}</select></div><div class="field"><label for="enabled">素材状态</label><select id="enabled" name="enabled"><option value="">全部状态</option><option value="1" ${f.enabled==='1'?'selected':''}>已启用</option><option value="0" ${f.enabled==='0'?'selected':''}>已停用</option></select></div><button class="primary">查找素材</button></form>`;
}
function materialCards(data) {
  state.items = data.items;
  return filters('materials') + `<div class="notice">修正后立即用于新消息的选图。图片裁剪与来源保持原样；繁体原文与简体检索台词请分别填写。</div>` + (data.items.length ? `<div class="materials-grid">${data.items.map(item=>`<article class="material-card"><img class="material-image" src="/api/materials/${encodeURIComponent(item.id)}/image" alt="${esc(item.quote_simplified)}" loading="lazy" width="${item.width}" height="${item.height}"><div class="material-meta"><div class="card-top"><span class="mono">VOL. ${String(item.volume).padStart(2,'0')} / ${esc(item.page_id)}</span><span class="badge ${item.enabled?'enabled':'disabled'}">${item.enabled?'已启用':'已停用'}</span></div><p class="material-quote">${esc(item.quote_simplified)}</p><div class="tags">${item.emotion_tags.map(tag=>`<span class="tag">${esc(tag)}</span>`).join('')}</div></div><div class="card-actions"><button class="switch" role="switch" aria-checked="${Boolean(item.enabled)}" aria-label="${esc(item.quote_simplified)}的启用状态" data-action="toggle" data-id="${esc(item.id)}"><span aria-hidden="true"></span>${item.enabled?'启用':'停用'}</button><div><button class="text-button" data-action="material-preview" data-id="${esc(item.id)}">预览</button><button class="secondary" data-action="edit" data-id="${esc(item.id)}">修正</button></div></div></article>`).join('')}</div>` : `<section class="panel">${empty('没有找到匹配素材','换个关键词，或调整卷号和启用状态筛选。')}</section>`) + paginator(data);
}
function usageTable(data) {
  state.items = data.items;
  const table = data.items.length ? `<div class="table-wrap"><table><thead><tr><th>调用时间 / 模型</th><th>输入 / 输出 tokens</th><th>缓存命中</th><th>AI 耗时</th><th>估算费用</th><th>结果 / 失败原因</th></tr></thead><tbody>${data.items.map(item=>`<tr><td class="time">${esc(time(item.called_at))}<div class="subtext">${esc(item.model)} · ${item.conversation_id?'对话 #'+item.conversation_id:'选图预览'}</div></td><td class="numeric">${item.usage.prompt_tokens==null?'—':number(item.usage.prompt_tokens)} / ${item.usage.completion_tokens==null?'—':number(item.usage.completion_tokens)}<div class="subtext">${item.candidate_count} 个候选</div></td><td class="numeric">${item.usage.prompt_cache_hit_tokens==null?'—':number(item.usage.prompt_cache_hit_tokens)}</td><td class="numeric">${seconds(item.elapsed_seconds)}</td><td class="numeric">${money(item.estimated_cost)}</td><td>${item.error_type?'<span class="badge failed">调用失败</span>':`<span class="badge sent">${item.selected_id?'已选图':'无匹配'}</span>`}<div class="subtext">${esc(item.error_type || item.scene || '—')}</div></td></tr>`).join('')}</tbody></table></div>` : empty('还没有 AI 调用记录','私聊选图或命令行预览产生的调用都会记录在这里。');
  return filters('usage')+`<p class="cost-note">${esc(data.price_note)} 未返回 token 用量的调用费用显示为“未知”。 <a href="${esc(data.price_source)}" target="_blank" rel="noopener">官方报价 ↗</a></p><section class="panel">${table}</section>`+paginator(data);
}
function settingsForm(data) {
  return `<div class="notice"><strong>修改后需要重启 Bot 服务</strong>保存只更新 .env，当前运行中的 Bot 继续使用启动时的配置。密钥留空表示保留现有值。</div>${state.restartNeeded?'<div class="notice success-notice" role="status"><strong>配置已保存</strong><p>请重启 Bot 服务使修改生效。</p></div>':''}<div class="settings-layout"><section class="panel"><div class="panel-head"><div><p class="eyebrow">PROVIDER & CREDENTIALS</p><h2>Codex OAuth Proxy 与 QQ 连接</h2></div></div><div class="panel-body"><form id="settings-form" class="settings-form"><div class="field"><label for="model">AI 模型</label><input id="model" name="CODEX_OAUTH_PROXY_MODEL" value="${esc(data.model)}" maxlength="512" required><p class="subtext">接口：http://127.0.0.1:9879/v1。填写代理 /v1/models 返回的模型 ID，可使用支持的推理强度后缀。</p></div>${[['CODEX_OAUTH_PROXY_API_KEY','Codex OAuth Proxy API 密钥'],['QQ_APP_ID','QQ Bot App ID'],['QQ_APP_SECRET','QQ Bot Client Secret']].map(([key,label])=>`<div class="field"><label for="${key}">${label}<span class="secret-state">${esc(data.secrets[key])}</span></label><input id="${key}" name="${key}" type="password" autocomplete="off" placeholder="留空保留现有值" maxlength="512"><p class="subtext">仅在需要更换时填写新值。</p></div>`).join('')}<p id="settings-error" class="error" role="alert"></p><div class="form-actions"><button class="primary" type="submit">保存配置</button></div></form></div></section><aside class="panel settings-help"><p class="eyebrow">A FEW NOTES</p><h2>照看好连接</h2><p>页面只显示脱敏凭据。保存时空白字段不会清除已有密钥。</p><div class="rule"></div><h3>重启后再测试</h3><p>通过 Supervisor 或启动终端重启 Bot，再私聊发送一条测试消息，确认新配置可用。</p><div class="rule"></div><h3>素材无需重启</h3><p>台词、情绪、场景和启用状态直接保存到素材库，新消息立即使用。</p></aside></div>`;
}
async function loadPage() {
  if (!state.authenticated) return;
  const requestId = ++state.request;
  const page = state.page;
  $('#page-error').hidden = true; $('#refresh').disabled = true;
  const query = new URLSearchParams({...state.filters,page:state.pagination,size:page==='materials'?12:20});
  try {
    const data = await api(`/api/${page==='overview'?'overview':page==='settings'?'settings':page}?${query}`);
    if (requestId !== state.request) return;
    $('#content').innerHTML = page==='overview'?overview(data):page==='materials'?materialCards(data):page==='usage'?usageTable(data):page==='settings'?settingsForm(data):filters(page)+`<section class="panel">${conversationTable(data.items)}</section>`+paginator(data);
    if (page === 'conversations') state.items = data.items;
    $('#updated-at').textContent = `最近更新 ${new Date().toLocaleTimeString('zh-CN',{hour12:false})}`;
  } catch (error) {
    if (requestId !== state.request) return;
    $('#page-error').textContent = error.message; $('#page-error').hidden = false;
  } finally { if (requestId === state.request) $('#refresh').disabled = false; }
}
function route() {
  const page = location.hash.slice(1);
  state.page = pages[page]?page:'overview'; state.pagination = 1; state.filters = {};
  const [kicker,title,description] = pages[state.page];
  $('#page-kicker').textContent = kicker; $('#page-title').textContent = title; $('#page-description').textContent = description;
  document.title = `${title} · 久留美工作室`;
  document.querySelectorAll('nav a').forEach(link=>{const active = link.dataset.page===state.page;link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
  $('#content').innerHTML = '<div class="loading">正在读取工作室数据…</div>';
  loadPage();
}
function dialog(title, html) { $('#dialog-title').textContent=title;$('#dialog-body').innerHTML=html;$('#detail-dialog').showModal(); }
function preview(url, title) { dialog('图片预览',`<div class="dialog-content"><img class="preview-image" src="${esc(url)}" alt="${esc(title)}"><p class="source-note">${esc(title)}</p></div>`); }
function conversation(item) {
  const image = item.has_image ? `<img class="preview-image" src="/api/conversations/${item.id}/image" alt="该条对话的回复图片">` : '';
  const details = [['场景判断',item.scene],['选图理由',item.reason],
    ['处理耗时',`${seconds(item.elapsed_seconds)} · AI ${seconds(item.ai_seconds)}`],
    ['发送情况',`${item.attempts} 次尝试${item.error_type?' · '+item.error_type:''}`],
    ['素材 ID',item.material_id || '默认反应图 / 未选图'],['兜底原因',item.fallback_reason || '未使用兜底'],
    ['会话类型',item.scope==='group'?'群 @ · '+item.chat_hash.slice(-6):'私聊']];
  dialog(`对话 #${item.id}`,`<div class="dialog-content"><div>${badge(item.status)} <span class="time">${esc(time(item.received_at))}</span></div><p class="detail-message">${esc(item.content || '（空消息）')}</p>${image}<div class="detail-grid">${details.map(([title,value])=>`<div><span class="eyebrow">${title}</span><p>${esc(value || '—')}</p></div>`).join('')}</div></div>`);
}
function edit(item) {
  dialog('修正台词素材',`<div class="dialog-content edit-layout"><div><img class="edit-image" src="/api/materials/${encodeURIComponent(item.id)}/image" alt="待修正的漫画截图"><p class="source-note">${esc(item.id)}<br>第 ${item.volume} 卷 · ${esc(item.source_file)}<br>原页裁剪坐标：${esc(item.bbox.join(', '))}</p></div><form id="edit-form" class="edit-form" data-id="${esc(item.id)}">${[['quote_traditional','繁体原台词'],['quote_simplified','简体检索台词'],['meaning','台词含义'],['scenarios','适用场景']].map(([key,label])=>`<div class="field"><label for="edit-${key}">${label}</label><textarea id="edit-${key}" name="${key}" maxlength="3000" required>${esc(item[key])}</textarea></div>`).join('')}<div class="field"><label for="edit-tags">情绪标签</label><input id="edit-tags" name="emotion_tags" value="${esc(item.emotion_tags.join('，'))}" required><p class="subtext">用逗号分隔，例如：焦虑，崩溃，自嘲</p></div><label class="check-field"><input name="enabled" type="checkbox" ${item.enabled?'checked':''}>启用此素材，参与新消息选图</label><p id="edit-error" class="error" role="alert"></p><div class="form-actions"><button class="secondary" type="button" data-action="cancel">取消</button><button class="primary" type="submit">保存修正</button></div></form></div>`);
}
$('#login-form').addEventListener('submit', async event=>{
  event.preventDefault(); const button=event.target.querySelector('button');button.disabled=true;$('#login-error').textContent='';
  try {const result=await api('/api/login',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});state.csrf=result.csrf;state.authenticated=true;$('#account-name').textContent=result.username;$('#password').value='';$('#login-screen').hidden=true;$('#workspace').hidden=false;route();}catch(error){$('#login-error').textContent=error.message;}finally{button.disabled=false;}
});
async function logout(){try{await api('/api/logout',{method:'POST',body:'{}'});showLogin();}catch(error){toast(error.message);}}
$('#logout').addEventListener('click',logout);
$('#mobile-logout').addEventListener('click',logout);
$('#refresh').addEventListener('click',loadPage);
$('.skip-link').addEventListener('click',event=>{event.preventDefault();$('#main').focus();});
$('#close-dialog').addEventListener('click',()=>$('#detail-dialog').close());
window.addEventListener('hashchange',route);
document.addEventListener('submit',async event=>{
  const form=event.target;
  if (form.id==='filters') {event.preventDefault();state.filters=Object.fromEntries(new FormData(form));state.pagination=1;await loadPage();return;}
  if (!['edit-form','settings-form'].includes(form.id)) return;
  event.preventDefault();const button=form.querySelector('button[type="submit"]');button.disabled=true;
  const errorBox=$(form.id==='edit-form'?'#edit-error':'#settings-error');errorBox.textContent='';
  try {
    const payload=Object.fromEntries(new FormData(form));
    if(form.id==='edit-form') {payload.emotion_tags=payload.emotion_tags.split(/[,，、]/).map(tag=>tag.trim()).filter(Boolean);payload.enabled=form.elements.enabled.checked;await api(`/api/materials/${encodeURIComponent(form.dataset.id)}`,{method:'PATCH',body:JSON.stringify(payload)});$('#detail-dialog').close();toast('素材修正已保存，新消息立即使用。');}
    else {const result=await api('/api/settings',{method:'POST',body:JSON.stringify(payload)});state.restartNeeded=state.restartNeeded||result.restart_required;toast(result.changed?'配置已保存，请重启 Bot 服务。':'没有更换配置，原有密钥已保留。');}
    await loadPage();
  } catch(error) {errorBox.textContent=error.message;}finally{button.disabled=false;}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-action]');if(!button)return;
  const action=button.dataset.action;
  if(action==='cancel'){$('#detail-dialog').close();return;}
  if(action==='previous'||action==='next'){state.pagination+=action==='next'?1:-1;await loadPage();return;}
  const item=state.items.find(row=>String(row.id)===button.dataset.id);if(!item)return;
  if(action==='conversation')conversation(item);
  else if(action==='reply-preview')preview(`/api/conversations/${item.id}/image`,item.content);
  else if(action==='material-preview')preview(`/api/materials/${encodeURIComponent(item.id)}/image`,item.quote_simplified);
  else if(action==='edit')edit(item);
  else if(action==='toggle') {button.disabled=true;try{await api(`/api/materials/${encodeURIComponent(item.id)}`,{method:'PATCH',body:JSON.stringify({enabled:!item.enabled})});toast(item.enabled?'素材已停用，不再参与新消息选图。':'素材已启用。');await loadPage();}catch(error){toast(error.message);}finally{button.disabled=false;}}
});
(async()=>{try{const session=await api('/api/session');if(session.authenticated){state.csrf=session.csrf;state.authenticated=true;$('#account-name').textContent=session.username;$('#workspace').hidden=false;route();}else showLogin();}catch(error){showLogin();$('#login-error').textContent=error.message;}})();
