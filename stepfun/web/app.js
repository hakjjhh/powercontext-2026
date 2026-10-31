// Browser-side embedding with Transformers.js. No inference request ever
// leaves the machine: only the resulting float vectors are sent to seekdb.
// The library is vendored under web/vendor so the demo works without a CDN.
import { pipeline, env } from './vendor/transformers.min.js';

// Model files are vendored under web/models/... so the demo runs offline.
// Use ?remote=1 (&mirror=1) to fetch them from Hugging Face instead.
const params = new URLSearchParams(location.search);
const useRemote = params.get('remote') === '1';
env.allowLocalModels = !useRemote;
env.allowRemoteModels = useRemote;
env.localModelPath = './models/';
env.backends.onnx.wasm.wasmPaths = './vendor/';
// The Cache API only exists in secure contexts; plain-http LAN access would
// otherwise throw "Browser cache is not available in this environment".
env.useBrowserCache = typeof caches !== 'undefined';
// Optional mirror for networks where huggingface.co is slow: open the page
// with ?mirror=1 to pull model files from hf-mirror.com instead.
if (useRemote && params.get('mirror') === '1') env.remoteHost = 'https://hf-mirror.com';

const MODEL_ID = 'Xenova/all-MiniLM-L6-v2';
const DIM = 384;
const BATCH = 16;

const $ = (id) => document.getElementById(id);
const metrics = {};
let extractor = null;
let backend = 'wasm';
let dtype = 'fp32';

function setMetrics(extra = {}) {
  Object.assign(metrics, extra);
  $('metrics').textContent = JSON.stringify(metrics, null, 2);
}

function setBadge(kind, text) {
  const el = $('backend');
  el.textContent = `后端：${text}`;
  el.className = `badge ${kind === 'webgpu' ? 'badge-gpu' : kind === 'wasm' ? 'badge-wasm' : 'badge-wait'}`;
}

async function buildPipeline(wantDtype) {
  dtype = wantDtype;
  if (navigator.gpu) {
    try {
      setBadge('wait', 'WebGPU 初始化…');
      const p = await pipeline('feature-extraction', MODEL_ID, { device: 'webgpu', dtype: wantDtype });
      backend = 'webgpu';
      setBadge('webgpu', `WebGPU · ${wantDtype}`);
      return p;
    } catch (e) {
      console.warn('WebGPU unavailable, falling back to WASM', e);
    }
  }
  setBadge('wait', 'WASM 初始化…');
  const p = await pipeline('feature-extraction', MODEL_ID, { device: 'wasm', dtype: wantDtype === 'q8' ? 'q8' : 'fp32' });
  backend = 'wasm';
  setBadge('wasm', `WASM · ${wantDtype}`);
  return p;
}

async function embed(texts) {
  const out = [];
  const t0 = performance.now();
  for (let i = 0; i < texts.length; i += BATCH) {
    const batch = texts.slice(i, i + BATCH);
    const res = await extractor(batch, { pooling: 'mean', normalize: true });
    const list = res.tolist();
    out.push(...list);
    if (texts.length > BATCH) {
      $('bar').style.width = `${Math.round((out.length / texts.length) * 100)}%`;
    }
  }
  const ms = performance.now() - t0;
  setMetrics({ embedBackend: backend, dtype, embedTexts: texts.length, embedMs: Number(ms.toFixed(1)), embedMsPerText: Number((ms / Math.max(texts.length, 1)).toFixed(2)) });
  return out;
}

async function api(path, body) {
  const r = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await r.json();
  if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

async function refreshHealth() {
  try {
    const h = await api('/api/health');
    $('docs').textContent = `文档：${h.docs}`;
  } catch (e) {
    $('docs').textContent = '文档：DB 未连接';
  }
}

$('load').onclick = async () => {
  const wantDtype = document.querySelector('input[name=dtype]:checked').value;
  $('load').disabled = true;
  $('loadStatus').textContent = '加载中…';
  const t0 = performance.now();
  try {
    extractor = await buildPipeline(wantDtype);
    const ms = performance.now() - t0;
    $('loadStatus').textContent = `模型就绪（${(ms / 1000).toFixed(1)}s）`;
    setMetrics({ loadMs: Number(ms.toFixed(0)), backend, dtype });
  } catch (e) {
    $('loadStatus').textContent = `加载失败：${e.message}`;
  } finally {
    $('load').disabled = false;
  }
};

$('import').onclick = async () => {
  if (!extractor) return alert('请先加载模型');
  const lines = $('corpus').value.split('\n').map((s) => s.trim()).filter(Boolean);
  if (!lines.length) return;
  const docs = lines.map((line, i) => {
    const idx = line.indexOf('::');
    return idx > 0
      ? { id: `doc-${Date.now()}-${i}`, title: line.slice(0, idx).trim(), text: line.slice(idx + 2).trim() }
      : { id: `doc-${Date.now()}-${i}`, title: '', text: line };
  });
  $('importStatus').textContent = `向量化 ${docs.length} 条…`;
  $('bar').style.width = '0%';
  const t0 = performance.now();
  try {
    const embeddings = await embed(docs.map((d) => d.text));
    const tEmbed = performance.now() - t0;
    const r = await api('/api/docs', { docs, embeddings });
    $('importStatus').textContent = `已写入 ${r.added} 条（向量化 ${(tEmbed / 1000).toFixed(2)}s）`;
    $('bar').style.width = '100%';
    await refreshHealth();
  } catch (e) {
    $('importStatus').textContent = `失败：${e.message}`;
  }
};

$('search').onclick = async () => {
  if (!extractor) return alert('请先加载模型');
  const q = $('query').value.trim();
  if (!q) return;
  $('searchStatus').textContent = '检索中…';
  const t0 = performance.now();
  try {
    const [vec] = await embed([q]);
    const tEmbed = performance.now() - t0;
    const r = await api('/api/search', { query: q, embedding: vec, k: 5 });
    $('searchStatus').textContent = `端侧向量化 ${tEmbed.toFixed(0)} ms · seekdb 检索 ${r.ms} ms`;
    $('results').innerHTML = r.results.map((x) => `
      <li>
        <div class="doc">${escapeHtml(x.text)}</div>
        <div class="meta">cosine=${x.distance.toFixed(4)} · fulltext=${x.fulltextScore.toFixed(4)} · id=${escapeHtml(x.id)}</div>
      </li>`).join('') || '<li>没有结果</li>';
    setMetrics({ searchEmbedMs: Number(tEmbed.toFixed(1)), seekdbSearchMs: r.ms });
  } catch (e) {
    $('searchStatus').textContent = `失败：${e.message}`;
  }
};

$('reset').onclick = async () => {
  if (!confirm('确定清空知识库？')) return;
  await api('/api/reset', {});
  $('results').innerHTML = '';
  $('importStatus').textContent = '已清空';
  await refreshHealth();
};

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

refreshHealth();
