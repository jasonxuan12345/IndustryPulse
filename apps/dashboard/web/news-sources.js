const newsLibrary = {
  catalog: null,
  draft: null,
  domain: 'semiconductor',
  industry: 'chip-design',
  query: '',
  language: 'all',
  kind: 'all',
  onlyEnabled: false,
  dirty: false,
  loading: false,
  error: '',
  poll: null
};

const sourceKindNames = {
  rss: '直接 RSS',
  wallstreetcn: '公开资讯接口',
  rsshub: 'RSSHub 社区源',
  search: '新闻聚合搜索'
};
const sourceCategoryNames = {
  media: '专业媒体',
  official: '官方发布',
  research: '研究机构',
  aggregator: '新闻聚合'
};
const sourceHealthNames = {
  never: '尚未抓取',
  ok: '读取正常',
  empty: '读取成功 · 暂无条目',
  stale: '连接失败 · 保留旧数据',
  error: '暂时不可用'
};

function availableNewsSources(industryId = newsLibrary.industry) {
  if (!newsLibrary.catalog) return [];
  const domain = newsLibrary.catalog.domains.find(item =>
    item.industries.some(industry => industry.id === industryId)
  );
  if (!domain) return [];
  return newsLibrary.catalog.sources.filter(
    source =>
      (source.domains.includes('*') || source.domains.includes(domain.id)) &&
      (!source.industry || source.industry === industryId)
  );
}

function visibleNewsSources() {
  const enabled = newsLibrary.draft?.selections?.[newsLibrary.industry] || [];
  return availableNewsSources().filter(source =>
    (newsLibrary.language === 'all' || source.language === newsLibrary.language) &&
    (newsLibrary.kind === 'all' || source.kind === newsLibrary.kind) &&
    (!newsLibrary.onlyEnabled || enabled.includes(source.id)) &&
    `${source.name} ${source.url} ${source.category}`
      .toLowerCase()
      .includes(newsLibrary.query.toLowerCase())
  );
}

function newsSourceCards() {
  const enabled = newsLibrary.draft?.selections?.[newsLibrary.industry] || [];
  return visibleNewsSources().map(source => {
    const health = source.status;
    const link = source.kind === 'rsshub'
      ? newsLibrary.draft.rsshub_base.replace(/\/$/, '') + source.route
      : source.url;
    return `<article class="source-card ${enabled.includes(source.id) ? 'selected' : ''}">
      <label class="source-card-top">
        <input type="checkbox" data-source-toggle="${escapeHtml(source.id)}" ${enabled.includes(source.id) ? 'checked' : ''}>
        <span><b>${escapeHtml(source.name)}</b><small>${source.language === 'zh' ? '中文' : '英文'} · ${sourceCategoryNames[source.category] || '专业媒体'}</small></span>
        ${source.recommended ? '<span class="source-recommended">推荐</span>' : ''}
      </label>
      <div class="source-card-tags">
        <span>${sourceKindNames[source.kind] || escapeHtml(source.kind)}</span>
        <span class="source-health ${health.state}">${sourceHealthNames[health.state] || escapeHtml(health.state)}</span>
      </div>
      <div class="source-card-meta">缓存 ${health.count} 条${health.last_success ? ' · 成功 ' + date(health.last_success) : ''}</div>
      ${health.error ? `<p class="source-card-error" title="${escapeHtml(health.error)}">${escapeHtml(health.error)}</p>` : ''}
      <div class="source-card-actions">
        <a href="${escapeHtml(link)}" target="_blank" rel="noopener">订阅地址 ↗</a>
        <button type="button" data-news-test="${escapeHtml(source.id)}">测试此源</button>
      </div>
    </article>`;
  }).join('') || '<div class="empty">没有匹配的来源，试试清除搜索或筛选。</div>';
}

function sourceSettingsPanel() {
  if (!newsLibrary.catalog?.domains?.length) {
    return panel('内置资讯源', `<div class="panel-body">
      <div class="${newsLibrary.error ? 'wb-error' : 'loading'}">${escapeHtml(newsLibrary.error || '正在读取内置来源库…')}</div>
      <button type="button" class="control" data-news-action="reload">重新读取</button>
    </div>`, '', 'PUBLIC INTELLIGENCE');
  }
  const catalog = newsLibrary.catalog;
  const draft = newsLibrary.draft;
  const domain = catalog.domains.find(item => item.id === newsLibrary.domain) || catalog.domains[0];
  const selected = new Set(Object.values(draft.selections || {}).flat()).size;
  const progress = catalog.status;

  return `<section class="panel source-library">
    <div class="source-library-hero">
      <div>
        <p class="eyebrow">PUBLIC INTELLIGENCE / 公开资讯</p>
        <h2>按领域管理消息源</h2>
        <p>勾选并保存后自动周期采集，原始链接与发布时间完整保留。</p>
      </div>
      <div class="source-library-numbers">
        <b>${catalog.sources.length}<small>内置通道</small></b>
        <b>${catalog.domains.length}<small>研究领域</small></b>
        <b>${selected}<small>已选通道</small></b>
      </div>
    </div>
    <div class="panel-body">
      <div class="source-mode-line">
        <label>资讯模式
          <select id="news-mode">
            <option value="builtin" ${draft.mode === 'builtin' ? 'selected' : ''}>内置公开资讯源 · 推荐</option>
            <option value="custom" ${draft.mode === 'custom' ? 'selected' : ''}>自定义 JSON / 外部接口</option>
            <option value="demo" ${draft.mode === 'demo' ? 'selected' : ''}>离线演示数据</option>
          </select>
        </label>
        <label>自动更新
          <select id="news-refresh-minutes">
            ${[5, 15, 30, 60, 180, 1440].map(v => `<option value="${v}" ${draft.refresh_minutes === v ? 'selected' : ''}>${v < 60 ? v + ' 分钟' : v / 60 + ' 小时'}</option>`).join('')}
          </select>
        </label>
        <span class="source-save-state" id="news-dirty">${newsLibrary.dirty ? '● 有未保存的修改' : '✓ 配置已保存'}</span>
      </div>
      <div class="actions" style="margin-bottom:18px">
        <button type="button" class="control primary" data-news-action="save">保存来源选择</button>
        <button type="button" class="control" data-news-action="refresh" ${progress.running ? 'disabled' : ''}>↻ 立即抓取已启用源</button>
      </div>
      <div class="source-domain-tabs">
        ${catalog.domains.map(item => `<button type="button" data-news-domain="${item.id}" class="${item.id === domain.id ? 'active' : ''}">${escapeHtml(item.name)}</button>`).join('')}
      </div>
      <div class="source-industry-tabs">
        ${domain.industries.map(ind => `<button type="button" data-news-industry="${ind.id}" class="${ind.id === newsLibrary.industry ? 'active' : ''}">${escapeHtml(ind.name)} <small>${(draft.selections[ind.id] || []).length} 源</small></button>`).join('')}
      </div>
      <div class="source-toolbar">
        <input id="news-source-query" type="search" aria-label="搜索资讯源" placeholder="搜索来源名称或网址…" value="${escapeHtml(newsLibrary.query)}">
        <select id="news-source-language" aria-label="来源语言">
          <option value="all">全部语言</option>
          <option value="zh" ${newsLibrary.language === 'zh' ? 'selected' : ''}>中文</option>
          <option value="en" ${newsLibrary.language === 'en' ? 'selected' : ''}>英文</option>
        </select>
        <select id="news-source-kind" aria-label="来源类型">
          <option value="all">全部类型</option>
          ${Object.entries(sourceKindNames).map(([k, v]) => `<option value="${k}" ${newsLibrary.kind === k ? 'selected' : ''}>${v}</option>`).join('')}
        </select>
        <label><input type="checkbox" id="news-only-enabled" ${newsLibrary.onlyEnabled ? 'checked' : ''}> 仅看已选</label>
      </div>
      <div class="source-bulk">
        <button type="button" data-news-action="recommend">本行业推荐</button>
        <button type="button" data-news-action="select-visible">全选筛选结果</button>
        <button type="button" data-news-action="clear-industry">清空本行业</button>
        <span></span>
        <button type="button" data-news-action="recommend-domain">本领域全用推荐</button>
        <button type="button" data-news-action="clear-domain">停用整个领域</button>
      </div>
      <div class="source-grid" id="news-source-grid">${newsSourceCards()}</div>
      ${progress.running ? `<div class="source-progress"><i style="width:${progress.total ? (progress.completed / progress.total) * 100 : 0}%"></i></div>` : ''}
      ${newsLibrary.error ? `<div class="wb-error" role="alert">${escapeHtml(newsLibrary.error)}</div>` : ''}
      <details class="source-advanced">
        <summary>高级：RSSHub 社区实例地址</summary>
        <label class="wb-field">RSSHub 地址
          <input id="news-rsshub-base" type="url" value="${escapeHtml(draft.rsshub_base)}">
        </label>
      </details>
    </div>
  </section>`;
}

function markNewsDirty() {
  newsLibrary.dirty = true;
  const marker = $('#news-dirty');
  if (marker) marker.textContent = '● 有未保存的修改';
}

async function loadNewsCatalog(preserveDraft = true) {
  if (newsLibrary.loading) return;
  newsLibrary.loading = true;
  try {
    const catalog = await request('/api/dashboard/sources');
    newsLibrary.catalog = catalog;
    if (!newsLibrary.draft || !preserveDraft) {
      newsLibrary.draft = structuredClone(catalog.settings);
      newsLibrary.dirty = false;
    }
    newsLibrary.error = '';
  } catch (error) {
    newsLibrary.error = error.message;
  } finally {
    newsLibrary.loading = false;
    if (state.page === 'settings') render();
  }
}

function queueNewsPoll() {
  if (newsLibrary.poll) return;
  newsLibrary.poll = setTimeout(async () => {
    newsLibrary.poll = null;
    try {
      await loadNewsCatalog(true);
      state.feed = await request('/api/dashboard/feed');
      const editing = state.page === 'settings' && (
        newsLibrary.dirty || ['INPUT', 'SELECT'].includes(document.activeElement?.tagName)
      );
      if (!editing) render();
      if (newsLibrary.catalog?.status.running) queueNewsPoll();
    } catch (error) {
      newsLibrary.error = error.message;
      if (state.page === 'settings') render();
    }
  }, 2200);
}

document.addEventListener('input', event => {
  if (event.target.id === 'news-source-query') {
    newsLibrary.query = event.target.value;
    const grid = $('#news-source-grid');
    if (grid) grid.innerHTML = newsSourceCards();
  }
  if (event.target.id === 'news-rsshub-base' && newsLibrary.draft) {
    newsLibrary.draft.rsshub_base = event.target.value;
    markNewsDirty();
  }
});

document.addEventListener('change', event => {
  const target = event.target;
  if (target.dataset.sourceToggle && newsLibrary.draft) {
    const selected = new Set(newsLibrary.draft.selections[newsLibrary.industry] || []);
    if (target.checked) selected.add(target.dataset.sourceToggle);
    else selected.delete(target.dataset.sourceToggle);
    newsLibrary.draft.selections[newsLibrary.industry] = [...selected];
    target.closest('.source-card')?.classList.toggle('selected', target.checked);
    markNewsDirty();
  }
  const mapping = {
    'news-source-language': 'language',
    'news-source-kind': 'kind',
    'news-only-enabled': 'onlyEnabled'
  };
  if (mapping[target.id]) {
    newsLibrary[mapping[target.id]] = target.type === 'checkbox' ? target.checked : target.value;
    render();
  }
  if (target.id === 'news-mode' && newsLibrary.draft) {
    newsLibrary.draft.mode = target.value;
    markNewsDirty();
  }
  if (target.id === 'news-refresh-minutes' && newsLibrary.draft) {
    newsLibrary.draft.refresh_minutes = Number(target.value);
    markNewsDirty();
  }
});

document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button) return;
  const data = button.dataset;
  if (data.newsDomain && newsLibrary.catalog) {
    newsLibrary.domain = data.newsDomain;
    const domain = newsLibrary.catalog.domains.find(item => item.id === data.newsDomain);
    if (domain?.industries?.length) newsLibrary.industry = domain.industries[0].id;
    render();
    return;
  }
  if (data.newsIndustry) {
    newsLibrary.industry = data.newsIndustry;
    render();
    return;
  }
  if (!data.newsAction && !data.newsTest) return;
  try {
    const action = data.newsAction;
    if (action === 'reload') {
      await loadNewsCatalog(false);
      return;
    }
    const draft = newsLibrary.draft;
    if (!draft) return;
    if (action === 'recommend') {
      draft.selections[newsLibrary.industry] = [
        ...(newsLibrary.catalog.defaults.selections[newsLibrary.industry] || [])
      ];
    } else if (action === 'select-visible') {
      draft.selections[newsLibrary.industry] = [
        ...new Set([...(draft.selections[newsLibrary.industry] || []), ...visibleNewsSources().map(s => s.id)])
      ];
    } else if (action === 'clear-industry') {
      draft.selections[newsLibrary.industry] = [];
    } else if (action === 'recommend-domain' || action === 'clear-domain') {
      const domain = newsLibrary.catalog.domains.find(item => item.id === newsLibrary.domain);
      for (const ind of (domain?.industries || [])) {
        draft.selections[ind.id] = action === 'clear-domain'
          ? []
          : [...(newsLibrary.catalog.defaults.selections[ind.id] || [])];
      }
    }
    if (['recommend', 'select-visible', 'clear-industry', 'recommend-domain', 'clear-domain'].includes(action)) {
      markNewsDirty();
      render();
      return;
    }
    if (action === 'save') {
      const result = await request('/api/dashboard/sources/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft)
      });
      newsLibrary.catalog = result;
      newsLibrary.draft = structuredClone(result.settings);
      newsLibrary.dirty = false;
      toast('来源选择已保存');
    }
    if (action === 'refresh' && newsLibrary.dirty) {
      toast('请先保存来源选择，再抓取');
      return;
    }
    if (action === 'refresh' || data.newsTest || (action === 'save' && draft.mode === 'builtin')) {
      await request('/api/dashboard/sources/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data.newsTest ? { source_ids: [data.newsTest] } : {})
      });
      await loadNewsCatalog(true);
      queueNewsPoll();
      toast(data.newsTest ? '正在测试此源' : '已开始采集公开资讯');
    }
    await load();
  } catch (error) {
    newsLibrary.error = error.message;
    toast(error.message);
    render();
  }
});
