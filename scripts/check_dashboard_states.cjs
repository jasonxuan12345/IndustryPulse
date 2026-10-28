'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('apps/dashboard/web/dashboard-states.js','utf8');
const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function fixture(){
  const item={id:'coal',name:'Coal',net_flow:null,change:null,heat:null,trend:[]};
  const state={page:'qa',filter:'all',range:30,sector:'coal',macro:null,feed:{sectors:[item],market:{},updated_at:'2026-10-28T19:00:00+08:00',flow_unit:'CNY'}};
  const stub=()=>'';
  const box={state,item,escapeHtml,sector:()=>item,stories:()=>[],companies:()=>[],
    number:v=>v==null?'MISSING':String(v),signed:v=>v==null?'MISSING':String(v),tone:()=>'',date:String,
    panel:(title,content)=>`<section>${title}${content}</section>`,categories:{},
    macroPanel:()=>'<macro/>',commoditiesPanel:stub,benchmarksPanel:stub,trendPanel:()=>'<trend/>',
    companyPanel:stub,hotspots:stub,metrics:stub,flowPanel:stub,newsRows:()=>'<news/>',
    trendPoints:()=>item.trend,render:stub,
    document:{querySelectorAll:()=>[],querySelector:()=>null}};
  vm.createContext(box);vm.runInContext(source,box);return box;
}
const b=fixture();
assert.equal(b.flowPanel(),'');
assert.equal(b.trendPanel(),'');
assert.equal(b.macroPanel(),'');
assert.equal(b.commoditiesPanel(),'');
assert.equal(b.benchmarksPanel(),'');
assert.ok(!b.metrics().includes('MISSING'));
b.item.net_flow=0;b.item.change=0;b.item.heat=0;
assert.ok(b.flowPanel().includes('0'));
assert.ok(!b.flowPanel().includes('MISSING'));
assert.ok(b.metrics().includes('0'));
b.state.feed.sectors.push({id:'unknown',name:'Unreported sector',net_flow:null});
assert.ok(!b.flowPanel().includes('Unreported sector'));
b.item.trend=[{date:'2026-10-28',value:100}];
assert.equal(b.trendPanel(),'<trend/>');
b.state.macro={indicators:[{value:0}]};
assert.equal(b.macroPanel(),'<macro/>');
b.state.feed.market={benchmarks:[{name:'valid',price:10},{name:'missing',price:null}],commodities:[{name:'valid',price:10},{name:'missing',price:null}]};
assert.ok(!b.benchmarksPanel().includes('missing'));
assert.ok(!b.commoditiesPanel().includes('missing'));
assert.ok(b.newsRows([]).includes('data-filter'));
assert.ok(b.newsRows([]).includes('/?page=settings'));
console.log('PASS: empty panels omitted; zero values preserved; partial sources filtered; recovery actions present.');
