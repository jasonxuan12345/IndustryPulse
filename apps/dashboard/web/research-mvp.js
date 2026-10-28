'use strict';
(() => {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {WATCH:'继续观察',REJECT:'暂不采用',BUY_CONDITIONAL:'有条件候选'};
  const triggers = {STAT_RELEASE:'统计发布',NEWS_EVENT:'新闻事件',ETF_RELATIVE_STRENGTH:'ETF 相对强弱'};
  const aliases = {CN_NBS_B07:'油气 石油 天然气',CN_NBS_C13:'农副食品 饲料 养殖',CN_NBS_C35:'专用设备 工程机械',CN_NBS_C06:'煤炭'};
  const state = {report:null,filter:'all',query:'',run:'',job:null,serial:0};
  const time = value => value ? new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '尚无记录';
  const message = (text, error=false) => {$('#mvp-message').innerHTML=text?`<div class="notice ${error?'error':'info'}">${esc(text)}</div>`:'';};
  const url = value => {try {const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:null;} catch {return null;}};
  const link = (value,label) => url(value)?`<a href="${esc(url(value))}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`:`<span class="evidence-meta">${esc(label)} · 原文链接未提供</span>`;
  const download = name => `/api/research/mvp/download/${encodeURIComponent(state.report.run_id)}/${name}`;
  const text = value => Array.isArray(value) ? value.join('；') : value || '尚待原始披露核验';
  async function api(path, post=false) {
    const response=await fetch(path,post?{method:'POST'}:{});
    const data=await response.json();
    if(!response.ok) throw Error(typeof data.detail==='string'?data.detail:'研究服务暂时不可用，请稍后刷新');
    return data;
  }
  function empty(title,reason,action='') {
    return `<div class="state-card"><span class="state-icon">⌁</span><h2>${esc(title)}</h2><p>${esc(reason)}</p>${action?`<div class="actions">${action}</div>`:''}</div>`;
  }
  function evidence(item) {
    const facts=item.facts||[];
    if(!facts.length) return `<p class="memo-note">尚无通过正文校验的证据。请查看下方检索记录和缺口；当前建议保持观察。</p>`;
    return `<p class="memo-note">${facts.length} 条摘录已在所读原文中逐字匹配。来源为事后抓取的重建证据；经济解释与支持/反对关系仍需人工审核。</p>`+facts.map(f=>`<div class="evidence-item"><div class="evidence-meta">${f.supports_or_refutes==='supports'?'支持线索':f.supports_or_refutes==='refutes'?'反对线索':'关系待核验'} · ${esc(f.publisher||'发布方待确认')}</div><p>${esc(f.fact)}</p><div>${link(f.url,f.title||'查看原始披露')}</div><div class="evidence-meta">发布 ${esc(time(f.publish_time))}<br>获取 ${esc(time(f.retrieved_at))} · 原文位置 ${esc(f.location?.start)}–${esc(f.location?.end)}</div></div>`).join('');
  }
  function card(item) {
    const m=item.memo,o=item.opportunity||{},r=item.review||{};
    const gaps=m.risk_flags||[];
    const rounds=r.rounds||[];
    const failures=(r.tool_log||[]).filter(t=>t.status!=='ok');
    const scenarios=m.scenario_assumptions||{};
    return `<article class="memo-card" data-memo="${esc(m.opportunity_id)}">
      <div class="memo-top"><div><h3>${esc(m.sector_name)}</h3><div class="asset-line">${esc(m.asset_name||'待补资产映射')} · ${esc(m.asset_id==='UNMAPPED'?'未完成映射':m.asset_id)} · ${esc(m.asset_market||'市场待核验')}</div></div><span class="status ${m.recommendation_status==='REJECT'?'reject':''}">${esc(labels[m.recommendation_status]||'状态待核验')}</span></div>
      <div class="tags"><span class="tag green">AI 基本面研究 · 收益未验证</span><span class="tag">${esc(triggers[o.trigger_type]||'研究线索')}</span><span class="tag">${(item.facts||[]).length} 条原文证据</span><span class="tag">1 / 3 / 6 个月观察</span></div>
      <p class="thesis">${esc(m.thesis||'盈利传导尚待核验')}</p>
      <p class="memo-note">业务暴露：${esc(m.business_exposure?.description||'未知')} · 映射${esc(m.business_exposure?.fit||'待核验')}<br>线索发布 ${esc(time(o.publish_time))}</p>
      <div class="condition-grid"><div><h4>何时考虑</h4><p>${esc(text(m.entry_condition))}</p></div><div><h4>何时放弃</h4><p>${esc(text(m.invalidation_condition))}</p></div></div>
      <details><summary>原始证据与引用 <span>${(item.facts||[]).length} 条 · 重建证据</span></summary>${evidence(item)}</details>
      <details><summary>独立反证与待补事项 <span>${r.status==='reviewed'?'已完成两路检索':'补证尚不完整'}</span></summary><ul class="gap-list">${gaps.map(g=>`<li>${esc(g)}</li>`).join('')||'<li>当前未列出缺口，仍需人工审核。</li>'}</ul>${rounds.map(step=>`<p class="memo-note">第 ${Number(step.round)+1} 轮 · ${step.stance==='refutes'?'独立反对检索':'支持检索'}：${esc(({supported:'找到支持线索',weakened:'存在削弱线索',rejected:'假设受反证挑战',unverified:'尚未核实'})[step.verdict]||'判断待复核')}。${step.error?esc('调用失败：'+step.error):esc(step.reason==='no_dated_primary_document'?'未找到带可用日期的官方正文':text(step.gaps))}</p>`).join('')}${failures.map(t=>`<p class="memo-note">${esc(({read:'原文读取',search:'搜索',plan:'检索规划',extract_review:'提取核验'})[t.tool]||'资料核验')}未完成：${esc(t.error||t.reason||'来源待核验')}</p>`).join('')}<p class="memo-note">已使用 ${r.calls_used??0} 次调用。建议保持人工复核；可运行一次新研究补充证据。</p></details>
      <details><summary>估值依据、三种情景与退出条件</summary><p class="memo-note">${esc(m.valuation_method||'估值依据尚未齐备')}</p><div class="scenario-grid">${Object.entries(scenarios).map(([key,s])=>`<div><b>${esc(({bear:'悲观情景',base:'基准情景',bull:'乐观情景'})[key]||key)}</b>${esc(s.assumption)}${s.target_price!=null?`<p>条件目标价 ${esc(s.target_price)}</p>`:''}${s.expected_return!=null?`<p>条件收益 ${esc(s.expected_return)}</p>`:''}</div>`).join('')}</div><p class="memo-note">缺少可追溯估值输入时不展示目标价与收益预测。退出条件：${esc(text(m.exit_condition))}</p></details>
    </article>`;
  }
  function drawItems() {
    const items=(state.report.items||[]).filter(i=>(state.filter==='all'||i.memo.recommendation_status===state.filter)&&`${i.memo.sector_name} ${i.memo.asset_name} ${i.memo.asset_id} ${aliases[i.memo.sector]||''}`.toLowerCase().includes(state.query.toLowerCase()));
    const total=state.report.items||[];
    const failed=(state.report.coverage||[]).filter(s=>s.error).length;
    const noResearch=empty('本轮没有足够的研究线索',failed?`${failed} 个行业扫描未完成，请检查数据接入后运行新研究。`:'本轮统计、新闻与行情未形成足够触发。等待新披露，或检查数据是否已同步；系统不会凑数。','<button class="primary" data-new-run>运行新研究</button><a href="/?page=settings">检查数据接入 ↗</a>');
    $('#memo-list').innerHTML=items.map(card).join('')||(total.length?empty('没有符合筛选的研究','试试清除关键词或切换建议状态。未找到候选时系统不会补造买入建议。','<button data-clear-filter>清除筛选</button>'):noResearch);
    $('#visible-count').textContent=`${items.length} 条`;
  }
  function render() {
    const d=state.report;
    const content=$('#mvp-content');
    content.setAttribute('aria-busy','false');
    if(d.status==='not_run') {
      content.innerHTML=empty('还没有跨行业研究结果',d.reason,'<button class="primary" data-new-run>运行首次研究</button><a href="/?page=settings">检查数据与模型配置 ↗</a>');return;
    }
    const items=d.items||[],facts=items.flatMap(i=>i.facts||[]),incomplete=items.filter(i=>i.evidence_status!=='reviewed').length;
    const watches=items.filter(i=>i.memo.recommendation_status==='WATCH').length;
    content.innerHTML=`<div class="run-line"><div class="timestamp">${esc(d.origin_label)}<br>研究时点 ${esc(time(d.as_of))} · 生成 ${esc(time(d.created_at))}</div><label>查看归档<select id="archive-select" aria-label="查看研究归档">${(d.runs||[]).map(run=>`<option value="${esc(run.run_id)}" ${run.run_id===d.run_id?'selected':''}>${esc(run.run_id)} · ${run.memo_count??0} 条</option>`).join('')}</select></label></div>
      <div class="summary-grid">${[[d.scanned_sectors,'已扫描行业','全量覆盖，不按推荐数量凑数'],[items.length,'已完成研究','每行业最多一条重点线索'],[facts.length,'官方原文摘录','正文匹配，支持回查来源'],[watches,'继续观察','估值与证据齐备前不执行']].map(([value,label,caption])=>`<div class="summary-card"><span>${label}</span><strong>${value??0}</strong><small>${caption}</small></div>`).join('')}</div>
      <div class="notice">${incomplete?`${incomplete} 条研究的独立补证尚不完整。`:'本轮已完成两路检索。'}当前为 AI_RESEARCH，收益有效性未经验证；请结合原文、估值缺口与风险条件审核。<a href="/research/quant">查看量化模型状态 ↗</a></div>
      <div class="research-layout"><section><div class="filter-row"><h2>本轮观察清单 <small id="visible-count" class="evidence-meta"></small></h2><input id="memo-query" type="search" value="${esc(state.query)}" placeholder="搜索行业 / 资产" aria-label="搜索行业或资产"><select id="memo-filter" aria-label="筛选建议状态"><option value="all">全部建议</option>${Object.entries(labels).map(([key,value])=>`<option value="${key}" ${state.filter===key?'selected':''}>${value}</option>`).join('')}</select></div><div id="memo-list"></div></section>
      <aside class="side-column"><section class="side-card"><p class="eyebrow">RESEARCH STATUS</p><h2>这轮研究的边界</h2><div class="side-stat"><span>运行状态</span><b>${d.status==='degraded'?'有待补证项':'研究已完成'}</b></div><div class="side-stat"><span>模型</span><b>${esc(d.model_version)}</b></div><div class="side-stat"><span>证据模式</span><b>事后重建</b></div><div class="side-stat"><span>执行状态</span><b>仅观察 · 人工审核</b></div><p>缺少估值、数据日期或资产暴露证据时，结果会降级为观察或不采用。尚未成熟的账本窗口不会标为已验证。</p><a href="/ledger">查看 1 / 3 / 6 月观察账本 ↗</a></section>
      <section class="side-card"><h2>下一步怎么做</h2><p>${incomplete?'核对各条研究的反证缺口，读取原始披露，再补充估值输入。':'复查原文与资产暴露，并等待下一期披露验证。'}运行新研究会另建归档，保留本轮判断。</p><div class="actions"><a href="/?page=settings">数据与模型设置 ↗</a><a href="${download('daily_report.md')}">下载每日简报 ↓</a><a href="${download('investment_memo.json')}">下载研究建议 ↓</a></div></section>
      <section class="side-card"><details><summary>查看全部行业覆盖 · ${d.scanned_sectors} 个</summary><ul class="coverage-list">${(d.coverage||[]).map(s=>`<li>${esc(s.name)} · ${s.error?'扫描失败，待重试':'已扫描'}</li>`).join('')}</ul></details><p>全量扫描不等于每行业都有建议。本轮发现 ${d.candidate_count??0} 条触发线索，经过跨行业去重与排序。</p></section></aside></div>`;
    drawItems();
  }
  async function load(run='') {
    const serial=++state.serial;
    try {
      const data=await api('/api/research/mvp/report'+(run?'?run_id='+encodeURIComponent(run):''));
      if(serial!==state.serial)return;
      if(state.loadFailed&&!state.job)message('');
      state.loadFailed=false;
      state.report=data;state.run=data.run_id||'';render();
    } catch(error) {
      if(serial!==state.serial)return;
      state.loadFailed=true;
      if(state.report)render();
      message((state.report?'保留上次已读研究。':'')+error.message,true);
      if(!state.report){$('#mvp-content').innerHTML=empty('研究归档暂时无法读取','可能尚未生成，或文件完整性检查未通过。请刷新重试；原有量化研究与账本仍可单独查看。','<button data-reload>重新读取</button><a href="/research/quant">查看量化研究 ↗</a>');$('#mvp-content').setAttribute('aria-busy','false');}
    }
  }
  function jobState(text) {$('#mvp-message').innerHTML=`<div class="notice info job-line"><span class="spinner"></span><span>${esc(text)}</span></div>`;}
  async function poll(id) {
    try {
      const job=await api('/api/workbench/jobs/'+encodeURIComponent(id));
      if(job.status!=='complete'){jobState('研究正在后台执行，逐行业查证可能需要数分钟。可查看已有归档。');setTimeout(()=>poll(id),2000);return;}
      state.job=null;$('#mvp-run').disabled=false;
      try{sessionStorage.removeItem('ip-mvp-job');}catch{}
      if(job.result?.error){message('研究未完成：'+job.result.error+'。已保留历史结果及失败日志，可检查配置后重新运行。',true);return;}
      message(job.result?.status==='degraded'?'本轮研究已保存；部分证据待补齐，建议与缺口已逐条标注。':'本轮研究已保存，请核对证据与观察条件。');
      await load(job.result?.run_id||'');
    }catch(error){state.job=null;$('#mvp-run').disabled=false;message('任务状态暂时无法读取：'+error.message+'。任务可能仍在后台执行，可刷新结果查看完成后的归档。',true);}
  }
  async function start() {
    if(state.job)return;
    state.job='submitting';$('#mvp-run').disabled=true;jobState('正在提交新研究任务…');
    try {const job=await api('/api/research/mvp/run',true);state.job=job.id;try{sessionStorage.setItem('ip-mvp-job',job.id);}catch{}await poll(job.id);}
    catch(error){state.job=null;$('#mvp-run').disabled=false;message(error.message,true);}
  }
  $('#mvp-refresh').onclick=()=>load(state.run);
  $('#mvp-run').onclick=start;
  document.addEventListener('click',e=>{if(e.target.closest('[data-new-run]'))start();if(e.target.closest('[data-reload]'))load();if(e.target.closest('[data-clear-filter]')){state.query='';state.filter='all';render();}});
  document.addEventListener('change',e=>{if(e.target.id==='archive-select')load(e.target.value);if(e.target.id==='memo-filter'){state.filter=e.target.value;drawItems();}});
  document.addEventListener('input',e=>{if(e.target.id==='memo-query'){state.query=e.target.value;drawItems();}});
  load();
  try{const job=sessionStorage.getItem('ip-mvp-job');if(job){state.job=job;$('#mvp-run').disabled=true;poll(job);}}catch{}
})();
