const newsLibrary = {
  catalog: null, draft: null, domain: 'semiconductor', industry: 'chip-design',
  query: '', language: 'all', kind: 'all', onlyEnabled: false,
  dirty: false, loading: false, error: '', poll: null,
  viewIndustry: 'all', viewSource: 'all'
};
const sourceKindNames = {rss:'直接 RSS',wallstreetcn:'公开资讯接口',rsshub:'RSSHub 社区源',search:'新闻聚合搜索'};
const sourceCategoryNames = {media:'专业媒体',official:'官方发布',research:'研究机构',aggregator:'新闻聚合'};
const sourceHealthNames = {never:'尚未抓取',ok:'读取正常',empty:'读取成功 · 暂无条目',stale:'连接失败 · 保留旧数据',error:'暂时不可用'};

function availableNewsSources(industryId = newsLibrary.industry) {
  const domain = newsLibrary.catalog.domains.find(item=>item.industries.some(industry=>industry.id===industryId));
  return newsLibrary.catalog.sources.filter(source=>(source.domains.includes('*')||source.domains.includes(domain.id))&&(!source.industry||source.industry===industryId));
}

function visibleNewsSources() {
  const enabled = newsLibrary.draft.selections[newsLibrary.industry] || [];
  return availableNewsSources().filter(source=>
    (newsLibrary.language==='all'||source.language===newsLibrary.language)&&
    (newsLibrary.kind==='all'||source.kind===newsLibrary.kind)&&
    (!newsLibrary.onlyEnabled||enabled.includes(source.id))&&
    `${source.name} ${source.url} ${source.category}`.toLowerCase().includes(newsLibrary.query.toLowerCase())
  );
}

function newsSourceCards() {
  const enabled = newsLibrary.draft.selections[newsLibrary.industry] || [];
  return visibleNewsSources().map(source=>{
    const health=source.status;
    const link=source.kind==='rsshub'?newsLibrary.draft.rsshub_base.replace(/\/$/,'')+source.route:source.url;
    return `<article class="source-card ${enabled.includes(source.id)?'selected':''}">
      <label class="source-card-top"><input type="checkbox" data-source-toggle="${escapeHtml(source.id)}" ${enabled.includes(source.id)?'checked':''}><span><b>${escapeHtml(source.name)}</b><small>${source.language==='zh'?'中文':'英文'} · ${sourceCategoryNames[source.category]||'专业媒体'}</small></span>${source.recommended?'<span class="source-recommended">推荐</span>':''}</label>
      <div class="source-card-tags"><span>${sourceKindNames[source.kind]}</span><span class="source-health ${health.state}">${sourceHealthNames[health.state]}</span></div>
      <p class="source-card-description">${source.kind==='rsshub'?'经社区 RSSHub 实例转换；受实例与原站可用性影响。':source.kind==='search'?'聚合该行业的多家媒体报道，保留原发布方。':source.kind==='wallstreetcn'?'公开网页使用的资讯接口，无需填写模型 API Key。':'直接订阅发布方公开 RSS / Atom，保留来源与原文入口。'}</p>
      <div class="source-card-meta">缓存 ${health.count} 条${health.last_success?' · 成功 '+date(health.last_success):''}</div>
      ${health.error?`<p class="source-card-error" title="${escapeHtml(health.error)}">${escapeHtml(health.error)}</p>`:''}
      <div class="source-card-actions"><a href="${escapeHtml(link)}" target="_blank" rel="noopener">订阅地址 ↗</a><button data-news-test="${escapeHtml(source.id)}">测试此源</button></div>
    </article>`;
  }).join('')||'<div class="empty">没有匹配的来源，试试清除搜索或筛选。</div>';
}

function sourceSettingsPanel() {
  if(!newsLibrary.catalog)return panel('内置资讯源',`<div class="panel-body"><div class="${newsLibrary.error?'wb-error':'loading'}">${escapeHtml(newsLibrary.error||'正在读取内置来源库…')}</div><button class="control" data-news-action="reload">重新读取</button></div>`);
  const catalog=newsLibrary.catalog, draft=newsLibrary.draft;
  const domain=catalog.domains.find(item=>item.id===newsLibrary.domain);
  const selected=new Set(Object.values(draft.selections).flat()).size;
  const progress=catalog.status;
  return `<section class="panel source-library">
    <div class="source-library-hero"><div><p class="eyebrow">PUBLIC INTELLIGENCE / 公开资讯</p><h2>你的行业，你的消息来源。</h2><p>无需寻找接口或填写 JSON。按领域与行业勾选来源，保存后自动采集。</p></div><div class="source-library-numbers"><b>${catalog.sources.length}<small>内置订阅通道</small></b><b>${catalog.domains.length}<small>研究领域</small></b><b>${catalog.domains.reduce((total,item)=>total+item.industries.length,0)}<small>细分行业</small></b><b>${selected}<small>已选通道</small></b></div></div>
    <div class="panel-body">
      <div class="source-mode-line"><label>资讯模式 <select id="news-mode"><option value="builtin" ${draft.mode==='builtin'?'selected':''}>内置公开资讯源 · 推荐</option><option value="custom" ${draft.mode==='custom'?'selected':''}>自定义 JSON / 外部统一接口</option><option value="demo" ${draft.mode==='demo'?'selected':''}>虚构演示 · 仅用于看界面</option></select></label><label>自动更新 <select id="news-refresh-minutes">${[5,15,30,60,180,1440].map(value=>`<option value="${value}" ${draft.refresh_minutes===value?'selected':''}>${value<60?value+' 分钟':value/60+' 小时'}</option>`).join('')}</select></label><span class="source-save-state" id="news-dirty">${newsLibrary.dirty?'● 有未保存的修改':'✓ 配置已保存'}</span></div>
      <div class="actions" style="margin-bottom:20px"><button class="control primary" data-news-action="save">保存来源选择</button><span class="muted" style="font-size:11px">修改当前行业不会影响其他行业，保存后立即生效。</span></div>
      <div class="source-domain-tabs">${catalog.domains.map(item=>`<button data-news-domain="${item.id}" class="${item.id===domain.id?'active':''}">${escapeHtml(item.name)}</button>`).join('')}</div>
      <div class="source-industry-tabs">${domain.industries.map(industry=>`<button data-news-industry="${industry.id}" class="${industry.id===newsLibrary.industry?'active':''}">${escapeHtml(industry.name)} <small>${(draft.selections[industry.id]||[]).length} 源</small></button>`).join('')}</div>
      <div class="source-toolbar"><input id="news-source-query" type="search" aria-label="搜索资讯源" placeholder="搜索来源名称或网址…" value="${escapeHtml(newsLibrary.query)}"><select id="news-source-language" aria-label="来源语言"><option value="all">全部语言</option><option value="zh" ${newsLibrary.language==='zh'?'selected':''}>中文</option><option value="en" ${newsLibrary.language==='en'?'selected':''}>英文</option></select><select id="news-source-kind" aria-label="来源类型"><option value="all">全部类型</option>${Object.entries(sourceKindNames).map(([key,value])=>`<option value="${key}" ${newsLibrary.kind===key?'selected':''}>${value}</option>`).join('')}</select><label><input type="checkbox" id="news-only-enabled" ${newsLibrary.onlyEnabled?'checked':''}> 仅看已选</label></div>
      <div class="source-bulk"><button data-news-action="recommend">本行业推荐</button><button data-news-action="select-visible">全选筛选结果</button><button data-news-action="clear-industry">清空本行业</button><span></span><button data-news-action="recommend-domain">为整个领域启用推荐</button><button data-news-action="clear-domain">停用整个领域</button></div>
      <div class="source-grid" id="news-source-grid">${newsSourceCards()}</div>
      <div class="source-save-bar"><div><b>${progress.running?`正在采集 ${progress.completed} / ${progress.total}`:progress.last_finished?'上次采集完成 '+date(progress.last_finished):'保存后即可开始读取真实资讯'}</b><small>多源去重 · 失败保留上次成功内容 · 不自动回退为演示</small></div><button class="control" data-news-action="refresh" ${progress.running?'disabled':''}>↻ 立即抓取已启用源</button><button class="control primary" data-news-action="save">保存选择并启用</button></div>
      ${progress.running?`<div class="source-progress"><i style="width:${progress.total?progress.completed/progress.total*100:0}%"></i></div>`:''}
      ${newsLibrary.error?`<div class="wb-error" role="alert">${escapeHtml(newsLibrary.error)}</div>`:''}
      <p class="help source-footnote">数量包含同一发布方的不同栏目和行业搜索通道，不代表同等数量的独立媒体。公开源可能变更、限流或在你的网络中不可达，卡片显示实际结果。当前采集新闻，不会编造价格、资金流或热度。</p>
      <details class="source-advanced"><summary>高级：RSSHub 社区实例与来源依据</summary><label class="wb-field">RSSHub 地址（已内置，无需修改）<input id="news-rsshub-base" type="url" value="${escapeHtml(draft.rsshub_base)}"></label><p class="help">社区实例为可选来源。地址变更仅影响 RSSHub 类型，不影响直接订阅。</p><div class="actions">${catalog.references.map((url,index)=>`<a class="control" target="_blank" rel="noopener" href="${escapeHtml(url)}">开源参考 ${index+1} ↗</a>`).join('')}</div></details>
    </div></section>`;
}

settings=function(){return sourceSettingsPanel()+`<div style="margin-top:22px">${modelPanel()}</div><details class="source-advanced panel"><summary>高级：导入 JSON 或接入自己的统一数据接口</summary>${dataSettings()}</details>`};

function markNewsDirty() {
  newsLibrary.dirty=true;
  const marker=$('#news-dirty');if(marker)marker.textContent='● 有未保存的修改';
}

async function loadNewsCatalog(preserveDraft=true) {
  newsLibrary.loading=true;
  try {
    const catalog=await request('/api/dashboard/sources');
    newsLibrary.catalog=catalog;
    if(!newsLibrary.draft||!preserveDraft) {newsLibrary.draft=structuredClone(catalog.settings);newsLibrary.dirty=false;}
    newsLibrary.error='';
  } catch(error) {newsLibrary.error=error.message;}
  finally {newsLibrary.loading=false;}
}

function queueNewsPoll() {
  if(newsLibrary.poll)return;
  newsLibrary.poll=setTimeout(async()=>{
    newsLibrary.poll=null;
    try {
      await loadNewsCatalog(true);
      state.feed=await request('/api/dashboard/feed');
      const editing=state.page==='settings'&&(newsLibrary.dirty||['INPUT','SELECT'].includes(document.activeElement?.tagName));
      if(!editing)render();
      if(newsLibrary.catalog?.status.running)queueNewsPoll();
    } catch(error) {newsLibrary.error=error.message;if(state.page==='settings')render();}
  },2200);
}

const storiesBeforeNewsLibrary=stories;
stories=function(){return storiesBeforeNewsLibrary().filter(item=>(newsLibrary.viewIndustry==='all'||item.industry_ids?.includes(newsLibrary.viewIndustry))&&(newsLibrary.viewSource==='all'||item.source_id===newsLibrary.viewSource||item.source_ids?.includes(newsLibrary.viewSource)))};

const newsRowsBeforeNewsLibrary=newsRows;
newsRows=function(items){
  if(!items.length)return '<div class="empty"><b>暂无匹配资讯</b>在“数据接入与设置”启用本领域的公开来源，或调整行业、来源和时间范围。新启用的源需要等待首次采集。</div>';
  return items.map(item=>{const html=newsRowsBeforeNewsLibrary([item]);return item.time_kind==='first_seen'?html.replace(date(item.published_at),'首次发现 · '+date(item.published_at)):html}).join('');
};

const renderBeforeNewsLibrary=render;
render=function(){
  renderBeforeNewsLibrary();
  if(!newsLibrary.catalog&&!newsLibrary.loading)loadNewsCatalog().then(()=>{if(newsLibrary.catalog)render()});
  if(state.feed?.collection&&!workPages.has(state.page)) {
    const progress=state.feed.collection;
    if(state.page!=='settings') {
      const banner=document.createElement('div');banner.className='notice source-live-notice';
      banner.textContent=progress.running?`↻ 正在从公开渠道采集真实资讯 ${progress.completed} / ${progress.total}，结果会自动出现。`:`● 内置真实资讯 · 已启用 ${progress.enabled_sources} 个通道。新闻不等于行情；价格、资金流与走势未接入时留空。`;
      if(progress.warning)banner.textContent+=' '+progress.warning;
      if(!progress.running&&progress.enabled_sources===0)banner.textContent='尚未启用任何资讯源，请在“数据接入与设置”勾选并保存。';
      else if(!progress.running&&progress.successful_sources===0)banner.textContent='尚无成功采集的资讯，请在“数据接入与设置”查看来源连接状态。';
      if(progress.failed_sources)banner.textContent+=` ${progress.failed_sources} 个已启用通道连接失败，保留其上次成功数据。`;
      const tabs=$('.sector-tabs');if(tabs)tabs.insertAdjacentElement('afterend',banner);
      const domain=newsLibrary.catalog?.domains.find(item=>item.id===state.sector);
      if(domain)banner.insertAdjacentHTML('afterend',`<div class="source-view-filters"><label>细分行业 <select id="news-view-industry"><option value="all">本领域全部行业</option>${domain.industries.map(item=>`<option value="${item.id}" ${newsLibrary.viewIndustry===item.id?'selected':''}>${escapeHtml(item.name)}</option>`).join('')}</select></label><label>资讯来源 <select id="news-view-source"><option value="all">本领域全部来源</option>${newsLibrary.catalog.sources.filter(source=>state.feed.stories.some(item=>item.sector===state.sector&&item.source_id===source.id)).map(source=>`<option value="${source.id}" ${newsLibrary.viewSource===source.id?'selected':''}>${escapeHtml(source.name)}</option>`).join('')}</select></label><button class="control" data-page="settings">管理本领域来源 ↗</button></div>`);
    }
    if(progress.running)queueNewsPoll();
  }
};

const detailBeforeNewsLibrary=detail;
detail=function(item){detailBeforeNewsLibrary(item);if(item.time_kind==='first_seen')$('#detail-body').insertAdjacentHTML('afterbegin','<div class="notice">该来源未提供可靠发布时间；列表所示为首次发现时间，并非新闻实际发布时间。</div>')};

document.addEventListener('input',event=>{
  if(event.target.id==='news-source-query'){newsLibrary.query=event.target.value;$('#news-source-grid').innerHTML=newsSourceCards();}
  if(event.target.id==='news-rsshub-base'){newsLibrary.draft.rsshub_base=event.target.value;markNewsDirty();}
});
document.addEventListener('change',event=>{
  const target=event.target;
  if(target.dataset.sourceToggle){
    const selected=new Set(newsLibrary.draft.selections[newsLibrary.industry]||[]);
    if(target.checked)selected.add(target.dataset.sourceToggle);else selected.delete(target.dataset.sourceToggle);
    newsLibrary.draft.selections[newsLibrary.industry]=[...selected];
    target.closest('.source-card').classList.toggle('selected',target.checked);markNewsDirty();
  }
  const mapping={'news-source-language':'language','news-source-kind':'kind','news-only-enabled':'onlyEnabled','news-view-industry':'viewIndustry','news-view-source':'viewSource'};
  if(mapping[target.id]){newsLibrary[mapping[target.id]]=target.type==='checkbox'?target.checked:target.value;render();}
  if(target.id==='news-mode'){newsLibrary.draft.mode=target.value;markNewsDirty();}
  if(target.id==='news-refresh-minutes'){newsLibrary.draft.refresh_minutes=Number(target.value);markNewsDirty();}
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  const data=button.dataset;
  if(data.sector){newsLibrary.viewIndustry='all';newsLibrary.viewSource='all';newsLibrary.domain=data.sector;const domain=newsLibrary.catalog?.domains.find(item=>item.id===data.sector);if(domain)newsLibrary.industry=domain.industries[0].id;render();}
  if(data.newsDomain){newsLibrary.domain=data.newsDomain;newsLibrary.industry=newsLibrary.catalog.domains.find(item=>item.id===data.newsDomain).industries[0].id;render();}
  if(data.newsIndustry){newsLibrary.industry=data.newsIndustry;render();}
  if(!data.newsAction&&!data.newsTest)return;
  try {
    const action=data.newsAction;
    if(action==='reload'){await loadNewsCatalog();render();return;}
    const draft=newsLibrary.draft;
    if(action==='recommend')draft.selections[newsLibrary.industry]=[...newsLibrary.catalog.defaults.selections[newsLibrary.industry]];
    if(action==='select-visible')draft.selections[newsLibrary.industry]=[...new Set([...(draft.selections[newsLibrary.industry]||[]),...visibleNewsSources().map(source=>source.id)])];
    if(action==='clear-industry')draft.selections[newsLibrary.industry]=[];
    if(action==='recommend-domain'||action==='clear-domain')for(const industry of newsLibrary.catalog.domains.find(item=>item.id===newsLibrary.domain).industries)draft.selections[industry.id]=action==='clear-domain'?[]:[...newsLibrary.catalog.defaults.selections[industry.id]];
    if(['recommend','select-visible','clear-industry','recommend-domain','clear-domain'].includes(action)){markNewsDirty();render();return;}
    if(action==='save'){
      const result=await request('/api/dashboard/sources/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(draft)});
      newsLibrary.catalog=result;newsLibrary.draft=structuredClone(result.settings);newsLibrary.dirty=false;
      toast('来源选择已保存，重启后沿用');
    }
    if(action==='refresh'&&newsLibrary.dirty){toast('请先保存来源选择，再抓取');return;}
    if(action==='refresh'||data.newsTest||(action==='save'&&draft.mode==='builtin')){
      await request('/api/dashboard/sources/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data.newsTest?{source_ids:[data.newsTest]}:{})});
      await loadNewsCatalog(true);queueNewsPoll();toast(data.newsTest?'正在测试此源':'已开始采集公开资讯');
    }
    await load();
  } catch(error){newsLibrary.error=error.message;toast(error.message);render();}
});

const refreshBeforeNewsLibrary=$('#refresh').onclick;
$('#refresh').onclick=async()=>{
  if(!workPages.has(state.page)&&state.feed?.collection){
    try{await request('/api/dashboard/sources/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});queueNewsPoll();}catch(error){toast(error.message);}
  }
  await refreshBeforeNewsLibrary();
};
setInterval(async()=>{
  if(document.hidden)return;
  try{state.feed=await request('/api/dashboard/feed');if(!workPages.has(state.page))render();}catch{}
},60000);
