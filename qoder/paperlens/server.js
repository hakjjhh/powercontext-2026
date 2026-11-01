/**
 * PaperLens 论文速查 - Express 主服务
 */
const express = require('express');
const path = require('path');
const config = require('./config');
const { getClient, closeClient } = require('./lib/db');

const app = express();

// ── 中间件 ──────────────────────────────────────────
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true }));
app.use(express.static(path.join(__dirname, 'public')));

// ── 路由 ────────────────────────────────────────────
app.use('/api/documents', require('./routes/documents'));
app.use('/api/search', require('./routes/search'));

// ── 健康检查 ────────────────────────────────────────
app.get('/api/health', async (req, res) => {
  try {
    const client = await getClient();
    const rows = await client.execute('SELECT 1 AS ok');
    res.json({ status: 'ok', db: 'connected' });
  } catch (e) {
    res.status(503).json({ status: 'degraded', db: 'disconnected', error: e.message });
  }
});

// ── 全局错误处理 ────────────────────────────────────
app.use((err, req, res, next) => {
  console.error('[error]', err.message || err);
  const status = err.status || 500;
  res.status(status).json({ error: err.message || 'Internal Server Error' });
});

// ── 启动 ────────────────────────────────────────────
const server = app.listen(config.PORT, () => {
  console.log(`\n  PaperLens 论文速查`);
  console.log(`  ─────────────────────`);
  console.log(`  服务地址: http://localhost:${config.PORT}`);
  console.log(`  seekdb:   ${config.SEEKDB.host}:${config.SEEKDB.port} / ${config.SEEKDB.database}`);
  console.log(`  向量维度: ${config.VECTOR_DIM}`);
  console.log(`\n  打开浏览器访问上述地址即可使用\n`);
});

// ── 优雅关闭 ────────────────────────────────────────
process.on('SIGINT', async () => {
  console.log('\n[shutdown] 正在关闭...');
  await closeClient();
  server.close(() => process.exit(0));
});

module.exports = app;
