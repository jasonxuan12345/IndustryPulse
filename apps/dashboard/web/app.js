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
  health: null,
  verifyResult: null,
  aiBusy: false,
  verifyBusy: false,
  smSyncBusy: false,
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
function renderRadar() {
  const radarData = state.radar || {};
  const sectors = radarData.sectors || [];
  const report = radarData.latest_ai_report || {};
  const hasReport = Boolean(report && report.run_id);
  const macro = report.macro_regime || {};
  const early = report.early_opportunities || [];
  const highRisk = report.high_risk_warnings || [];
  const storiesById = new Map((state.feed?.stories || []).map(s => [String(s.id), s]));

  const aiHeaderRight = `
    <div class="toolbar">
      ${hasReport ? `<span class="pill ${report.mode === 'ai' ? '' : 'demo'}">${report.mode === 'ai' ? `AI · ${escapeHtml(report.model || '')}` : '规则基线'} · ${date(report.generated_at)}</span>` : ''}
      <button class="control primary" data-action="run-butterfly" ${state.aiBusy ? 'disabled' : ''}>${state.aiBusy ? '正在并行推演…' : '✧ 运行 AI 蝴蝶效应扫描'}</button>
    </div>`;

  const macroSummary = hasReport
    ? `<div class="panel-body">
        <p class="macro-lead">${escapeHtml(macro.summary || '暂无宏观研判')}</p>
        <div class="tag-row">
          ${(macro.favored_styles || []).map(t => `<span class="pill">有利方向：${escapeHtml(t)}</span>`).join('')}
          ${(macro.key_risks || []).map(t => `<span class="pill warn-pill">关注风险：${escapeHtml(t)}</span>`).join('')}
        </div>
      </div>`
    : `<div class="panel-body"><p class="muted">尚未运行过扫描。点击右上角「运行 AI 蝴蝶效应扫描」，系统会并行读取 12 行业最新新闻，结合量化位置与机构共识找出早期潜伏线索与高危离场信号。</p></div>`;

  const earlyCards = early.length
    ? early.map(item => {
        const evs = (item.evidence_stories || []).length
          ? item.evidence_stories
          : (item.evidence_story_ids || []).map(id => storiesById.get(String(id))).filter(Boolean);
        return `<article class="signal-card early-card">
          <div class="signal-top">
            <div>
              <strong>${escapeHtml(item.sector_name || item.sector)}</strong>
              ${zoneBadge(item.zone, item.zone_label)}
            </div>
            <button class="control" data-jump-sector="${escapeHtml(item.sector)}">进行业透视 ↗</button>
          </div>
          <h3>${escapeHtml(item.title)}</h3>
          <p class="signal-body">${escapeHtml(item.butterfly_chain)}</p>
          <div class="signal-meta">
            ${item.quant_alignment ? `<span>📊 ${escapeHtml(item.quant_alignment)}</span>` : ''}
            ${item.smart_money_note ? `<span>⚖ ${escapeHtml(item.smart_money_note)}</span>` : ''}
          </div>
          ${item.disproof_signal ? `<p class="disproof"><b>证伪红线：</b>${escapeHtml(item.disproof_signal)}</p>` : ''}
          ${evs.length ? `<div class="evidence-links"><b>依据原文：</b>${evs.map(s => `<a href="${escapeHtml(s.url || '#')}" target="_blank" rel="noopener">${escapeHtml(s.title)}</a>`).join(' · ')}</div>` : ''}
        </article>`;
      }).join('')
    : '<div class="empty">当前暂无高催化且处于低位的潜伏线索（或尚未运行扫描）。</div>';

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
        <p class="signal-body">${escapeHtml(item.reason)}</p>
        ${item.disproof_signal ? `<p class="disproof"><b>解除条件：</b>${escapeHtml(item.disproof_signal)}</p>` : ''}
      </article>`).join('')
    : '<div class="empty">当前没有触发高危离场警示的板块。</div>';

  const quantTable = `<div class="panel-body">
    <div class="table-wrap"><table class="radar-table">
      <thead><tr><th>行业</th><th>状态</th><th>位置分</th><th>催化分</th><th>20日涨跌</th><th>相对沪深300</th><th>60日价格分位</th><th>成交拥挤度</th></tr></thead>
      <tbody>${sectors.map(s => `<tr data-jump-sector="${escapeHtml(s.id)}" class="clickable-row">
        <td><b>${escapeHtml(s.name)}</b></td>
        <td>${zoneBadge(s.zone, s.zone_label)}</td>
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
            <div class="muted" style="font-size:11.5px;margin-top:4px">${escapeHtml(sig.chain)}</div>
          </div>`).join('')}
        </div>
      </article>`).join('')}</div>`
    : '';

  return `
    <div class="page-heading">
      <div><p class="eyebrow">EARLY RADAR / AI 蝴蝶效应 + 量化位置</p><h1>早期雷达<span> · 潜伏与离场</span></h1></div>
      ${aiHeaderRight}
    </div>
    ${panel('宏观经济风向与主要矛盾', macroSummary, '', 'MACRO REGIME')}
    <div class="two-col" style="margin-top:20px">
      ${panel('「信息催化 × 市场位置」四象限', `<div class="panel-body">${quadrantChart(sectors)}</div>`, '', 'QUANT POSITION MATRIX')}
      ${panel('高危离场警示区', `<div class="panel-body stack">${riskCards}</div>`, '', 'HIGH RISK WARNINGS')}
    </div>
    ${panel('早期潜伏重点板块（高催化 + 未拥挤）', `<div class="panel-body card-grid">${earlyCards}</div>`, '', 'EARLY OPPORTUNITIES')}
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
  const macroItems = (state.macro?.items || []).filter(m => m.value != null).slice(0, 8);

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

  const macroPanel = macroItems.length
    ? panel('核心宏观底座指标', `<div class="panel-body macro-grid">${macroItems.map(m => `<div class="macro-cell">
        <small>${escapeHtml(m.name)}</small>
        <b>${escapeHtml(m.value)}<i>${escapeHtml(m.unit || '')}</i></b>
        <em>${escapeHtml(m.period || '')}</em>
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
function renderSmartMoney() {
  const sm = state.smartMoney || { institutions: [], entries: [], sync_errors: {} };
  const sectors = state.feed?.sectors || [];
  const instCards = (sm.institutions || []).map(inst => {
    const filing = inst.latest_filing;
    return `<div class="sm-inst-card">
      <div class="signal-top">
        <b>${escapeHtml(inst.name)}</b>
        <span class="pill ${inst.region === 'US' ? 'demo' : ''}">${escapeHtml(inst.region)} · 常规滞后 ${inst.disclosure_lag_days}天</span>
      </div>
      <p class="muted" style="font-size:12px;margin:6px 0">${escapeHtml(inst.style)} — ${escapeHtml(inst.notes)}</p>
      <div style="font-size:11px;color:#9aa7b9">
        ${filing
          ? `最新 13F-HR：${escapeHtml(filing.filing_date)}（报告期 ${escapeHtml(filing.report_date)}） · <a href="${escapeHtml(filing.url)}" target="_blank" rel="noopener">EDGAR 原件 ↗</a>`
          : (inst.cik ? '尚未同步 SEC 13F 状态' : '国内/产业渠道（支持手动录入笔记）')}
      </div>
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
    <div><button type="submit" class="control primary">保存机构动向（将参与 AI 推演）</button></div>
  </form>`;

  const entryList = (sm.entries || []).length
    ? sm.entries.map(e => `<div class="news-row">
        <div class="news-content">
          <div class="signal-top">
            <div><b>${escapeHtml(e.institution_name)}</b> · <span class="tag">${escapeHtml(e.sector)}</span> · <span class="tag">${escapeHtml(e.direction)}</span></div>
            <button class="control" data-sm-delete="${escapeHtml(e.id)}">删除</button>
          </div>
          <p style="margin:6px 0 4px;font-weight:500">${escapeHtml(e.title)}</p>
          <p class="muted" style="font-size:12px">${escapeHtml(e.summary)}</p>
          <div class="news-meta">
            <span style="color:#e8c88d">${escapeHtml(e.lag_warning)}</span>
            ${e.source_url ? `<a href="${escapeHtml(e.source_url)}" target="_blank" rel="noopener">来源 ↗</a>` : ''}
          </div>
        </div>
      </div>`).join('')
    : '<div class="empty">暂无手动录入的机构持仓或观点笔记。</div>';

  return `
    <div class="page-heading">
      <div><p class="eyebrow">SMART MONEY / 滞后性提醒</p><h1>国内外知名机构动向<span> · 中长期风向与拥挤校验</span></h1></div>
      <div class="toolbar">
        <button class="control" data-action="sync-sec" ${state.smSyncBusy ? 'disabled' : ''}>${state.smSyncBusy ? '同步中…' : '↻ 同步海外 SEC 13F 状态'}</button>
      </div>
    </div>
    <div class="notice">提示：13F 与国内公募季报披露存在 15~45 天滞后，切勿直接作短线追高依据；重点看资本跨季度迁徙方向，以及机构极度一致扎堆时的高危反向信号。</div>
    <div class="two-col">
      ${panel('录入机构动态 / 大佬观点', entryForm, '', 'ADD OBSERVATION')}
      ${panel('已跟踪机构名录', `<div class="panel-body stack-sm">${instCards}</div>`, '', 'WATCHED INSTITUTIONS')}
    </div>
    ${panel(`机构动向时间线 (${(sm.entries || []).length})`, entryList, '', 'TIMELINE')}
  `;
}

// ------------------------------------------------------------------ Page 4: 投喂核验 & 回看
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
    const [feed, radar, macro, smartMoney, history] = await Promise.all([
      request('/api/dashboard/feed'),
      request('/api/radar/overview'),
      request('/api/dashboard/macro').catch(() => ({ items: [] })),
      request('/api/smart-money').catch(() => ({ institutions: [], entries: [] })),
      request('/api/ai/history').catch(() => ({ items: [] }))
    ]);
    state.feed = feed;
    state.radar = radar;
    state.macro = macro;
    state.smartMoney = smartMoney;
    state.history = history;
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
          body: JSON.stringify({ use_ai: true, refresh_news: false })
        });
        toast('蝴蝶效应扫描完成');
        await load();
      } catch (e) { toast(e.message); }
      finally { state.aiBusy = false; render(); }
    } else if (act === 'sync-sec') {
      state.smSyncBusy = true; render();
      try {
        await request('/api/smart-money/sync', { method: 'POST' });
        toast('已更新 SEC 13F 申报状态');
        await load();
      } catch (e) { toast(e.message); }
      finally { state.smSyncBusy = false; render(); }
    }
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

document.addEventListener('submit', async event => {
  if (event.target.id === 'sm-form') {
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
