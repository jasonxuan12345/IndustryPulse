const MODEL_PRESETS = [
  {
    id: 'deepseek',
    name: 'DeepSeek',
    base_url: 'https://api.deepseek.com/v1',
    protocol: 'chat_completions',
    needsKey: true,
    hint: '填入 DeepSeek API Key 后将自动获取可用模型（如 deepseek-chat / deepseek-reasoner）'
  },
  {
    id: 'openai',
    name: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    protocol: 'chat_completions',
    needsKey: true,
    hint: '填入 OpenAI API Key 后自动探测 gpt-4o / o3 等可用模型'
  },
  {
    id: 'qwen',
    name: '阿里通义千问 (DashScope)',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    protocol: 'chat_completions',
    needsKey: true,
    hint: '填入阿里云百炼 API Key 后自动获取 qwen 系列模型'
  },
  {
    id: 'zhipu',
    name: '智谱 GLM',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    protocol: 'chat_completions',
    needsKey: true,
    hint: '填入智谱 API Key 后自动探测 glm-4 等模型'
  },
  {
    id: 'moonshot',
    name: 'Kimi (Moonshot)',
    base_url: 'https://api.moonshot.cn/v1',
    protocol: 'chat_completions',
    needsKey: true,
    hint: '填入 Moonshot API Key 后自动获取 kimi 模型列表'
  },
  {
    id: 'siliconflow',
    name: '硅基流动 (SiliconFlow)',
    base_url: 'https://api.siliconflow.cn/v1',
    protocol: 'chat_completions',
    needsKey: true,
    hint: '填入 SiliconFlow API Key 后自动获取托管开源模型'
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    base_url: 'https://openrouter.ai/api/v1',
    protocol: 'chat_completions',
    needsKey: true,
    hint: '填入 OpenRouter API Key 后自动获取聚合模型列表'
  },
  {
    id: 'ollama',
    name: '本地 Ollama',
    base_url: 'http://127.0.0.1:11434/v1',
    protocol: 'chat_completions',
    needsKey: false,
    hint: '本地运行无需 API Key，选择后可直接探测本机已拉取的模型'
  },
  {
    id: 'custom',
    name: '自定义 OpenAI 兼容服务',
    base_url: '',
    protocol: 'chat_completions',
    needsKey: true,
    hint: '手动填写第三方或自建网关的 Base URL 与调用协议'
  }
];

const modelSettings = {
  loaded: false,
  loading: false,
  busy: false,
  preset: 'deepseek',
  models: [],
  filter: '',
  base_url: 'https://api.deepseek.com/v1',
  api_key: '',
  model: '',
  protocol: 'chat_completions',
  enabled: true,
  has_api_key: false,
  configured: false,
  message: '',
  error: false,
  saved: null,
  discovery: 0
};

function detectPreset(baseUrl) {
  const clean = String(baseUrl || '').trim().replace(/\/+$/, '');
  if (!clean) return 'deepseek';
  const match = MODEL_PRESETS.find(p => p.id !== 'custom' && p.base_url === clean);
  return match ? match.id : 'custom';
}

function modelPayload() {
  return {
    base_url: modelSettings.base_url.trim(),
    api_key: modelSettings.api_key,
    model: modelSettings.model.trim(),
    protocol: modelSettings.protocol,
    enabled: modelSettings.enabled
  };
}

function modelOptions() {
  const choices = [...modelSettings.models];
  if (modelSettings.model && !choices.some(item => item.id === modelSettings.model)) {
    choices.unshift({ id: modelSettings.model, owner: '' });
  }
  const placeholder = choices.length
    ? '<option value="">请选择可用模型</option>'
    : '<option value="">填入 API Key 后将自动探测可用模型</option>';
  return placeholder + choices
    .filter(item =>
      item.id.toLowerCase().includes(modelSettings.filter.toLowerCase()) ||
      item.id === modelSettings.model
    )
    .map(item =>
      `<option value="${escapeHtml(item.id)}" ${item.id === modelSettings.model ? 'selected' : ''}>` +
      `${escapeHtml(item.id)}${item.owner ? ' · ' + escapeHtml(item.owner) : ''}</option>`
    ).join('');
}

function modelPanel() {
  const saved = modelSettings.saved;
  const presetObj = MODEL_PRESETS.find(p => p.id === modelSettings.preset) || MODEL_PRESETS[0];
  const isCustom = modelSettings.preset === 'custom';
  const message = modelSettings.message || (
    modelSettings.loading ? '正在读取已保存配置…' : presetObj.hint
  );

  return panel('AI 模型与 API 快速接入', `<div class="panel-body">
    <div class="model-connection-grid">
      <div>
        <label class="wb-field">1. 选择常用服务商
          <select id="model-preset">
            ${MODEL_PRESETS.map(p => `<option value="${p.id}" ${p.id === modelSettings.preset ? 'selected' : ''}>${escapeHtml(p.name)}</option>`).join('')}
          </select>
        </label>
        <label class="wb-field" style="margin-top:14px">2. 填入 API Key ${presetObj.needsKey ? '' : '（本地免密可留空）'}
          <input id="model-api-key" type="password" autocomplete="off" placeholder="${modelSettings.has_api_key ? '已保存密钥（留空沿用；填入新密钥将重新探测）' : '粘贴你的 API Key，系统将自动探测可用模型'}" value="${escapeHtml(modelSettings.api_key)}">
        </label>
        <div class="actions" style="margin-top:12px">
          <button class="control" type="button" data-model-action="discover" ${modelSettings.busy ? 'disabled' : ''}>${modelSettings.busy ? '正在探测可用模型…' : '↻ 探测可用模型'}</button>
          <span class="muted" style="font-size:11px">${modelSettings.models.length ? `已发现 ${modelSettings.models.length} 个可用模型` : escapeHtml(modelSettings.base_url || '待填接口地址')}</span>
        </div>
        ${isCustom ? `
          <label class="wb-field" style="margin-top:14px">自定义 API Base URL
            <input id="model-base-url" type="url" placeholder="https://your-provider.example/v1" autocomplete="off" value="${escapeHtml(modelSettings.base_url)}">
          </label>
          <label class="wb-field" style="margin-top:14px">调用协议
            <select id="model-protocol">
              <option value="chat_completions" ${modelSettings.protocol === 'chat_completions' ? 'selected' : ''}>OpenAI 兼容 · Chat Completions</option>
              <option value="responses" ${modelSettings.protocol === 'responses' ? 'selected' : ''}>OpenAI Responses</option>
            </select>
          </label>
        ` : ''}
      </div>
      <div>
        <label class="wb-field">3. 选择模型 ${modelSettings.models.length > 8 ? '<small class="muted">（可用下方搜索过滤）</small>' : ''}
          <select id="model-choice">${modelOptions()}</select>
        </label>
        ${modelSettings.models.length > 6 ? `
          <label class="wb-field" style="margin-top:10px">筛选模型名称
            <input id="model-filter" placeholder="输入关键词过滤，如 chat / reasoner / qwen…" value="${escapeHtml(modelSettings.filter)}">
          </label>
        ` : ''}
        <details style="margin-top:12px">
          <summary class="muted" style="font-size:11px;cursor:pointer">接口未返回列表？手动填写模型 ID</summary>
          <label class="wb-field" style="margin-top:10px">模型 ID
            <input id="model-manual" placeholder="例如 deepseek-chat" value="${escapeHtml(modelSettings.model)}">
          </label>
        </details>
        <label style="display:flex;gap:8px;align-items:center;font-size:12px;margin-top:16px">
          <input id="model-enabled" type="checkbox" style="width:auto;accent-color:var(--green)" ${modelSettings.enabled ? 'checked' : ''}>
          启用此模型用于 AI 蝴蝶效应扫描与投喂核验
        </label>
        <div class="actions">
          <button class="control" type="button" data-model-action="test" ${modelSettings.busy ? 'disabled' : ''}>测试连通性</button>
          <button class="control primary" type="button" data-model-action="save" ${modelSettings.busy ? 'disabled' : ''}>保存并设为默认</button>
        </div>
      </div>
    </div>
    <div id="model-message" class="${modelSettings.error ? 'wb-error' : 'notice'}" style="margin-top:18px;margin-bottom:0" role="status">${escapeHtml(message)}</div>
    <div class="setting-line"><span>当前已生效配置</span><b>${saved?.configured && saved.enabled ? `${escapeHtml(saved.model)} (${escapeHtml(saved.base_url)})` : saved?.configured ? '已保存 · 暂未启用' : '尚未配置'}</b></div>
  </div>`, '<span class="pill">PERSISTENT / 持久保存</span>', 'MODEL CONNECTION / 一键接入');
}

const modelStyle = document.createElement('style');
modelStyle.textContent = '.model-connection-grid{display:grid;grid-template-columns:1fr 1fr;gap:28px}.model-connection-grid input,.model-connection-grid select{min-height:39px}.model-connection-grid button:disabled{opacity:.45;cursor:wait}@media(max-width:800px){.model-connection-grid{grid-template-columns:1fr;gap:22px}}';
document.head.append(modelStyle);

async function modelRequest(path, payload) {
  const response = await fetch('/api/dashboard/model' + path, payload === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查 API 配置和模型名称');
  return data;
}

async function loadModelSettings() {
  if (modelSettings.loading) return;
  modelSettings.loading = true;
  try {
    const data = await modelRequest('/settings');
    Object.assign(modelSettings, data, { saved: data, loaded: true });
    if (data.configured && data.base_url) {
      modelSettings.preset = detectPreset(data.base_url);
    } else {
      modelSettings.preset = 'deepseek';
      modelSettings.base_url = 'https://api.deepseek.com/v1';
      modelSettings.protocol = 'chat_completions';
      modelSettings.enabled = true;
    }
  } catch (error) {
    modelSettings.message = error.message;
    modelSettings.error = true;
  } finally {
    modelSettings.loading = false;
    modelSettings.loaded = true;
    if (state.page === 'settings') render();
  }
}

async function discoverModels() {
  if (!modelSettings.base_url.trim()) return;
  const presetObj = MODEL_PRESETS.find(p => p.id === modelSettings.preset);
  if (presetObj?.needsKey && !modelSettings.api_key.trim() && !modelSettings.has_api_key) {
    return;
  }
  const sequence = ++modelSettings.discovery;
  modelSettings.busy = true;
  modelSettings.error = false;
  modelSettings.message = '正在自动探测可用模型列表…';
  if (state.page === 'settings') render();
  try {
    const data = await modelRequest('/models', modelPayload());
    if (sequence !== modelSettings.discovery) return;
    modelSettings.models = data.models || [];
    modelSettings.base_url = data.base_url;
    if (modelSettings.models.length && !modelSettings.models.some(m => m.id === modelSettings.model)) {
      const preferred = modelSettings.models.find(m => /chat|gpt-4o|qwen-plus|glm-4/i.test(m.id));
      modelSettings.model = (preferred || modelSettings.models[0]).id;
    }
    modelSettings.message = modelSettings.models.length
      ? `探测成功：共 ${modelSettings.models.length} 个可用模型，已为你选中 ${modelSettings.model || '首个模型'}，直接保存即可。`
      : '接口未返回模型列表，可展开下方手动填写模型 ID。';
  } catch (error) {
    if (sequence !== modelSettings.discovery) return;
    modelSettings.message = `${error.message}。请检查 Key 或展开手动填写模型 ID。`;
    modelSettings.error = true;
  } finally {
    if (sequence === modelSettings.discovery) {
      modelSettings.busy = false;
      if (state.page === 'settings') render();
    }
  }
}

let autoDiscoveryTimer;
document.addEventListener('input', event => {
  const mapping = {
    'model-base-url': 'base_url',
    'model-api-key': 'api_key',
    'model-manual': 'model',
    'model-filter': 'filter'
  };
  const field = mapping[event.target.id];
  if (!field) return;
  modelSettings[field] = event.target.value;
  if (field === 'filter' || field === 'model') {
    const choice = $('#model-choice');
    if (choice) choice.innerHTML = modelOptions();
    return;
  }
  if (field === 'base_url' || field === 'api_key') {
    modelSettings.discovery++;
    modelSettings.models = [];
    clearTimeout(autoDiscoveryTimer);
    if (field === 'base_url') {
      modelSettings.model = '';
      modelSettings.filter = '';
    }
    if (field === 'api_key' && modelSettings.api_key.trim().length >= 8) {
      autoDiscoveryTimer = setTimeout(() => discoverModels(), 500);
    }
  }
});

document.addEventListener('change', event => {
  if (event.target.id === 'model-preset') {
    const preset = MODEL_PRESETS.find(p => p.id === event.target.value) || MODEL_PRESETS[0];
    modelSettings.preset = preset.id;
    modelSettings.models = [];
    modelSettings.model = '';
    modelSettings.filter = '';
    modelSettings.error = false;
    if (preset.id !== 'custom') {
      modelSettings.base_url = preset.base_url;
      modelSettings.protocol = preset.protocol;
    } else {
      modelSettings.base_url = '';
    }
    modelSettings.message = preset.hint;
    render();
    if (!preset.needsKey || modelSettings.api_key.trim().length >= 8) {
      discoverModels();
    }
    return;
  }
  if (event.target.id === 'model-choice') {
    modelSettings.model = event.target.value;
    const manual = $('#model-manual');
    if (manual) manual.value = modelSettings.model;
  }
  if (event.target.id === 'model-protocol') modelSettings.protocol = event.target.value;
  if (event.target.id === 'model-enabled') modelSettings.enabled = event.target.checked;
});

document.addEventListener('click', async event => {
  const action = event.target.closest('[data-model-action]')?.dataset.modelAction;
  if (!action) return;
  clearTimeout(autoDiscoveryTimer);
  if (action === 'discover') {
    await discoverModels();
    return;
  }
  if (!modelSettings.model.trim()) {
    toast('请先选择或填写一个模型 ID');
    return;
  }
  modelSettings.busy = true;
  modelSettings.error = false;
  modelSettings.message = action === 'test' ? '正在测试连通性…' : '正在保存默认配置…';
  render();
  try {
    const data = await modelRequest(
      action === 'test' ? '/test' : '/settings',
      action === 'test' ? { ...modelPayload(), enabled: true } : modelPayload()
    );
    if (action === 'save') {
      modelSettings.saved = data;
      modelSettings.has_api_key = data.has_api_key;
      modelSettings.base_url = data.base_url;
      modelSettings.api_key = '';
      modelSettings.message = `已保存 ${data.model}，${data.enabled ? '立即生效，可用于 AI 扫描与交叉核验。' : '当前已停用。'}`;
      toast('默认模型配置已保存');
    } else {
      modelSettings.message = `连接成功 · ${data.model} · ${data.reply}`;
    }
  } catch (error) {
    modelSettings.message = error.message;
    modelSettings.error = true;
  } finally {
    modelSettings.busy = false;
    if (state.page === 'settings') render();
  }
});
