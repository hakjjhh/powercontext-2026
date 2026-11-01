/**
 * 冒烟测试：启动服务 → 导入 → 搜索 → 验证结果
 * 用法: node scripts/smoke-test.js
 * 前提: seekdb server 已在 127.0.0.1:10000 运行
 */
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');

const PORT = process.env.PORT || 3789;
const BASE = `http://localhost:${PORT}`;

function request(method, urlPath, body) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlPath, BASE);
    const opts = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    const req = http.request(opts, res => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, body: JSON.parse(data) });
        } catch {
          resolve({ status: res.statusCode, body: data });
        }
      });
    });
    req.on('error', reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

async function waitForServer(timeout = 10000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try {
      const res = await request('GET', '/api/health');
      if (res.status === 200 || res.status === 503) return res;
    } catch {}
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error('服务启动超时');
}

async function main() {
  console.log('═══ PaperLens 冒烟测试 ═══\n');

  // 启动服务
  console.log('[1/5] 启动 Express 服务...');
  const server = spawn('node', ['server.js'], {
    cwd: path.join(__dirname, '..'),
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PORT: String(PORT) },
  });

  let serverOutput = '';
  server.stdout.on('data', d => { serverOutput += d.toString(); });
  server.stderr.on('data', d => { serverOutput += d.toString(); });

  // 等待服务就绪
  const health = await waitForServer();
  console.log(`  ✓ 服务已启动 (${BASE})`);
  console.log(`  ✓ DB 状态: ${health.body.db || health.body.error}\n`);

  let passed = 0;
  let failed = 0;

  // 测试 1: 导入文档
  console.log('[2/5] 测试文档导入...');
  try {
    const doc = {
      title: '冒烟测试论文: Hybrid Search Evaluation',
      content: 'This is a test paper about hybrid search combining BM25 full-text scoring with cosine similarity vector search. The relevance ranking uses a weighted combination of text_score and vec_distance.',
      authors: 'Test Bot',
      year: 2024,
    };
    const res = await request('POST', '/api/documents', doc);
    if (res.status === 201 && res.body.ok) {
      console.log(`  ✓ 导入成功, id=${res.body.id}`);
      passed++;
    } else {
      console.log(`  ✗ 导入失败: ${JSON.stringify(res.body)}`);
      failed++;
    }
  } catch (e) {
    console.log(`  ✗ 导入异常: ${e.message}`);
    failed++;
  }

  // 测试 2: 搜索
  console.log('\n[3/5] 测试混合检索...');
  try {
    const res = await request('GET', '/api/search?q=hybrid+search+vector+cosine');
    if (res.status === 200 && Array.isArray(res.body.results)) {
      console.log(`  ✓ 搜索返回 ${res.body.count} 条结果`);
      if (res.body.results.length > 0) {
        const top = res.body.results[0];
        console.log(`  ✓ Top-1: "${top.title.slice(0, 40)}" relevance=${top.relevance.toFixed(4)}`);
      }
      passed++;
    } else {
      console.log(`  ✗ 搜索失败: ${JSON.stringify(res.body)}`);
      failed++;
    }
  } catch (e) {
    console.log(`  ✗ 搜索异常: ${e.message}`);
    failed++;
  }

  // 测试 3: 前端页面可访问
  console.log('\n[4/5] 测试前端页面...');
  try {
    const res = await new Promise((resolve, reject) => {
      http.get(`${BASE}/`, r => {
        let data = '';
        r.on('data', c => data += c);
        r.on('end', () => resolve({ status: r.statusCode, body: data }));
      }).on('error', reject);
    });
    if (res.status === 200 && res.body.includes('PaperLens')) {
      console.log('  ✓ 前端页面正常');
      passed++;
    } else {
      console.log('  ✗ 前端页面异常');
      failed++;
    }
  } catch (e) {
    console.log(`  ✗ 前端请求失败: ${e.message}`);
    failed++;
  }

  // 测试 4: 文档列表
  console.log('\n[5/5] 测试文档列表 API...');
  try {
    const res = await request('GET', '/api/documents');
    if (res.status === 200 && Array.isArray(res.body.documents)) {
      console.log(`  ✓ 文档列表: ${res.body.total} 篇`);
      passed++;
    } else {
      console.log(`  ✗ 列表失败`);
      failed++;
    }
  } catch (e) {
    console.log(`  ✗ 列表异常: ${e.message}`);
    failed++;
  }

  // 清理
  server.kill('SIGTERM');

  console.log(`\n═══ 结果: ${passed} 通过 / ${failed} 失败 ═══`);
  if (failed > 0) process.exit(1);
}

main().catch(e => {
  console.error('冒烟测试异常:', e.message);
  process.exit(1);
});
