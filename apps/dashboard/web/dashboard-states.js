/* Existing dashboard: show usable data, group missing inputs into an actionable status. */
(() => {
  const present = v => v != null && Number.isFinite(Number(v));
  const stamp = () => state.feed?.market?.updated_at ? `行情状态更新 ${date(state.feed.market.updated_at)}` : '行情尚无成功更新时间';
  const statusBlock = (title,reason,actions='') => `<div class="data-state"><b>${escapeHtml(title)}</b><p>${escapeHtml(reason)}</p><div class="actions">${actions||'<button class="control" data-action="retry">刷新数据</button><a class="control" href="/?page=settings">检查数据接入 ↗</a>'}</div></div>`;
  const originalMacro = macroPanel;
  macroPanel = function(){
    const available=(state.macro?.indicators||[]).filter(i=>present(i.value));
    if(!available.length)return '';
    return originalMacro().replace(/另有 (\d+) 项仍在同步：/,'另有 $1 项尚未获取：');
  };
  commoditiesPanel = function(){
    const items=(state.feed?.market?.commodities||[]).filter(i=>present(i.price));
    if(!items.length)return '';
    return panel('商品价格',`<div class="panel-body"><div class="macro-grid">${items.map(i=>`<div class="macro-cell" title="${escapeHtml(i.source||'来源待核验')} · ${escapeHtml(i.date||'日期待核验')}"><small>${escapeHtml(i.name)}</small><b>${number(i.price,i.price>=1000?0:2)}</b><em>${present(i.change_pct)?signed(i.change_pct)+'% · ':''}${escapeHtml(i.unit||'')}</em></div>`).join('')}</div><div class="section-caption">${escapeHtml(stamp())} · 行情按各来源时间展示</div></div>`,'','COMMODITY / 商品行情');
  };
  benchmarksPanel = function(){
    const market=state.feed?.market||{},items=(market.benchmarks||[]).filter(i=>present(i.price));
    if(!items.length)return '';
    return panel('市场基准',`<div class="panel-body"><div class="macro-grid">${items.map(i=>`<div class="macro-cell"><small>${escapeHtml(i.name)}</small><b>${number(i.price,2)}</b>${present(i.change_pct)?`<em class="${tone(i.change_pct)}">${signed(i.change_pct)}%</em>`:'<em>涨跌待更新</em>'}</div>`).join('')}</div><div class="section-caption">${escapeHtml(market.source_label||'公开行情接口')} · ${escapeHtml(stamp())}</div></div>`,'','BENCHMARK / 市场基准');
  };
  tabs = function(){return `<div class="sector-tabs">${state.feed.sectors.map(i=>`<button data-sector="${escapeHtml(i.id)}" class="${i.id===state.sector?'active':''}" title="${escapeHtml(i.latest_date?'数据截至 '+i.latest_date:'行情待更新')}">${escapeHtml(i.name)}${present(i.change)?` <span class="tiny ${tone(i.change)}">${signed(i.change)}%</span>`:''}</button>`).join('')}</div>`;};
  const originalTrend = trendPanel;
  trendPanel = function(){return trendPoints().length?originalTrend():'';};
  const originalHotspots = hotspots;
  hotspots = function(){return stories().length?originalHotspots():'';};
  companyPanel = function(){const items=companies().slice(0,5);if(!items.length)return '';return panel('重点公司',`<div class="panel-body">${items.map(item=>`<div class="company-mini"><span class="company-logo">${escapeHtml(item.name.slice(0,1))}</span><div><h3>${escapeHtml(item.name)}</h3><small>${escapeHtml(item.symbol)} · ${escapeHtml(item.focus)}</small></div>${present(item.change)?`<span class="${tone(item.change)}">${signed(item.change)}%</span>`:'<small class="muted">行情待更新</small>'}</div>`).join('')}</div>`,`<button data-page="companies">全部公司 ↗</button>`);};
  metrics = function(){
    const item=sector()||{};
    const cells=[];
    const metric=(label,value,meta)=>`<div class="metric"><div class="label">${label}</div><div class="value">${value}</div><div class="meta">${meta}</div></div>`;
    if(present(item.change))cells.push(metric('行业涨跌幅',`<span class="${tone(item.change)}">${signed(item.change)}<small>%</small></span>`,escapeHtml(item.latest_date||stamp())));
    if(present(item.net_flow))cells.push(metric('主力净流入',`<span class="${tone(item.net_flow)}">${signed(item.net_flow)}</span>`,escapeHtml(state.feed.flow_unit||'亿元')));
    else if(present(item.amount))cells.push(metric('成交活跃度',number(item.amount),escapeHtml(item.amount_metric||'成交额指标，以来源口径为准')));
    if(present(item.heat))cells.push(metric('行业关注热度',`${number(item.heat,0)}<small>/ 100</small>`,'新闻与市场活跃度综合分，不是收益预测'));
    cells.push(metric('时间窗口内资讯',`${stories().length}<small>条</small>`,present(item.news_count_24h)?`24 小时资讯 ${item.news_count_24h} 条`:'按所选领域与时间窗口统计'));
    const collection=state.feed.collection||{};
    if(cells.length<4&&present(collection.enabled_sources))cells.push(metric('已启用公开源',`${collection.enabled_sources}<small>个</small>`,'按已保存的资讯订阅配置统计'));
    if(cells.length<4&&present(collection.failed_sources))cells.push(metric('待恢复来源',`${collection.failed_sources}<small>个</small>`,collection.failed_sources?'保留上次成功内容，可在设置中查看原因':'当前没有记录到来源连接失败'));
    return `<div class="metric-grid">${cells.join('')}</div>`;
  };
  flowPanel = function(){
    const available=state.feed.sectors.filter(i=>present(i.net_flow));
    if(!available.length)return '';
    const maximum=Math.max(1,...available.map(i=>Math.abs(i.net_flow)));
    return panel('领域资金流向',`<div class="panel-body">${[...available].sort((a,b)=>b.net_flow-a.net_flow).map((item,index)=>`<button class="flow-row" data-sector="${escapeHtml(item.id)}" style="width:100%;text-align:left"><span class="rank">${String(index+1).padStart(2,'0')}</span><span>${escapeHtml(item.name)}</span><div class="flow-track"><span style="width:${Math.abs(item.net_flow)/maximum*100}%;background:${item.net_flow>=0?'var(--green)':'var(--red)'}"></span></div><span class="flow-value ${tone(item.net_flow)}">${signed(item.net_flow)}</span></button>`).join('')}<div class="section-caption">${available.length}/${state.feed.sectors.length} 个领域有数据 · 单位：${escapeHtml(state.feed.flow_unit)} · ${escapeHtml(stamp())}</div></div>`,'','CAPITAL / 资金动向');
  };
  const originalNews=newsRows;
  newsRows=function(items){return items.length?originalNews(items):statusBlock(state.page==='saved'?'当前条件下没有收藏资讯':'当前条件下没有匹配资讯',state.page==='saved'?'收藏原文后可在此集中查看；也可放宽领域与时间筛选。':`${state.filter==='all'?'资讯':categories[state.filter]||'资讯'}暂无匹配结果。可调整时间范围或检查该领域是否启用了来源。`,'<button class="control" data-filter="all">查看全部类型</button><a class="control" href="/?page=settings">管理资讯来源 ↗</a>');};
  function health(){
    const market=state.feed.market||{},item=sector()||{},missing=[];
    if(!present(item.change))missing.push('行业涨跌幅');
    if(!trendPoints().length)missing.push('所选窗口的行业走势');
    if(!state.feed.sectors.some(i=>present(i.net_flow)))missing.push('领域资金流');
    if(!(market.commodities||[]).some(i=>present(i.price)))missing.push('商品行情');
    if(!(market.benchmarks||[]).some(i=>present(i.price)))missing.push('市场基准');
    const macroMissing=(state.macro?.indicators||[]).filter(i=>!present(i.value));
    if(!state.macro)missing.push('宏观指标');
    else if(macroMissing.length)missing.push(`${macroMissing.length} 项宏观指标`);
    if(!missing.length)return '';
    const phase=market.status==='refreshing'||market.status==='cold'?'首次数据正在获取，刷新后查看已完成项。':market.status==='failed'?'最近一次行情请求失败，已有数据继续保留。':'当前来源尚未返回这些项目，不显示空图或填入推测值。';
    return `<section class="data-health-summary"><div><b>数据待补齐 · ${escapeHtml(missing.join('、'))}</b><p>${escapeHtml(phase)} ${escapeHtml(state.macroError?'宏观读取失败：'+state.macroError:'')}</p><small>${escapeHtml(stamp())} · 资讯更新 ${date(state.feed.updated_at)}</small></div><div class="actions"><button class="control" data-action="retry">刷新数据</button><a class="control" href="/?page=settings">数据接入设置 ↗</a><a class="control" href="/research">查看 AI 研究 ↗</a></div></section>`;
  }
  const previousRender=render;
  render=function(){
    previousRender();
    if(!state.feed)return;
    if(['overview','trends'].includes(state.page)){
      const summary=health();
      const metricsElement=document.querySelector('#main .metric-grid');
      if(summary&&metricsElement)metricsElement.insertAdjacentHTML('afterend',summary);
    }
    if(state.page==='companies'){
      document.querySelectorAll('.company-card').forEach((card,index)=>{
        const item=companies()[index];if(!item)return;const fields=[];
        if(present(item.change))fields.push(`当日涨跌 <b class="${tone(item.change)}">${signed(item.change)}%</b>`);
        for(const [key,label,digits] of [['price','现价',2],['market_cap','市值（亿）',0],['pe','PE',1],['pb','PB',2]])if(present(item.quote?.[key]))fields.push(`${label} ${number(item.quote[key],digits)}`);
        card.querySelector('p').innerHTML=fields.length?fields.join(' · '):'公司行情与估值尚未获取。可先查看公告与资讯，待来源返回后更新。';
      });
    }
  };
  if(state.feed)render();
})();
