const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

const state = {
  page: 'radar',
  sector: 'semiconductor',
  range: 90,
  search: '',
  feed: null,
  radar: null,
  macro: null,
  smartMoney: null,
  history: null,
  review: null,
  leads: null,
  health: null,
  verifyResult: null,
  aiBusy: false,
  sigBusy: false,
  verifyBusy: false,
  smSyncBusy: false,
  smHoldingSector: '',
  smHoldings: null,
  smHoldingsError: '',
  smParseBusy: false,
  error: ''
};

const pageNames = {
  radar: '早期雷达',
  sector: '行业透视',
  smart: '机构动向',
  verify: '投喂核验 & 回看',
  settings: '模型与数据源设置'
};

// news-sources.js 兼容集合：非资讯视图避免自动整页刷新打断表单输入
const workPages = new Set(['smart', 'verify', 'settings']);

const escapeHtml = v => String(v ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));

const date = iso => iso
  ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(iso))
  : '—';

const signed = (v, u = '%') => (v == null || Number.isNaN(Number(v)))
  ? '<span class="dash">—</span>'
  : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}${u}`;

function toast(msg) {
  const box = $('#toast');
  if (!box) return;
  box.textContent = msg;
  box.style.display = 'block';
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { box.style.display = 'none'; }, 2800);
}

async function request(url, opts = {}) {
  const res = await fetch(url, opts);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!res.ok) throw new Error(data.detail || data.message || `请求失败 (${res.status})`);
  return data;
}

function panel(title, body, right = '', eyebrow = '') {
  return `<section class="panel">
    <div class="panel-head">
      <div>${eyebrow ? `<p class="eyebrow">${escapeHtml(eyebrow)}</p>` : ''}<h2>${escapeHtml(title)}</h2></div>
      <div>${right}</div>
    </div>
    ${body}
  </section>`;
}

function zoneBadge(zone, label) {
  const map = {
    LURKING: 'badge-lurking',
    RISING: 'badge-rising',
    FALLING: 'badge-falling',
    CROWDED: 'badge-crowded',
    HIGH_RISK: 'badge-highrisk',
    NEUTRAL: 'badge-neutral',
    UNKNOWN: 'badge-neutral'
  };
  return `<span class="zone-badge ${map[zone] || 'badge-neutral'}">${escapeHtml(label || zone || '—')}</span>`;
}

// ------------------------------------------------------------------ 四象限 SVG 图
function quadrantChart(sectors) {
  const items = (sectors || []).filter(s => s.available && s.position_score != null && s.catalyst_score != null);
  if (!items.length) {
    return '<div class="empty"><b>暂无量化坐标数据</b>请先刷新行情与新闻以生成位置分位。</div>';
  }
  const w = 640, h = 320, pad = 42;
  const xOf = c => pad + (Math.max(0, Math.min(100, c)) / 100) * (w - pad * 2);
  const yOf = p => (h - pad) - (Math.max(0, Math.min(100, p)) / 100) * (h - pad * 2);
  const midX = xOf(45), midY = yOf(50);
  const colorOf = z => ({
    LURKING: '#5ee3b5',
    RISING: '#7db7ff',
    FALLING: '#b7896b',
    CROWDED: '#e8c88d',
    HIGH_RISK: '#f48691',
    NEUTRAL: '#8390a4'
  }[z] || '#8390a4');

  const dots = items.map(s => {
    const cx = xOf(s.catalyst_score).toFixed(1);
    const cy = yOf(s.position_score).toFixed(1);
    const fill = colorOf(s.zone);
    return `<g class="q-dot" data-jump-sector="${escapeHtml(s.id)}">
      <circle cx="${cx}" cy="${cy}" r="7" fill="${fill}" fill-opacity="0.85" stroke="#0b0e14" stroke-width="1.5"></circle>
      <text x="${cx}" y="${Number(cy) - 11}" text-anchor="middle" fill="#dce5f2" font-size="11">${escapeHtml(s.name)}</text>
    </g>`;
  }).join('');

  return `<div class="quadrant-wrap">
    <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="信息催化强度与量化位置四象限">
      <rect x="${midX}" y="${pad}" width="${w - pad - midX}" height="${midY - pad}" fill="#f486910d"/>
      <rect x="${midX}" y="${midY}" width="${w - pad - midX}" height="${h - pad - midY}" fill="#5ee3b512"/>
      <line x1="${pad}" y1="${midY}" x2="${w - pad}" y2="${midY}" stroke="#2b3748" stroke-dasharray="4 4"/>
      <line x1="${midX}" y1="${pad}" x2="${midX}" y2="${h - pad}" stroke="#2b3748" stroke-dasharray="4 4"/>
      <rect x="${pad}" y="${pad}" width="${w - pad * 2}" height="${h - pad * 2}" fill="none" stroke="#252d39"/>
      <text x="${w - pad - 8}" y="${h - pad - 10}" text-anchor="end" fill="#5ee3b5" font-size="10">★ 黄金潜伏区（高催化 + 低位置）</text>
      <text x="${w - pad - 8}" y="${pad + 16}" text-anchor="end" fill="#f48691" font-size="10">▲ 高危拥挤区（高位置 + 资金拥挤）</text>
      <text x="${pad + 8}" y="${pad + 16}" fill="#e8c88d" font-size="10">高位无新增催化</text>
      <text x="${pad + 8}" y="${h - pad - 10}" fill="#8390a4" font-size="10">冷门观望区</text>
      <text x="${w / 2}" y="${h - 10}" text-anchor="middle" fill="#8390a4" font-size="11">横轴：信息与新闻催化强度 (0 → 100) →</text>
      <text x="14" y="${h / 2}" text-anchor="middle" transform="rotate(-90 14 ${h / 2})" fill="#8390a4" font-size="11">纵轴：市场位置与拥挤度 (0 → 100) →</text>
      ${dots}
    </svg>
    <p class="chart-note">点击图上任意行业圆点，可直接跳转到该行业的「行业透视」核验新闻与行情明细。</p>
  </div>`;
}

function trendSvg(points) {
  if (!points || points.length < 2) return '<div class="empty">暂无趋势曲线</div>';
  const slice = points.slice(-state.range);
  const vals = slice.flatMap(p => [p.value, p.benchmark].filter(v => v != null));
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = (max - min) || 1;
  const w = 680, h = 210, pad = 16;
  const x = i => (pad + (i / (slice.length - 1)) * (w - pad * 2)).toFixed(1);
  const y = v => (h - pad - ((v - min) / span) * (h - pad * 2)).toFixed(1);
  const pathFor = key => slice
    .map((p, i) => p[key] != null ? `${i === 0 ? 'M' : 'L'}${x(i)},${y(p[key])}` : '')
    .filter(Boolean).join(' ');
  return `<div class="chart">
    <div class="legend"><span><i></i>行业指数</span><span><i class="purple"></i>沪深300基准</span></div>
    <svg viewBox="0 0 ${w} ${h}">
      <path d="${pathFor('benchmark')}" fill="none" stroke="#ae9aff" stroke-width="1.6" stroke-opacity="0.7"/>
      <path class="line" d="${pathFor('value')}" fill="none" stroke="#5ee3b5" stroke-width="2.2"/>
    </svg>
    <div class="chart-axis"><span>${escapeHtml(slice[0].date)}</span><span>近 ${slice.length} 个交易日</span><span>${escapeHtml(slice[slice.length - 1].date)}</span></div>
  </div>`;
}

// ------------------------------------------------------------------ Page 1: 早期雷达
function renderJudgment(item, fallback = '', label = 'AI 推断') {
  const types = { news_event: '报道事件', sec_filing: 'SEC 公告', cninfo_ann: '巨潮公告',
    twse_pulse: '台股营收', market_metric: '数据快照', announcement: '公告或披露' };
  const sources = new Map((item.evidence || []).map(ref => [ref.id, ref]));
  const facts = (item.facts || []).filter(fact => sources.has(fact.evidence_id));
  const inference = item.inference || fallback;
  return `${facts.length ? `<div class="stack-sm"><b>原文或数据确认的事实</b>
    <ul>${facts.map(fact => {
      const source = sources.get(fact.evidence_id);
      const description = `${types[source.type] || '来源'} · ${source.title || ''}`;
      return `<li>「${escapeHtml(fact.quote)}」 <small>
        ${source.url ? `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener">${escapeHtml(description)}</a>` : escapeHtml(description)}
        ${source.date ? ` · ${escapeHtml(String(source.date).slice(0, 10))}` : ''}
        ${source.reprints ? ` · ${source.reprints} 篇相关报道` : ''}
      </small></li>`;
    }).join('')}</ul></div>` : ''}
    ${inference ? `<p class="signal-body"><b>${escapeHtml(label)}</b> · ${escapeHtml(inference)}</p>` : ''}
    ${(item.gaps || []).length ? `<div class="muted"><b>待验证缺口</b>
      <ul>${item.gaps.map(gap => `<li>${escapeHtml(gap)}</li>`).join('')}</ul></div>` : ''}`;
}

function renderPilotChains(chains) {
  return chains.map(chain => {
    const { evidence, quote } = chain.shock;
    const shared = renderJudgment({ evidence: [evidence], facts: [{ evidence_id: evidence.id, quote }] });
    const nodes = chain.nodes.map(node => `<article class="signal-card">
      <div class="signal-top"><h3>${escapeHtml(node.name)}</h3>
        <span class="tag">利润${escapeHtml(node.profit_status)}</span></div>
      ${renderJudgment(
        { ...node, facts: node.facts.filter(fact => fact.evidence_id !== evidence.id) },
        '', '条件推演'
      )}
      <p class="muted"><b>成立条件</b> · ${node.conditions.map(escapeHtml).join(' · ')}</p>
      ${node.basket ? `<details>
        <summary>市场观察篮子 · ${escapeHtml(node.basket.effective_from)} 生效</summary>
        <ul>${node.basket.members.map(member => `<li>${escapeHtml(member.name)}
          ${escapeHtml(member.symbol)} · ${escapeHtml(member.weight * 100)}% · ${escapeHtml(member.reason)}</li>`).join('')}</ul>
      </details>` : (node.basket_note ? `<p class="muted">${escapeHtml(node.basket_note)}</p>` : '')}
    </article>`).join('');
    return panel(chain.name, `<div class="panel-body">${shared}<div class="card-grid">${nodes}</div></div>`);
  }).join('');
}

function renderRadar() {
  const radarData = state.radar || {};
  const sectors = radarData.sectors || [];
  const report = radarData.latest_ai_report || {};
  const hasReport = Boolean(report && report.run_id);
  const macro = report.macro_regime || {};
  const early = report.early_opportunities || [];
  const highRisk = report.high_risk_warnings || [];
  const judgmentLabel = report.mode === 'rule_fallback' ? '规则判断' : 'AI 推断';

  const aiHeaderRight = `
    <div class="toolbar">
      ${hasReport ? `<span class="pill ${report.mode === 'ai' ? '' : 'demo'}">${report.mode === 'ai' ? `AI · ${escapeHtml(report.model || '')}` : '规则基线'} · ${date(report.generated_at)}</span>` : ''}
      <button class="control" data-action="refresh-signals" ${state.sigBusy ? 'disabled' : ''}>${state.sigBusy ? '拉取异动中…' : '↻ 刷新硬异动源'}</button>
      <button class="control primary" data-action="run-butterfly" ${state.aiBusy ? 'disabled' : ''}>${state.aiBusy ? '正在并行严审…' : '✧ 运行 AI 蝴蝶效应扫描'}</button>
    </div>`;

  const anomalies = radarData.anomalies || {};
  const highlights = anomalies.highlights || [];
  const disclosures = anomalies.disclosures || [];
  const anomalyBar = highlights.length
    ? `<div class="panel-body">
        <div class="tag-row" style="margin-bottom:12px">
          ${Object.entries(anomalies.channels || {}).map(([k, v]) => `<span class="pill ${v.status === 'ok' ? '' : 'warn-pill'}">${escapeHtml(k)}: ${v.status === 'ok' ? `${v.count}条` : '失败'}</span>`).join('')}
        </div>
        <div class="card-grid">${highlights.slice(0, 6).map(h => `<div class="sm-inst-card">
          <div class="signal-top"><span class="tag">${escapeHtml(h.tag)}</span><small class="muted">${escapeHtml(h.sector)} · ${escapeHtml(h.date || '')}</small></div>
          <div style="font-size:12.5px;font-weight:500;margin:6px 0">${escapeHtml(h.title)}</div>
          <div class="muted" style="font-size:11.5px">${escapeHtml(h.detail || '')}</div>
          ${h.url ? `<div style="margin-top:6px;font-size:11px"><a href="${escapeHtml(h.url)}" target="_blank" rel="noopener">查看原始公告/来源 ↗</a></div>` : ''}
        </div>`).join('')}</div>
      </div>`
    : '<div class="panel-body"><p class="muted">尚未拉取全网硬异动（业绩预增簇 / 巨潮公告 / 台股月度营收 / 海外映射）。点击「刷新硬异动源」或直接运行扫描即可自动采集。</p></div>';

  const disclosureList = disclosures.length ? `<details class="panel-body">
    <summary>SEC 披露记录 (${disclosures.length})</summary>
    ${disclosures.map(item => `<p>
      <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a>
      <span class="tag">${item.content_verified ? '有原文摘录' : '待核对正文'}</span>
      ${(item.items || []).length ? `<small>事项 ${escapeHtml(item.items.join('、'))}</small>` : ''}
    </p>`).join('')}
  </details>` : '';

  const macroSummary = hasReport
    ? `<div class="panel-body">
        ${renderJudgment(macro, macro.summary || '暂无宏观研判', judgmentLabel)}
        <div class="tag-row">
          ${(macro.favored_styles || []).map(t => `<span class="pill">有利方向：${escapeHtml(t)}</span>`).join('')}
          ${(macro.key_risks || []).map(t => `<span class="pill warn-pill">关注风险：${escapeHtml(t)}</span>`).join('')}
        </div>
      </div>`
    : `<div class="panel-body"><p class="muted">尚未运行过扫描。点击右上角「运行 AI 蝴蝶效应扫描」，系统会先抓取硬异动并结合 180 日止跌位置与机构共识做严苛筛选。</p></div>`;

  const earlyCards = early.length
    ? early.map(item => `<article class="signal-card early-card">
          <div class="signal-top">
            <div>
              <strong>${escapeHtml(item.sector_name || item.sector)}</strong>
              ${zoneBadge(item.zone, item.zone_label)}
            </div>
            <button class="control" data-jump-sector="${escapeHtml(item.sector)}">进行业透视 ↗</button>
          </div>
          <h3>${escapeHtml(item.title)}</h3>
          ${(item.hard_triggers || []).length ? `<div class="tag-row" style="margin-bottom:8px">${item.hard_triggers.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('')}</div>` : ''}
          ${item.sub_theme_and_targets ? `<p class="disproof"><b>细分方向与标的：</b>${escapeHtml(item.sub_theme_and_targets)}</p>` : ''}
          ${renderJudgment(item, item.butterfly_chain, judgmentLabel)}
          <div class="signal-meta">
            ${item.quant_alignment ? `<span>📊 ${escapeHtml(item.quant_alignment)}</span>` : ''}
            ${item.smart_money_note ? `<span>⚖ ${escapeHtml(item.smart_money_note)}</span>` : ''}
          </div>
          ${item.disproof_signal ? `<p class="disproof"><b>证伪红线：</b>${escapeHtml(item.disproof_signal)}</p>` : ''}
        </article>`).join('')
    : (hasReport
        ? '<div class="empty"><b>当前 0 个早期潜伏板块</b>经严审，目前暂无同时满足「真实硬异动 + 180日低位止跌企稳 + 未拥挤」的行业，建议防守观望，宁缺毋滥。</div>'
        : '<div class="empty">尚未运行过扫描。</div>');

  const riskCards = highRisk.length
    ? highRisk.map(item => `<article class="signal-card risk-card">
        <div class="signal-top">
          <div>
            <strong>${escapeHtml(item.sector_name || item.sector)}</strong>
            ${zoneBadge(item.zone || 'HIGH_RISK', item.zone_label || '高危警示')}
          </div>
          <button class="control" data-jump-sector="${escapeHtml(item.sector)}">核验走势 ↗</button>
        </div>
        <h3>${escapeHtml(item.title)}</h3>
        ${renderJudgment(item, item.reason, judgmentLabel)}
        ${item.disproof_signal ? `<p class="disproof"><b>解除条件：</b>${escapeHtml(item.disproof_signal)}</p>` : ''}
      </article>`).join('')
    : '<div class="empty">当前没有触发高危离场警示的板块。</div>';

  const quantTable = `<div class="panel-body">
    <div class="table-wrap"><table class="radar-table">
      <thead><tr><th>行业</th><th>状态</th><th>硬异动</th><th>位置分</th><th>催化分</th><th>20日涨跌</th><th>相对沪深300</th><th>180日价格分位</th><th>成交拥挤度</th></tr></thead>
      <tbody>${sectors.map(s => `<tr data-jump-sector="${escapeHtml(s.id)}" class="clickable-row">
        <td><b>${escapeHtml(s.name)}</b></td>
        <td>${zoneBadge(s.zone, s.zone_label)}</td>
        <td>${s.hard_signal_count ? `<span class="pill">${s.hard_signal_count}条</span>` : (s.anti_drop ? '<span class="pill">逆势抗跌</span>' : '<span class="muted">0</span>')}</td>
        <td>${s.position_score ?? '—'}</td>
        <td>${s.catalyst_score ?? '—'}</td>
        <td class="${(s.return_20d ?? 0) >= 0 ? 'positive' : 'negative'}">${signed(s.return_20d)}</td>
        <td class="${(s.relative_20d ?? 0) >= 0 ? 'positive' : 'negative'}">${signed(s.relative_20d)}</td>
        <td>${s.price_percentile_60d != null ? `${s.price_percentile_60d}%` : '—'}</td>
        <td>${s.turnover_percentile_60d != null ? `${s.turnover_percentile_60d}%` : '—'}</td>
      </tr>`).join('')}</tbody>
    </table></div>
  </div>`;

  const sectorSignals = (report.sector_signals || []).filter(s => (s.signals || []).length);
  const weakSignalsHtml = sectorSignals.length
    ? `<div class="panel-body card-grid">${sectorSignals.map(group => `<article class="signal-card">
        <div class="signal-top">
          <strong>${escapeHtml(group.sector_name || group.sector)}</strong>
          <button class="control" data-jump-sector="${escapeHtml(group.sector)}">看行业 ↗</button>
        </div>
        <div class="stack-sm" style="margin-top:10px">
          ${(group.signals || []).map(sig => `<div style="border-top:1px solid var(--line);padding-top:8px">
            <div style="font-size:12.5px;font-weight:500">${escapeHtml(sig.headline)} <span class="tag">${escapeHtml(sig.strength)}</span></div>
            ${renderJudgment(sig, sig.chain, 'AI 推断')}
          </div>`).join('')}
        </div>
      </article>`).join('')}</div>`
    : '';
  const watch = (report.watch_list || []).map(item => `<article class="signal-card">
    <h3>${escapeHtml(item.sector_name || item.sector)} · ${escapeHtml(item.status || '观察')}</h3>
    ${renderJudgment(item, item.note, judgmentLabel)}
  </article>`).join('');

  return `
    <div class="page-heading">
      <div><p class="eyebrow">EARLY RADAR / 硬异动 + AI严审 + 180日位置</p><h1>早期雷达<span> · 潜伏与离场</span></h1></div>
      ${aiHeaderRight}
    </div>
    ${panel('全网硬异动探测器（业绩预增簇 / 公告 / 台股月营收 / 海外映射）', anomalyBar + disclosureList, anomalies.updated_at ? `<span class="muted" style="font-size:11px">更新于 ${date(anomalies.updated_at)}</span>` : '', 'HARD ANOMALY DETECTORS')}
    <div style="margin-top:20px">${panel('宏观经济风向与主要矛盾', macroSummary, '', 'MACRO REGIME')}</div>
    ${renderPilotChains(report.chain_assessments || [])}
    <div class="two-col" style="margin-top:20px">
      ${panel('「异常催化 × 180日市场位置」四象限', `<div class="panel-body">${quadrantChart(sectors)}</div>`, '', 'QUANT POSITION MATRIX')}
      ${panel('高危离场警示区', `<div class="panel-body stack">${riskCards}</div>`, '', 'HIGH RISK WARNINGS')}
    </div>
    ${panel('早期潜伏重点板块（硬异动 + 止跌企稳 + 未拥挤）', `<div class="panel-body card-grid">${earlyCards}</div>`, '', 'EARLY OPPORTUNITIES')}
    ${watch ? panel('继续观察', `<div class="panel-body card-grid">${watch}</div>`) : ''}
    ${weakSignalsHtml ? `<div style="margin-top:20px">${panel('各行业 AI 初筛弱信号与传导链', weakSignalsHtml, '', 'WEAK SIGNALS')}</div>` : ''}
    <div style="margin-top:20px">${panel('12 行业量化位置全览', quantTable, '', 'ALL SECTORS QUANT RADAR')}</div>
  `;
}

// ------------------------------------------------------------------ Page 2: 行业透视
function renderSector() {
  const sectors = state.feed?.sectors || [];
  const radars = state.radar?.sectors || [];
  const current = sectors.find(s => s.id === state.sector) || sectors[0];
  const radar = radars.find(r => r.id === (current?.id || state.sector)) || {};
  const q = state.search.trim().toLowerCase();
  const stories = (state.feed?.stories || [])
    .filter(s => s.sector === (current?.id || state.sector))
    .filter(s => !q || `${s.title} ${s.summary} ${s.source}`.toLowerCase().includes(q));
  const companies = (state.feed?.companies || []).filter(c => c.sector === (current?.id || state.sector));
  const macroItems = (state.macro?.indicators || []).filter(m => m.value != null).slice(0, 8);
  const macroUnavailable = state.macro?.unavailable || [];

  const tabs = `<div class="sector-tabs">${sectors.map(s => {
    const r = radars.find(x => x.id === s.id);
    return `<button data-sector="${escapeHtml(s.id)}" class="${s.id === current?.id ? 'active' : ''}">
      ${escapeHtml(s.name)}<span class="tiny">${r?.zone === 'LURKING' ? '✧潜伏' : r?.zone === 'HIGH_RISK' ? '▲高危' : signed(s.change)}</span>
    </button>`;
  }).join('')}</div>`;

  const metrics = `<div class="metric-grid">
    <div class="metric"><div class="label"><span>量化位置状态</span></div><div class="value" style="font-size:20px">${zoneBadge(radar.zone, radar.zone_label)}</div><div class="meta">位置分 ${radar.position_score ?? '—'} · 催化分 ${radar.catalyst_score ?? '—'}</div></div>
    <div class="metric"><div class="label"><span>20日涨跌 / 超额</span></div><div class="value">${signed(radar.return_20d)}</div><div class="meta">相对沪深300 ${signed(radar.relative_20d)}</div></div>
    <div class="metric"><div class="label"><span>60日价格 / 成交分位</span></div><div class="value">${radar.price_percentile_60d != null ? `${radar.price_percentile_60d}%` : '—'}</div><div class="meta">成交拥挤分位 ${radar.turnover_percentile_60d != null ? `${radar.turnover_percentile_60d}%` : '—'}</div></div>
    <div class="metric"><div class="label"><span>24小时新闻与热度</span></div><div class="value">${current?.news_count_24h ?? stories.length}<small>篇</small></div><div class="meta">HeatScore ${current?.heat ?? '—'}</div></div>
  </div>`;

  const rangeButtons = `<div class="range">${[30, 90, 180].map(d => `<button data-range="${d}" class="${state.range === d ? 'active' : ''}">${d}D</button>`).join('')}</div>`;
  const newsList = stories.length
    ? stories.slice(0, 30).map(s => `<div class="news-row">
        <div class="news-marker">▤</div>
        <div class="news-content">
          <button class="news-title" data-story-id="${escapeHtml(s.id)}">${escapeHtml(s.title)}</button>
          <div class="news-meta">
            <span>${escapeHtml(s.source || '公开源')}</span>
            <span>${date(s.published_at)}</span>
            ${s.url ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">查看原文 ↗</a>` : ''}
          </div>
        </div>
      </div>`).join('')
    : '<div class="empty">当前行业暂无匹配新闻。</div>';

  const companyList = companies.length
    ? `<div class="panel-body">${companies.slice(0, 8).map(c => `<div class="company-mini">
        <div class="company-logo">${escapeHtml((c.name || '?')[0])}</div>
        <div><h3>${escapeHtml(c.name)}</h3><small>${escapeHtml(c.symbol || c.focus || '')}</small></div>
        <div class="${(c.change ?? 0) >= 0 ? 'positive' : 'negative'}">${signed(c.change)}</div>
      </div>`).join('')}</div>`
    : '<div class="empty">暂无关联公司或 ETF 数据。</div>';

  const macroPanel = macroItems.length || macroUnavailable.length
    ? panel('核心宏观底座指标', `<div class="panel-body macro-grid">${macroItems.map(m => `<div class="macro-cell">
        <small>${escapeHtml(m.name)}</small>
        <b>${escapeHtml(m.value)}<i>${escapeHtml(m.unit || '')}</i></b>
        <em>${escapeHtml(m.period || '')}</em>
      </div>`).join('')}${macroUnavailable.map(m => `<div class="macro-cell">
        <small>${escapeHtml(m.name)}</small>
        <em>${escapeHtml(m.message)}</em>
      </div>`).join('')}</div>`, '', 'MACRO CONTEXT')
    : '';

  return `
    <div class="page-heading">
      <div><p class="eyebrow">SECTOR DEEP DIVE</p><h1>${escapeHtml(current?.name || '行业透视')}<span> · 新闻原文与量化标尺</span></h1></div>
    </div>
    ${tabs}
    ${metrics}
    <div class="two-col">
      ${panel('行业指数走势与基准对比', `<div class="panel-body">${trendSvg(current?.trend || [])}</div>`, rangeButtons, 'TREND')}
      ${panel('重点龙头与标的', companyList, '', 'COMPANIES')}
    </div>
    <div class="two-col">
      ${panel(`行业最新资讯 (${stories.length})`, newsList, '', 'ORIGINAL NEWS')}
      <div class="stack">
        ${panel('量化风险与位置提示', `<div class="panel-body">${(radar.risk_flags || []).length ? radar.risk_flags.map(f => `<p class="notice">${escapeHtml(f)}</p>`).join('') : '<p class="muted">暂无极端风险标志。</p>'}</div>`, '', 'QUANT SIGNALS')}
        ${macroPanel}
      </div>
    </div>
  `;
}

// ------------------------------------------------------------------ Page 3: 机构动向
function renderHoldingComparisons(sm, sectors) {
  const selected = state.smHoldingSector;
  const sectorNames = Object.fromEntries(sectors.map(s => [s.id, s.name]));
  const labels = { NEW: '本期新增披露', INCREASE: '增加', REDUCE: '减少', HOLD: '持平',
    NOT_DISCLOSED: '本期未披露', REVIEW: '待核实' };
  const qty = n => n == null ? '—' : n.toLocaleString('zh-CN');
  const weight = n => n == null ? '—' : `${n.toFixed(2)}%`;
  const filings = sources => (sources || []).map(f =>
    `<a href="${escapeHtml(f.url)}" target="_blank" rel="noopener">${escapeHtml(f.filing_date)}</a>`).join(' · ');
  const tabs = (sm.holding_sectors || []).map(id => `<button type="button"
    class="control ${selected === id ? 'primary' : ''}" data-sm-sector="${escapeHtml(id)}"
    aria-pressed="${selected === id}">${escapeHtml(sectorNames[id] || (id === 'unmapped' ? '未映射' : id))}</button>`).join(' ');

  const summary = sm.sector_summary?.[selected];
  const consensus = summary?.report_date
    ? `<p class="muted">${escapeHtml(summary.report_date)} · 披露持仓增加 ${summary.increase_count} 家 · 减少 ${summary.reduce_count} 家 · 同行业内有增有减 ${summary.mixed_count} 家</p>`
    : '';
  const details = (state.smHoldings?.institutions || []).map(inst => `<details class="sm-inst-card" open>
      <summary>${escapeHtml(inst.institution_name)} · ${escapeHtml(inst.previous_report_date)} → ${escapeHtml(inst.report_date)}</summary>
      ${inst.sync_error ? `<p class="notice">本次同步失败，以下为上次成功的数据。</p>` : ''}
      ${inst.confidential_omitted || inst.previous_confidential_omitted ? '<p class="muted">相关申报标注有保密持仓。</p>' : ''}
      <p class="muted">上期申报 ${filings(inst.previous_versions)} · 本期申报 ${filings(inst.versions)}</p>
      <div class="table-wrap"><table>
        <thead><tr><th>证券</th><th>持仓状态</th><th>上期 → 本期数量</th><th>上期 → 本期权重</th><th>持仓披露日</th></tr></thead>
        <tbody>${inst.rows.map(row => `<tr>
          <td>${escapeHtml(row.issuer)} <span class="muted">${escapeHtml(row.ticker || row.cusip)} · ${escapeHtml(row.option === 'NONE' ? (row.unit === 'SH' ? '正股' : '本金') : row.option)}</span></td>
          <td>${escapeHtml(labels[row.status] || row.status)}${row.review_reason ? `<br><small>${escapeHtml(row.review_reason)}</small>` : ''}</td>
          <td>${qty(row.previous_quantity)} → ${qty(row.quantity)}${row.quantity_change == null ? '' : `<br><small>${row.quantity_change > 0 ? '+' : ''}${qty(row.quantity_change)}</small>`}</td>
          <td>${weight(row.previous_weight_pct)} → ${weight(row.weight_pct)}${row.weight_change_pp == null ? '' : `<br><small>${row.weight_change_pp > 0 ? '+' : ''}${row.weight_change_pp.toFixed(2)} 个百分点</small>`}</td>
          <td>${filings(row.previous_sources) || '—'} → ${filings(row.sources) || '—'}</td>
        </tr>`).join('')}</tbody>
      </table></div>
    </details>`).join('');
  const message = state.smHoldingsError || (selected
    ? (state.smHoldings ? '该行业暂无可比持仓。' : '正在读取持仓…')
    : (tabs ? '选择行业查看两季持仓变化。' : '同步 SEC 13F 后可查看两季持仓变化。'));
  return `<div class="panel-body stack-sm">${tabs ? `<div>${tabs}</div>` : ''}${consensus}
    ${details || `<p class="muted">${escapeHtml(message)}</p>`}</div>`;
}

async function loadHoldingSector(sector) {
  state.smHoldingSector = sector;
  state.smHoldings = null;
  state.smHoldingsError = '';
  render();
  try {
    const holdings = await request(`/api/smart-money/holdings?sector=${encodeURIComponent(sector)}`);
    if (state.smHoldingSector === sector) state.smHoldings = holdings;
  } catch (e) {
    if (state.smHoldingSector === sector) state.smHoldingsError = e.message;
  }
  render();
}

function renderSmartMoney() {
  const sm = state.smartMoney || { institutions: [], entries: [], sync_errors: {} };
  const sectors = state.feed?.sectors || [];
  const instCards = (sm.institutions || []).map(inst => {
    const filing = inst.latest_filing;
    return `<div class="sm-inst-card">
      <div class="signal-top">
        <b>${escapeHtml(inst.name)}</b>
        <span class="pill ${inst.region === 'US' ? 'demo' : ''}">${escapeHtml(inst.region)}${inst.cik ? ' · 季度 13F' : ''}</span>
      </div>
      <p class="muted" style="font-size:12px;margin:6px 0">${escapeHtml(inst.style)} — ${escapeHtml(inst.notes)}</p>
      <div style="font-size:11px;color:#9aa7b9">
        ${filing
          ? `最新 13F：${escapeHtml(filing.filing_date)}（报告期 ${escapeHtml(filing.report_date)}） · <a href="${escapeHtml(filing.url)}" target="_blank" rel="noopener">EDGAR 原件 ↗</a>
             ${inst.holdings_report_date ? `<br>持仓明细 ${escapeHtml(inst.holdings_previous_report_date)} → ${escapeHtml(inst.holdings_report_date)}` : '<br>暂无连续两季持仓明细'}`
          : (inst.cik ? '尚未同步 SEC 13F 状态' : '国内/产业渠道（支持手动录入笔记）')}
      </div>
      ${sm.sync_errors?.[inst.id] ? `<p class="muted">${escapeHtml(sm.sync_errors[inst.id])}</p>` : ''}
    </div>`;
  }).join('');

  const entryForm = `<form id="sm-form" class="panel-body stack-sm">
    <div class="form-row">
      <label class="wb-field">选择机构
        <select name="institution_id" required>${(sm.institutions || []).map(i => `<option value="${escapeHtml(i.id)}">${escapeHtml(i.name)}</option>`).join('')}</select>
      </label>
      <label class="wb-field">关联行业
        <select name="sector" required>${sectors.map(s => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)}</option>`).join('')}</select>
      </label>
      <label class="wb-field">记录类型
        <select name="kind">
          <option value="view">公开观点</option>
          <option value="holding">持仓摘录</option>
          <option value="capex">企业 CapEx</option>
        </select>
      </label>
      <label class="wb-field">动向方向
        <select name="direction">
          <option value="INCREASE">增配 / 建仓</option>
          <option value="REDUCE">减配 / 撤退</option>
          <option value="VIEW">公开观点 / 纪要</option>
          <option value="HOLD">维持重仓</option>
        </select>
      </label>
      <label class="wb-field">披露日期
        <input type="date" name="disclosed_at" value="${new Date().toISOString().slice(0, 10)}" required>
      </label>
    </div>
    <label class="wb-field">标题 / 核心事件
      <input name="title" placeholder="例如：Q3 13F 增持某半导体设备 / 公开演讲看好电网出海" required>
    </label>
    <label class="wb-field">逻辑摘要与细节
      <textarea name="summary" rows="3" placeholder="记录核心依据、变化幅度或原文要点…" required></textarea>
    </label>
    <label class="wb-field">来源链接（可选）
      <input name="source_url" placeholder="https://...">
    </label>
    <div><button type="submit" class="control primary">保存机构动向</button></div>
  </form>`;

  const entryList = kind => (sm.entries || []).filter(e => e.kind === kind).map(e => `<div class="news-row">
        <div class="news-content">
          <div class="signal-top">
            <div><b>${escapeHtml(e.institution_name)}</b> · <span class="tag">${escapeHtml(e.sector)}</span> · <span class="tag">${escapeHtml(e.direction)}</span></div>
            <button class="control" data-sm-delete="${escapeHtml(e.id)}">删除</button>
          </div>
          <p style="margin:6px 0 4px;font-weight:500">${escapeHtml(e.title)}</p>
          <p class="muted" style="font-size:12px">${escapeHtml(e.summary)}</p>
          <div class="news-meta">
            <span>${escapeHtml(e.disclosed_at)}</span>
            <span style="color:#e8c88d">${escapeHtml(e.lag_warning)}</span>
            ${e.source_url ? `<a href="${escapeHtml(e.source_url)}" target="_blank" rel="noopener">来源 ↗</a>` : ''}
          </div>
        </div>
      </div>`).join('') || '<div class="empty">暂无记录。</div>';

  const coverage = (sm.coverage || []).map(c => `<div class="sm-inst-card">
    <b>${escapeHtml(c.name)}</b> <span class="tag">${escapeHtml(c.frequency)}</span>
    <p class="muted">${escapeHtml(c.coverage_basis)} · ${c.covered_from
      ? `${escapeHtml(c.covered_from)} → ${escapeHtml(c.covered_to)}` : '暂无已录数据'}</p>
    <p>${escapeHtml(c.gap)}</p>
  </div>`).join('');

  const parseForm = `<form id="sm-parse-form" class="panel-body stack-sm">
    <label class="wb-field">直接粘贴一段机构季报摘要 / 13F 新闻 / 知名投资人访谈，AI 自动提取结构化条目并入库：
      <textarea name="text" rows="3" placeholder="例如：段永平 Q3 13F 显示大幅增持…… / 某头部公募基金经理季报表示重点加仓电网设备与出海算力……" required></textarea>
    </label>
    <div><button type="submit" class="control primary" ${state.smParseBusy ? 'disabled' : ''}>${state.smParseBusy ? 'AI 解析入库中…' : '✦ AI 自动拆解并入库'}</button></div>
  </form>`;

  return `
    <div class="page-heading">
      <div><p class="eyebrow">SMART MONEY / 滞后性提醒</p><h1>国内外知名机构动向<span> · 中长期风向与拥挤校验</span></h1></div>
      <div class="toolbar">
        <button class="control" data-action="sync-sec" ${state.smSyncBusy ? 'disabled' : ''}>${state.smSyncBusy ? '同步中…' : '同步 SEC 13F 持仓'}</button>
      </div>
    </div>
    ${panel('实际持仓', renderHoldingComparisons(sm, sectors))}
    <div class="two-col">
      ${panel('公开观点', entryList('view'))}
      ${panel('企业 CapEx', entryList('capex'))}
    </div>
    ${panel('持仓摘录', entryList('holding'))}
    ${panel('数据覆盖', `<div class="panel-body stack-sm">${coverage}</div>`)}
    ${panel('AI 快捷解析机构动向（免手填）', parseForm, '', 'AI AUTO PARSE')}
    <div class="two-col" style="margin-top:20px">
      ${panel('手动录入机构动态 / 大佬观点', entryForm, '', 'ADD OBSERVATION')}
      ${panel('已跟踪机构名录', `<div class="panel-body stack-sm">${instCards}</div>`, '', 'WATCHED INSTITUTIONS')}
    </div>
  `;
}

// ------------------------------------------------------------------ Page 4: 投喂核验 & 回看
function renderLeadLedger(ledger) {
  if (ledger?.error) return `<div class="empty">${escapeHtml(ledger.error)}</div>`;
  const items = ledger?.items || [];
  if (!items.length) return '<div class="empty">蝴蝶效应扫描发现待验证线索后，会在这里记录。</div>';
  const labels = { discovery: '首次发现', observation: '报告期观察', evidence: '新增证据',
    industry: '产业验证', market: '市场确认' };
  return `<div class="panel-body stack-sm">${items.map(lead => `<details class="sm-inst-card">
    <summary><strong>${escapeHtml(lead.title)}</strong>
      <span class="tag">产业 · ${escapeHtml(lead.industry_status)}</span>
      <span class="tag">市场 · ${escapeHtml(lead.market_status)}</span>
      ${lead.provisional ? '<span class="tag">待补环节或报告期</span>' : ''}</summary>
    <p class="muted">首次发现 ${date(lead.first_seen_at)} · 核验截止 ${escapeHtml(lead.check_by)}</p>
    ${lead.validation ? `<p><b>核验条件</b> · ${escapeHtml(lead.validation)}</p>` : ''}
    ${lead.market_members?.length ? `<p class="muted">市场参照 · ${lead.market_members.map(
      member => escapeHtml(member.symbol)
    ).join('、')}</p>` : ''}
    <ol class="stack-sm">${(lead.timeline || []).map(entry => `<li>
        <small class="muted">${date(entry.at)}</small>
        <b>${escapeHtml(labels[entry.type] || entry.type)}</b>
        ${entry.period ? ` · ${escapeHtml(entry.period)}` : ''}
        ${entry.from ? ` · ${escapeHtml(entry.from)} → ${escapeHtml(entry.to)}` : ''}
        ${entry.detail ? `<div>${escapeHtml(entry.detail)}</div>` : ''}
        ${entry.quote ? `<div>「${escapeHtml(entry.quote)}」
          ${entry.source?.url ? `<a href="${escapeHtml(entry.source.url)}" target="_blank" rel="noopener">${escapeHtml(entry.source.title || '来源')}</a>` : ''}
        </div>` : ''}
      </li>`).join('')}</ol>
  </details>`).join('')}</div>`;
}

function renderDualReview(review) {
  if (review?.error) return `<div class="empty">${escapeHtml(review.error)}</div>`;
  const day = review?.selected;
  if (!day) return '<div class="empty">运行蝴蝶效应扫描后生成每日参评快照。</div>';
  const closing = value => value
    ? `<small>${escapeHtml(value.date)}<br>${Object.entries(value.closes).map(
        ([symbol, price]) => `${escapeHtml(symbol)} ${escapeHtml(price)}`).join('<br>')}</small>`
    : '数据不足';
  const windowCell = value => value.status === '已锁定'
    ? `<b>${escapeHtml(value.interpretation)}</b><br>标的 ${signed(value.return_pct)} · 基准 ${signed(value.benchmark_return_pct)}
       <br>超额 ${signed(value.excess_pp, ' 个百分点')}
       <br><small>最大涨幅 ${escapeHtml(value.max_gain_pct)}% · 最大回撤 ${escapeHtml(value.max_drawdown_pct)}% · ${escapeHtml(value.date)}</small>`
    : escapeHtml(value.status);
  const industry = item => item.industry.map(check => `<div><b>${escapeHtml(check.status)}</b>
    <small>${escapeHtml(check.period || check.expected_period || check.observed_period || '')}</small>
    ${check.metric ? `<br>${escapeHtml(check.metric)}` : ''}
    ${(check.evidence || []).map(fact => `<br><small>「${escapeHtml(fact.quote)}」
      ${fact.source?.url ? `<a href="${escapeHtml(fact.source.url)}" target="_blank" rel="noopener">来源</a>` : ''}</small>`).join('')}
    </div>`).join('') || '—';
  const table = rows => `<div class="table-wrap"><table>
    <thead><tr><th>环节</th><th>产业核验</th><th>生成时可见价</th><th>发布后起点</th>${[5, 20, 60].map(period => `<th>T+${period}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(item => `<tr><td><b>${escapeHtml(item.name)}</b><br><small>${escapeHtml(item.decision?.stance || item.coverage)}</small>
      ${item.published_at ? `<br><small>${date(item.published_at)} · ${escapeHtml(item.benchmark.name)}</small>` : ''}</td>
      <td>${industry(item)}</td><td>${closing(item.visible_at_scan)}</td><td>${closing(item.market.start)}</td>
      ${[5, 20, 60].map(period => `<td>${windowCell(item.market.windows[period])}</td>`).join('')}</tr>`).join('')}</tbody>
    </table></div>`;
  const calls = day.calls || [];
  const calledNodes = new Set(calls.map(item => item.node));
  const focus = [...calls, ...day.items.filter(item => item.industry.length && !calledNodes.has(item.node))];
  const misses = day.omissions || [];
  const omitted = misses.length ? `<div class="table-wrap"><table>
    <thead><tr><th>环节</th><th>分类</th><th>T+20 超额</th><th>复核依据</th></tr></thead>
    <tbody>${misses.map(item => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.classification)}</td>
      <td>${signed(item.excess_pp, ' 个百分点')}</td><td>${item.reasons.map(escapeHtml).join(' · ')}
      ${item.announcements.map(ann => `<br><small>${escapeHtml(ann.date)}
        ${ann.url ? `<a href="${escapeHtml(ann.url)}" target="_blank" rel="noopener">${escapeHtml(ann.title)}</a>` : escapeHtml(ann.title)}</small>`).join('')}</td></tr>`).join('')}</tbody>
    </table></div>` : '<p class="muted">这一天暂无到期漏报复核项。</p>';
  return `<div class="panel-body stack-sm"><div class="toolbar">
    <label>参评日 <select id="review-day">${review.days.map(value => `<option value="${escapeHtml(value)}" ${day.day === value ? 'selected' : ''}>${escapeHtml(value)}</option>`).join('')}</select></label>
    <span class="muted">基准 ${escapeHtml(day.benchmark.name)} · 发布 ${date(day.published_at)} · 新版判断样本 ${review.summary.samples}</span>
    </div>
    ${focus.length ? table(focus) : '<p class="muted">这一天没有正式判断或带核验条件的观察。</p>'}
    <details><summary>全量参评环节 (${day.items.length})</summary>${table(day.items)}</details>
    <h3>到期漏报复核 (${misses.length})</h3>${omitted}</div>`;
}

function renderVerify() {
  const sectors = state.feed?.sectors || [];
  const vr = state.verifyResult;
  const history = state.history?.items || [];

  const verifyForm = `<form id="verify-form" class="panel-body stack-sm">
    <label class="wb-field">对应行业（不选则自动识别）
      <select name="sector">
        <option value="">自动识别行业</option>
        ${sectors.map(s => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)}</option>`).join('')}
      </select>
    </label>
    <label class="wb-field">粘贴研报摘录 / 会议纪要 / 新闻原文
      <textarea name="text" rows="5" placeholder="把你在外部看到的线索贴到这里，AI 会立刻对照本地近 30 天新闻、量化位置分位和机构风向做交叉核验：到底是早期认知差，还是已充分定价的冷饭？" required></textarea>
    </label>
    <div><button type="submit" class="control primary" ${state.verifyBusy ? 'disabled' : ''}>${state.verifyBusy ? '交叉核验中…' : '⌕ 交叉核验这条线索'}</button></div>
  </form>`;

  const verifyResultBox = vr
    ? `<div class="panel-body">
        <div class="signal-top">
          <h3>${escapeHtml(vr.sector_name)} · ${escapeHtml(vr.verdict_label)}</h3>
          <span class="pill ${vr.verdict === 'EARLY_ALPHA' ? '' : 'demo'}">${escapeHtml(vr.verdict)}</span>
        </div>
        <p style="margin:12px 0">${escapeHtml(vr.reasoning)}</p>
        <p class="disproof"><b>支持点：</b>${(vr.supporting_signals || []).map(escapeHtml).join('；') || '—'}</p>
        <p class="disproof"><b>反证/风险：</b>${(vr.counter_evidence || []).map(escapeHtml).join('；') || '—'}</p>
        <p class="muted" style="font-size:12px;margin-top:8px"><b>实操建议：</b>${escapeHtml(vr.action_hint || '')}</p>
      </div>`
    : '<div class="empty">提交一条线索后，这里会显示交叉核验报告。</div>';

  const historyRows = history.length
    ? `<div class="panel-body table-wrap"><table class="radar-table">
        <thead><tr><th>日期</th><th>行业</th><th>立场</th><th>核心逻辑</th><th>当时位置分</th><th>发出以来涨跌</th><th>结果对照</th></tr></thead>
        <tbody>${history.map(h => `<tr>
          <td>${date(h.created_at)}</td>
          <td><b>${escapeHtml(h.sector_name)}</b></td>
          <td>${zoneBadge(h.stance === 'LURKING' ? 'LURKING' : 'HIGH_RISK', h.stance === 'LURKING' ? '潜伏关注' : '高危警示')}</td>
          <td style="max-width:340px">${escapeHtml(h.thesis)}</td>
          <td>${h.position_score_at_call ?? '—'}</td>
          <td class="${(h.actual_return_pct ?? 0) >= 0 ? 'positive' : 'negative'}">${signed(h.actual_return_pct)}</td>
          <td>${escapeHtml(h.outcome_label)}</td>
        </tr>`).join('')}</tbody>
      </table></div>`
    : '<div class="empty">正式运行 AI 雷达扫描后，会自动在此记录每次核心判断及后续真实涨跌对照。</div>';

  return `
    <div class="page-heading">
      <div><p class="eyebrow">CROSS VERIFY &amp; REVIEW</p><h1>随手投喂核验 &amp; 判断回看</h1></div>
    </div>
    <div class="two-col">
      ${panel('投喂一条外部线索', verifyForm, '', 'FEED A LEAD')}
      ${panel('交叉核验结论', verifyResultBox, '', 'VERIFICATION RESULT')}
    </div>
    ${panel(`跨天线索账本 (${state.leads?.count || 0})`, renderLeadLedger(state.leads))}
    ${panel('双轨复盘与漏报评估', renderDualReview(state.review))}
    ${panel(`AI 历史判断回看 (${history.length})`, historyRows, '', 'OUTCOME REVIEW')}
  `;
}

// ------------------------------------------------------------------ Page 5: 设置
function renderSettings() {
  return `
    <div class="page-heading">
      <div><p class="eyebrow">SETTINGS / 模型与数据源</p><h1>模型与数据源设置</h1></div>
    </div>
    <div class="stack">
      ${typeof modelPanel === 'function' ? modelPanel() : ''}
      ${typeof sourceSettingsPanel === 'function' ? sourceSettingsPanel() : ''}
    </div>
  `;
}

// ------------------------------------------------------------------ 主渲染与数据加载
function render() {
  $$('#nav button, .settings-button').forEach(b => b.classList.toggle('active', b.dataset.page === state.page));
  $('#breadcrumb').textContent = pageNames[state.page] || state.page;
  const main = $('#main');
  if (!state.feed && state.error) {
    main.innerHTML = `<div class="panel empty"><p class="negative">${escapeHtml(state.error)}</p><button class="control" onclick="load()">重试</button></div>`;
    return;
  }
  if (!state.feed) {
    main.innerHTML = '<div class="loading">正在载入工作台数据…</div>';
    return;
  }
  const views = {
    radar: renderRadar,
    sector: renderSector,
    smart: renderSmartMoney,
    verify: renderVerify,
    settings: renderSettings
  };
  main.innerHTML = (views[state.page] || renderRadar)();
  if (state.page === 'settings') {
    if (typeof loadModelSettings === 'function' && !modelSettings.loaded && !modelSettings.loading) {
      loadModelSettings();
    }
    if (typeof loadNewsCatalog === 'function' && !newsLibrary.catalog && !newsLibrary.loading) {
      loadNewsCatalog();
    }
  }
}

async function load() {
  try {
    const [feed, radar, macro, smartMoney, history, leads, review] = await Promise.all([
      request('/api/dashboard/feed'),
      request('/api/radar/overview'),
      request('/api/dashboard/macro').catch(() => ({ indicators: [], unavailable: [] })),
      request('/api/smart-money').catch(() => ({ institutions: [], entries: [] })),
      request('/api/ai/history').catch(() => ({ items: [] })),
      request('/api/ai/leads').catch(() => ({ error: '线索账本读取失败' })),
      request('/api/ai/review').catch(() => ({ error: '新版复盘读取失败' }))
    ]);
    state.feed = feed;
    state.radar = radar;
    state.macro = macro;
    state.smartMoney = smartMoney;
    state.history = history;
    state.leads = leads;
    state.review = review;
    state.error = '';
    if (!state.sector && feed.sectors?.length) state.sector = feed.sectors[0].id;
    $('#connection').textContent = '本地服务已连接';
    $('#footer-status').textContent = `最近同步 ${date(feed.updated_at)}`;
  } catch (err) {
    state.error = err.message;
    $('#connection').textContent = '连接异常';
  }
  render();
}

document.addEventListener('click', async event => {
  const navBtn = event.target.closest('[data-page]');
  if (navBtn) {
    state.page = navBtn.dataset.page;
    render();
    return;
  }
  const jump = event.target.closest('[data-jump-sector]');
  if (jump) {
    state.sector = jump.dataset.jumpSector;
    state.page = 'sector';
    render();
    return;
  }
  const sectorBtn = event.target.closest('[data-sector]');
  if (sectorBtn) {
    state.sector = sectorBtn.dataset.sector;
    render();
    return;
  }
  const rangeBtn = event.target.closest('[data-range]');
  if (rangeBtn) {
    state.range = Number(rangeBtn.dataset.range);
    render();
    return;
  }
  const storyBtn = event.target.closest('[data-story-id]');
  if (storyBtn) {
    const story = (state.feed?.stories || []).find(s => String(s.id) === storyBtn.dataset.storyId);
    if (story) {
      $('#detail-body').innerHTML = `
        <p class="eyebrow">${escapeHtml(story.source || '公开新闻')}</p>
        <h2>${escapeHtml(story.title)}</h2>
        <p class="muted">${date(story.published_at)}</p>
        <p>${escapeHtml(story.summary || '暂无摘要，请打开原文查看全文。')}</p>
        <div class="actions">
          ${story.url ? `<a class="control primary" href="${escapeHtml(story.url)}" target="_blank" rel="noopener">打开原始网页 ↗</a>` : ''}
          <button type="button" class="control" id="story-ai-analyze">✦ 使用默认模型解读</button>
        </div>
        <div id="story-ai-result" class="help" style="margin-top:14px;white-space:pre-wrap"></div>
      `;
      $('#story-ai-analyze').onclick = async e => {
        const btn = e.currentTarget;
        const out = $('#story-ai-result');
        btn.disabled = true;
        out.textContent = '正在用当前默认模型解读…';
        try {
          const res = await modelRequest('/analyze', {
            text: `请分析这条新闻是否存在潜在产业传导（蝴蝶效应）、核心受益或受损环节及后续验证信号。\n标题：${story.title}\n来源：${story.source}\n时间：${story.published_at}\n内容：${story.summary || ''}`
          });
          if (out.isConnected) out.textContent = `${res.model}\n\n${res.text}`;
        } catch (err) {
          if (out.isConnected) out.textContent = err.message;
        } finally {
          if (btn.isConnected) btn.disabled = false;
        }
      };
      $('#detail').showModal();
    }
    return;
  }
  const actionBtn = event.target.closest('[data-action]');
  if (actionBtn) {
    const act = actionBtn.dataset.action;
    if (act === 'run-butterfly') {
      state.aiBusy = true; render();
      try {
        await request('/api/ai/butterfly/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ use_ai: true, refresh_news: false, refresh_signals: true })
        });
        toast('硬异动与 AI 蝴蝶效应严审完成');
        await load();
      } catch (e) { toast(e.message); }
      finally { state.aiBusy = false; render(); }
    } else if (act === 'refresh-signals') {
      state.sigBusy = true; render();
      try {
        await request('/api/radar/signals/refresh', { method: 'POST' });
        toast('全网硬异动数据源已更新');
        await load();
      } catch (e) { toast(e.message); }
      finally { state.sigBusy = false; render(); }
    } else if (act === 'sync-sec') {
      state.smSyncBusy = true; render();
      try {
        const result = await request('/api/smart-money/sync', { method: 'POST' });
        const errors = Object.keys(result.errors || {}).length;
        toast(errors ? `有 ${errors} 家未完成持仓对比，详情见机构名录` : '已同步 SEC 13F 持仓');
        await load();
        if (state.smHoldingSector) await loadHoldingSector(state.smHoldingSector);
      } catch (e) { toast(e.message); }
      finally { state.smSyncBusy = false; render(); }
    }
    return;
  }
  const holdingBtn = event.target.closest('[data-sm-sector]');
  if (holdingBtn) {
    await loadHoldingSector(holdingBtn.dataset.smSector);
    return;
  }
  const delBtn = event.target.closest('[data-sm-delete]');
  if (delBtn) {
    try {
      await request(`/api/smart-money/entries/${encodeURIComponent(delBtn.dataset.smDelete)}`, { method: 'DELETE' });
      toast('已删除机构记录');
      await load();
    } catch (e) { toast(e.message); }
  }
});

document.addEventListener('change', async event => {
  if (event.target.id !== 'review-day') return;
  try {
    state.review = await request(`/api/ai/review?day=${encodeURIComponent(event.target.value)}`);
    render();
  } catch (error) { toast(error.message); }
});

document.addEventListener('submit', async event => {
  if (event.target.id === 'sm-parse-form') {
    event.preventDefault();
    const fd = new FormData(event.target);
    state.smParseBusy = true; render();
    try {
      await request('/api/smart-money/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: fd.get('text') })
      });
      toast('已自动拆解并入库');
      event.target.reset();
      await load();
    } catch (e) { toast(e.message); }
    finally { state.smParseBusy = false; render(); }
  } else if (event.target.id === 'sm-form') {
    event.preventDefault();
    const fd = new FormData(event.target);
    const body = Object.fromEntries(fd.entries());
    try {
      await request('/api/smart-money/entries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      toast('已保存机构动态');
      event.target.reset();
      await load();
    } catch (e) { toast(e.message); }
  } else if (event.target.id === 'verify-form') {
    event.preventDefault();
    const fd = new FormData(event.target);
    state.verifyBusy = true; render();
    try {
      state.verifyResult = await request('/api/ai/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: fd.get('text'), sector: fd.get('sector') || null })
      });
      toast('交叉核验完成');
    } catch (e) { toast(e.message); }
    finally { state.verifyBusy = false; render(); }
  }
});

$('#search')?.addEventListener('input', e => {
  state.search = e.target.value;
  if (state.page === 'sector') render();
});
$('#close-detail')?.addEventListener('click', () => $('#detail').close());
$('#refresh').onclick = () => load();
window.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
    e.preventDefault(); $('#search')?.focus();
  }
});

load();
