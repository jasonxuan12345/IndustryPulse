const modelSettings = {
  loaded: false, loading: false, busy: false, models: [], filter: '',
  base_url: '', api_key: '', model: '', protocol: 'chat_completions',
  enabled: true, has_api_key: false, configured: false, message: '', error: false,
  saved: null, discovery: 0
};

function modelPayload() {
  return {base_url: modelSettings.base_url.trim(), api_key: modelSettings.api_key,
    model: modelSettings.model.trim(), protocol: modelSettings.protocol, enabled: modelSettings.enabled};
}

function modelOptions() {
  const choices = [...modelSettings.models];
  if (modelSettings.model && !choices.some(item => item.id === modelSettings.model)) {
    choices.unshift({id: modelSettings.model, owner: ''});
  }
  return '<option value="">请选择模型</option>' + choices
    .filter(item => item.id.toLowerCase().includes(modelSettings.filter.toLowerCase()) || item.id === modelSettings.model)
    .map(item => `<option value="${escapeHtml(item.id)}" ${item.id === modelSettings.model ? 'selected' : ''}>${escapeHtml(item.id)}${item.owner ? ' · '+escapeHtml(item.owner) : ''}</option>`).join('');
}

function modelPanel() {
  const saved = modelSettings.saved;
  const message = modelSettings.message || (modelSettings.loading ? '正在读取已保存配置…' : '填写地址和密钥后，离开输入框会自动获取模型列表，也可以点击下方按钮。');
  return panel('AI 模型与 API', `<div class="panel-body">
    <div class="model-connection-grid">
      <div>
        <label class="wb-field">API 地址（Base URL）<input id="model-base-url" type="url" placeholder="https://你的服务商/v1" autocomplete="off" value="${escapeHtml(modelSettings.base_url)}"></label>
        <label class="wb-field" style="margin-top:14px">API Key<input id="model-api-key" type="password" autocomplete="off" placeholder="${modelSettings.has_api_key ? '密钥已保存；留空沿用，填写则替换' : '填写 API Key；本地免密服务可留空'}" value="${escapeHtml(modelSettings.api_key)}"></label>
        <label class="wb-field" style="margin-top:14px">调用协议<select id="model-protocol"><option value="chat_completions" ${modelSettings.protocol === 'chat_completions' ? 'selected' : ''}>OpenAI 兼容 · Chat Completions</option><option value="responses" ${modelSettings.protocol === 'responses' ? 'selected' : ''}>OpenAI Responses</option></select></label>
        <div class="actions"><button class="control" data-model-action="discover" ${modelSettings.busy ? 'disabled' : ''}>${modelSettings.busy ? '正在连接…' : '↻ 获取可用模型'}</button><span class="muted" style="font-size:11px">${modelSettings.models.length ? `发现 ${modelSettings.models.length} 个模型` : '从当前接口读取模型列表'}</span></div>
      </div>
      <div>
        <label class="wb-field">搜索模型<input id="model-filter" placeholder="输入模型名称筛选…" value="${escapeHtml(modelSettings.filter)}"></label>
        <label class="wb-field" style="margin-top:14px">选择默认模型<select id="model-choice">${modelOptions()}</select></label>
        <details style="margin-top:12px"><summary class="muted" style="font-size:11px;cursor:pointer">服务商不提供列表？手动填写模型 ID</summary><label class="wb-field" style="margin-top:10px">模型 ID<input id="model-manual" placeholder="填写服务商提供的完整模型 ID" value="${escapeHtml(modelSettings.model)}"></label></details>
        <label style="display:flex;gap:8px;align-items:center;font-size:12px;margin-top:17px"><input id="model-enabled" type="checkbox" style="width:auto;accent-color:var(--green)" ${modelSettings.enabled ? 'checked' : ''}>启用此模型作为软件默认 AI</label>
        <div class="actions"><button class="control" data-model-action="test" ${modelSettings.busy ? 'disabled' : ''}>测试所选模型</button><button class="control primary" data-model-action="save" ${modelSettings.busy ? 'disabled' : ''}>保存并设为默认</button></div>
        <p class="help" style="margin-top:10px;font-size:11px">测试会发送一条简短请求。保存后立即用于 AI 解读与证据抽取，重启自动沿用。</p>
      </div>
    </div>
    <div id="model-message" class="${modelSettings.error ? 'wb-error' : 'notice'}" style="margin-top:18px;margin-bottom:0" role="status">${escapeHtml(message)}</div>
    <div class="setting-line"><span>当前已生效配置</span><b>${saved?.configured && saved.enabled ? escapeHtml(saved.model) : saved?.configured ? '已保存 · 暂未启用' : '尚未配置'}</b></div>
    ${saved?.configured ? `<div class="setting-line"><span>API 地址</span><span style="overflow-wrap:anywhere">${escapeHtml(saved.base_url)}</span></div>` : ''}
    <p class="help" style="margin-top:12px;margin-bottom:0;font-size:11px">模型列表包含接口声明的可见模型，实际文本调用权限以测试结果为准。本地统计预测模型独立运行；实时新闻和行情仍由资讯数据源提供。</p>
  </div>`, '<span class="pill">PERSISTENT / 持久保存</span>', 'MODEL CONNECTION / 模型连接');
}

const modelStyle = document.createElement('style');
modelStyle.textContent = '.model-connection-grid{display:grid;grid-template-columns:1fr 1fr;gap:28px}.model-settings-card{margin-bottom:20px}.model-connection-grid input,.model-connection-grid select{min-height:39px}.model-connection-grid button:disabled{opacity:.45;cursor:wait}@media(max-width:800px){.model-connection-grid{grid-template-columns:1fr;gap:22px}}';
document.head.append(modelStyle);
const dataSettings = settings;
settings = function() { return `<div class="model-settings-card">${modelPanel()}</div>`+dataSettings(); };

const openStoryDetails = detail;
detail = function(item) {
  openStoryDetails(item);
  $('#detail-body').insertAdjacentHTML('beforeend', `<div class="actions"><button class="control primary" id="story-ai-analyze">✦ 使用默认模型解读</button></div><div id="story-ai-result" class="help" style="margin-top:15px;white-space:pre-wrap"></div>`);
  $('#story-ai-analyze').onclick = async event => {
    const button = event.currentTarget;
    const output = $('#story-ai-result');
    button.disabled = true; output.textContent = '正在使用已保存的默认模型分析…';
    try {
      const result = await modelRequest('/analyze', {text: `请根据以下资料，简洁整理事件要点、产业链影响和后续关注点。区分原文事实与推测，不能补造最新信息。\n资料模式：${state.feed.mode === 'demo' ? '虚构演示，请按假设案例分析' : '用户接入资讯'}\n标题：${item.title}\n来源：${item.source}\n发布时间：${item.published_at}\n内容：${item.summary}`});
      if (output.isConnected) output.textContent = `${result.model}\n\n${result.text}`;
    } catch (error) {if(output.isConnected)output.textContent = error.message;}
    finally {if(button.isConnected)button.disabled = false;}
  };
};

async function modelRequest(path, payload) {
  const response = await fetch('/api/dashboard/model'+path, payload === undefined ? {} : {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查 API 配置和模型名称');
  return data;
}

async function loadModelSettings() {
  modelSettings.loading = true;
  try {
    const data = await modelRequest('/settings');
    Object.assign(modelSettings, data, {saved: data, loaded: true});
    if (!data.configured) modelSettings.enabled = true;
  } catch (error) {
    modelSettings.message = error.message; modelSettings.error = true;
  } finally {
    modelSettings.loading = false;
    modelSettings.loaded = true;
    if (state.page === 'settings') render();
  }
}

const renderWithResearch = render;
render = function() {
  renderWithResearch();
  if (state.page === 'settings' && !modelSettings.loaded && !modelSettings.loading) loadModelSettings();
};

async function discoverModels() {
  if (!modelSettings.base_url.trim()) return;
  const sequence = ++modelSettings.discovery;
  modelSettings.busy = true; modelSettings.error = false;
  modelSettings.message = '正在从 API 获取可用模型…';
  if (state.page === 'settings') render();
  try {
    const data = await modelRequest('/models', modelPayload());
    if (sequence !== modelSettings.discovery) return;
    modelSettings.models = data.models; modelSettings.base_url = data.base_url;
    modelSettings.message = data.models.length ? `已发现 ${data.models.length} 个模型。选择文本模型，测试后保存即可。` : '接口未返回模型；可展开“手动填写模型 ID”。';
  } catch (error) {
    if (sequence !== modelSettings.discovery) return;
    modelSettings.message = error.message+'。也可以手动填写模型 ID。'; modelSettings.error = true;
  } finally {
    if (sequence === modelSettings.discovery) {modelSettings.busy = false; if (state.page === 'settings') render();}
  }
}

let autoDiscoveryTimer;
document.addEventListener('input', event => {
  const mapping = {'model-base-url':'base_url','model-api-key':'api_key','model-manual':'model','model-filter':'filter'};
  const field = mapping[event.target.id];
  if (!field) return;
  modelSettings[field] = event.target.value;
  if (field === 'base_url' || field === 'api_key') {
    modelSettings.discovery++; modelSettings.models = []; modelSettings.busy = false;
    clearTimeout(autoDiscoveryTimer);
    if (field === 'base_url') {modelSettings.model = '';modelSettings.filter = '';}
  }
  if (field === 'filter') $('#model-choice').innerHTML = modelOptions();
  if (field === 'model') $('#model-choice').innerHTML = modelOptions();
});
document.addEventListener('focusout', event => {
  if (!['model-base-url','model-api-key'].includes(event.target.id)) return;
  clearTimeout(autoDiscoveryTimer);
  autoDiscoveryTimer = setTimeout(() => {
    if (['model-base-url','model-api-key'].includes(document.activeElement?.id)) return;
    discoverModels();
  }, 450);
});
document.addEventListener('change', event => {
  if (event.target.id === 'model-choice') {
    modelSettings.model = event.target.value;
    const manual = $('#model-manual'); if (manual) manual.value = modelSettings.model;
  }
  if (event.target.id === 'model-protocol') modelSettings.protocol = event.target.value;
  if (event.target.id === 'model-enabled') modelSettings.enabled = event.target.checked;
});
document.addEventListener('click', async event => {
  const action = event.target.closest('[data-model-action]')?.dataset.modelAction;
  if (!action) return;
  clearTimeout(autoDiscoveryTimer);
  if (action === 'discover') return discoverModels();
  if (!modelSettings.model.trim()) {toast('请先选择或填写模型 ID');return;}
  modelSettings.busy = true; modelSettings.error = false;
  modelSettings.message = action === 'test' ? '正在测试所选模型…' : '正在保存默认配置…';
  render();
  try {
    const data = await modelRequest(action === 'test' ? '/test' : '/settings', action === 'test' ? {...modelPayload(), enabled:true} : modelPayload());
    if (action === 'save') {
      modelSettings.saved = data; modelSettings.has_api_key = data.has_api_key;
      modelSettings.base_url = data.base_url; modelSettings.api_key = '';
      modelSettings.message = `已保存 ${data.model}，${data.enabled ? '立即生效，重启后继续使用。' : '当前已停用。'}`;
      toast('默认模型配置已保存');
    } else modelSettings.message = `连接成功 · ${data.model} · ${data.reply}`;
  } catch (error) {modelSettings.message = error.message;modelSettings.error = true;}
  finally {modelSettings.busy = false;if(state.page === 'settings')render();}
});
