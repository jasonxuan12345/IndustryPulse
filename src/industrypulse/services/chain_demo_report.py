"""无需外部依赖的示范验收页；所有图表、证据和筛选理由来自同一个报告。"""

# ruff: noqa: E501 -- 内嵌 HTML/CSS/JS 模板保留浏览器可读的完整语句。

from __future__ import annotations

import json
from typing import Any


def render_html(report: dict[str, Any]) -> str:
    data = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")
    return _TEMPLATE.replace("__REPORT_DATA__", data)


_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>电力及电气设备 · 产业链示范验收</title><link rel="icon" href="data:,">
<style>
:root{color-scheme:dark;font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:#10151d;color:#e8edf4}
*{box-sizing:border-box}body{margin:0}main{max-width:1240px;margin:auto;padding:36px 28px 70px}h1{font-size:28px;margin:8px 0 16px}h2{font-size:20px;margin:0 0 16px}h3{font-size:16px}p{line-height:1.7}a{color:#83b8fa}small,.muted{color:#a9b7c8}.kicker{letter-spacing:2px;font-size:12px;color:#83b8fa}
.banner{padding:16px 20px;background:#202a38;border-left:3px solid #83b8fa;margin:24px 0}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;margin:28px 0}.metric{border-top:1px solid #344154;padding:14px 0}.value{font-size:25px;margin:6px 0}.caption{font-size:12px;color:#a9b7c8;line-height:1.7}
section{margin-top:36px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:11px 9px;border-bottom:1px solid #2c3747;vertical-align:top}th{color:#a9b7c8;font-weight:500}.scroll{overflow:auto}.split{display:grid;grid-template-columns:1fr 1fr;gap:28px}.box{padding:20px;background:#18212d}.flow{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.flow span{padding:10px 15px;border:1px solid #344154}.facts{margin-top:12px;font-size:12px}.facts summary{cursor:pointer;color:#83b8fa}li{line-height:1.7;margin:6px 0}select{background:#202a38;color:#e8edf4;padding:9px;border:1px solid #465773;max-width:100%}.legend{display:flex;gap:24px;flex-wrap:wrap;font-size:12px}.legend i{display:inline-block;width:18px;height:3px;margin:0 7px 3px 0}svg{width:100%;height:auto;display:block}.hash{font-family:monospace;overflow-wrap:anywhere}.status{font-size:13px;color:#e9c58a}.trace{margin-top:18px}footer{margin-top:36px;border-top:1px solid #344154;padding-top:16px}@media(max-width:760px){main{padding:24px 16px}.metrics{grid-template-columns:1fr 1fr}.split{grid-template-columns:1fr}h1{font-size:24px}}
</style>
<main>
<div class="kicker">INDUSTRYPULSE / 完整产业链示范</div>
<h1>从生产变化到可交易资产</h1>
<p id="range" class="muted"></p>
<div class="banner"><b>工程链路完成 · 研究有效性尚未验证</b><br><small>这是固定规则的回顾性示范。今天生成的 AI 假设只作现实研究证据，不进入历史交易。无历史事前注册、无独立留出验收。</small></div>
<div class="flow"><span>PMI 生产变化</span><span>下一次行业利润验证</span><span>对口 ETF 筛选</span><span>次日成交与费后回测</span></div>
<div id="metrics" class="metrics"></div>
<section><h2>同起点、同成本的组合表现</h2><div id="legend" class="legend"></div><div id="chart"></div><p class="caption">横轴：交易日期；纵轴：资产净值（初始资金=100）。策略、沪深300ETF和行业买入持有均为50%目标仓位，其余现金，末日开盘退出。佣金3bps、滑点5bps，均为单边；现金分红入账，现金利率0。来源：冻结输入中的新浪不复权日线及本地分红记录。曲线每五个交易日抽样显示，指标使用完整日序列。</p><div id="backtests" class="scroll"></div></section>
<section class="split"><div><h2>规则和资产边界</h2><ul><li>PMI生产≥50，较相邻月上升≥0.5点。</li><li>行业利润累计同比较相邻公布期改善，同期营收同比&gt;0。</li><li>领先指标所属期不超过75天；财务所属期不超过120天。</li><li>只纳入对口ETF，最近20个交易日有成交；总权重50%。</li><li>条件消失即发退出目标；次交易日开盘成交。</li></ul></div><div class="box"><h2>可逐日核对的筛选</h2><label for="decision">选择历史决策日</label><br><br><select id="decision"></select><div id="trace" class="trace"></div></div></section>
<section><h2>领先事件的后续经济验证</h2><p class="caption">宏观代理不等于行业因果。下面只核对领先事件之后首次新所属期的行业利润公布，提前量按实际可见时点计算；历史关联与真实系统发现时间分开记录。</p><div id="economic" class="scroll"></div></section>
<section><h2>事件窗研究：21 / 63 / 126个交易日</h2><p class="caption">次交易日开盘进入，窗口末日收盘退出，双边扣费并加回持有期间分红。事件组为领先阈值通过，其他发布为对照。窗口可以重叠，不等同组合收益；q为本示范全部行业与周期的BH校正值。跨未核实拆并日的窗口剔除。</p><div id="studies" class="scroll"></div></section>
<section><h2>现实 AI 研究与独立反证</h2><div id="ai"></div></section>
<section><h2>验收范围与复现</h2><ul id="limits"></ul><p class="caption">冻结输入与策略、全部逐日证据、目标权重、逐笔成交、完整曲线保存在相邻的 inputs.json 与 report.json。使用 industrypulse chain-demo --inputs inputs.json --out 目标目录 可离线重放。</p><p id="hash" class="caption hash"></p><p><a href="inputs.json">冻结输入</a> · <a href="report.json">完整结构化报告</a> · <a href="report.md">文字验收报告</a></p></section>
<footer class="caption">工程验收不等于预测或投资有效性验收。研究状态始终为 NOT_VALIDATED。</footer>
</main>
<script>
const R=__REPORT_DATA__;
const C=R.config, W=(C.max_weight*100).toFixed(0)+'%', leadName=C.leading_name||C.leading_series;
document.title=R.chain+' · 产业链示范验收';
document.querySelector('.flow span').textContent=leadName+'变化';
const ruleList=document.querySelector('.split ul');
ruleList.children[0].textContent=`${leadName}≥${C.leading_floor}，较相邻月上升≥${C.leading_delta}点。`;
ruleList.children[2].textContent=`领先指标所属期不超过${C.leading_max_age_days}天；财务所属期不超过${C.fundamental_max_age_days}天。`;
ruleList.children[3].textContent=`只纳入对口ETF，最近${C.min_history_days}个交易日有成交；总权重${W}。`;
const E=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=x=>x==null?'—':(x*100).toFixed(2)+'%';
const table=(headers,rows)=>'<table><thead><tr>'+headers.map(x=>'<th>'+E(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
document.querySelector('#range').textContent=`${R.chain} · ${R.start} — ${R.end} · ${R.snapshot_id}`;
const S=R.selection_summary;
document.querySelector('#metrics').innerHTML=[['领先发布事件',S.event_count],['通过领先阈值',S.leading_on_events],['有候选的决策日',S.days_with_candidate],['策略费后收益',pct(R.backtest.metrics.total_return)]].map(([k,v])=>`<div class="metric"><small>${E(k)}</small><div class="value">${E(v)}</div></div>`).join('');
const runs=[['策略',R.backtest,'#83b8fa'],['沪深300ETF · '+W,R.baselines.benchmark_matched_weight,'#d2af76'],['行业买入持有 · '+W,R.baselines.industry_buy_hold_matched_weight,'#9eabbc']];
document.querySelector('#legend').innerHTML=runs.map(([name,,color])=>`<span><i style="background:${color}"></i>${E(name)}</span>`).join('');
const width=1080,height=300,pad=56;
const all=runs.flatMap(([,r])=>r.equity_curve.map(([,v])=>Number(v)/Number(r.initial_cash)*100));
const lo=Math.floor(Math.min(...all)/5)*5,hi=Math.ceil(Math.max(...all)/5)*5+5;
const y=v=>height-pad-(v-lo)/(hi-lo)*(height-2*pad);
let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="策略和同权重基准净值，初始资金100">`;
for(let i=0;i<5;i++){const v=lo+(hi-lo)*i/4;svg+=`<line x1="${pad}" y1="${y(v)}" x2="${width-pad}" y2="${y(v)}" stroke="#2c3747"/><text x="8" y="${y(v)+4}" fill="#a9b7c8" font-size="11">${v.toFixed(0)}</text>`;}
for(const [,r,color] of runs){const curve=r.equity_curve;const points=curve.filter((_,i)=>i%5===0||i===curve.length-1).map(([d,v])=>{const i=curve.findIndex(p=>p[0]===d);return`${pad+i/(curve.length-1)*(width-2*pad)},${y(Number(v)/Number(r.initial_cash)*100)}`;}).join(' ');svg+=`<polyline points="${points}" fill="none" stroke="${color}" stroke-width="2"/>`;}
svg+=`<text x="${pad}" y="${height-22}" fill="#a9b7c8" font-size="11">${E(R.start)}</text><text x="${width-pad}" y="${height-22}" text-anchor="end" fill="#a9b7c8" font-size="11">${E(R.end)}</text><text x="${width/2}" y="${height-4}" fill="#a9b7c8" font-size="11">交易日期</text><text x="8" y="18" fill="#a9b7c8" font-size="11">净值（初始=100）</text></svg>`;
document.querySelector('#chart').innerHTML=svg;
document.querySelector('#chart').nextElementSibling.textContent=`横轴：交易日期；纵轴：资产净值（初始资金=100）。各组合为${W}目标仓位，其余现金，末日开盘退出。佣金${C.commission_bps}bps、滑点${C.slippage_bps}bps，均为单边；现金分红入账，现金年利率${pct(C.cash_rate_annual)}。来源：冻结输入中的新浪不复权日线及本地分红记录。曲线每五个交易日抽样显示，指标使用完整日序列。`;
document.querySelector('#backtests').innerHTML=table(['组合','费后收益','最大回撤','成交笔数'],[...runs.map(([name,r])=>[E(name),pct(r.metrics.total_return),pct(r.metrics.max_drawdown),r.trades.length]),['策略 · 单边佣金/滑点各10bps',pct(R.stress_10bps_each.metrics.total_return),pct(R.stress_10bps_each.metrics.max_drawdown),R.stress_10bps_each.trades.length]]);
const sel=document.querySelector('#decision');sel.innerHTML=R.decisions.map((d,i)=>`<option value="${i}">${E(d.day)} · ${Object.keys(d.weights).length?'有候选':'无候选'}</option>`).join('');sel.value=String(R.decisions.length-1);
function show(){const d=R.decisions[Number(sel.value)];document.querySelector('#trace').innerHTML=d.candidates.map(c=>`<h3>${E(c.asset_name)}</h3><p class="status">${c.selected?'研究候选':'未入选'} · ${E(c.fit)}</p><p>${E(c.reasons.join('；')||'全部固定条件通过')}</p>${c.counter_evidence.length?`<p class="muted">反证：${E(c.counter_evidence.join('；'))}</p>`:''}<details class="facts"><summary>原件、所属期与可见时点</summary><ul>${c.facts.map(f=>`<li>${E(f.series_id)} / ${E(f.period)} / v${f.vintage_id} / ${E(f.value)}<br>可见：${E(f.available_at)}<br>${f.source_url&&/^https?:/.test(f.source_url)?`<a href="${E(f.source_url)}" target="_blank" rel="noreferrer">官方原件</a>`:''}</li>`).join('')}</ul></details>`).join('');}sel.onchange=show;show();
document.querySelector('#economic').innerHTML=table(['领先期','事件组','行业','下一利润公布','利润同比变化（百分点）','提前天数'],R.events.slice(-8).flatMap(e=>e.economic_verifications.map(v=>[E(e.period),e.on?'通过':'未通过',E(R.config.industries[v.industry_id].name),E(v.verification_day||'待公布'),v.profit_delta_pp==null?'—':v.profit_delta_pp.toFixed(2),v.lead_calendar_days==null?'—':v.lead_calendar_days.toFixed(1)])));
document.querySelector('#studies').innerHTML=table(['ETF / 匹配','交易日','事件/对照数','事件组费后超额均值','q','未成熟 / 异常剔除'],R.event_study.map(o=>[E(o.asset_id+' / '+o.fit),o.trading_days,`${o.stats.n_on} / ${o.stats.n_off}`,pct(o.stats.mean_on),o.stats.q_value==null?'样本不足':o.stats.q_value.toFixed(3),`${o.pending} / ${o.blocked}`]));
document.querySelector('#ai').innerHTML=R.hypotheses.length?R.hypotheses.map(h=>`<div class="box"><small>${E(h.created_at)} · ${E(h.hypothesis_id)}</small><p>${E(h.statement)}</p><p class="status">独立反证：${E(h.counter_verdict)} · ${E(h.status)}</p><details><summary>反证及失效条件</summary><ul>${(h.counter_evidence||[]).map(c=>`<li>${E(c.observation||c.note||c)}</li>`).join('')}${(h.invalidation_conditions||[]).map(c=>`<li>${E(c)}</li>`).join('')}</ul></details></div>`).join(''):'<p>该截止时点之前没有本地AI假设；研究和历史交易回放不依赖AI。</p>';
document.querySelector('#limits').innerHTML=R.limitations.map(x=>`<li>${E(x)}</li>`).join('');
document.querySelector('#hash').textContent='输入 SHA256 · '+R.input_sha256;
</script></html>"""
