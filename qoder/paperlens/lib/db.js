/**
 * seekdb 连接管理 (Server 模式)
 */
const { SeekdbClient, SeekdbAdminClient } = require('seekdb');
const config = require('../config');

let _client = null;

/**
 * 获取 / 创建 SeekdbClient 单例
 */
async function getClient() {
  if (_client) return _client;

  _client = new SeekdbClient({
    host: config.SEEKDB.host,
    port: config.SEEKDB.port,
    user: config.SEEKDB.user,
    password: config.SEEKDB.password,
    database: config.SEEKDB.database,
  });

  return _client;
}

/**
 * 获取 AdminClient (用于建库等管理操作)
 */
function getAdminClient() {
  return new SeekdbAdminClient({
    host: config.SEEKDB.host,
    port: config.SEEKDB.port,
    user: config.SEEKDB.user,
    password: config.SEEKDB.password,
  });
}

/**
 * 关闭连接
 */
async function closeClient() {
  if (_client) {
    await _client.close();
    _client = null;
  }
}

/**
 * 初始化数据库和表结构
 * - 创建数据库 paperlens (若不存在)
 * - 创建 documents 表（含全文索引 + 向量列）
 */
async function initSchema() {
  // 1. 用 AdminClient 创建数据库
  const admin = getAdminClient();
  try {
    const dbs = await admin.listDatabases();
    const exists = dbs.some(d => d.Name === config.SEEKDB.database || d === config.SEEKDB.database);
    if (!exists) {
      await admin.createDatabase(config.SEEKDB.database);
      console.log(`[init] 数据库 ${config.SEEKDB.database} 已创建`);
    } else {
      console.log(`[init] 数据库 ${config.SEEKDB.database} 已存在`);
    }
  } catch (e) {
    console.warn('[init] admin 操作:', e.message);
  }
  await admin.close();

  // 2. 用 SeekdbClient 建表
  const client = await getClient();

  const createTableSQL = `
    CREATE TABLE IF NOT EXISTS documents (
      id VARCHAR(64) PRIMARY KEY,
      title VARCHAR(512) NOT NULL,
      content TEXT NOT NULL,
      authors VARCHAR(512) DEFAULT '',
      year INT DEFAULT 0,
      embedding VECTOR(${config.VECTOR_DIM}),
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      FULLTEXT INDEX ft_content (title, content),
      VECTOR INDEX vec_idx (embedding) WITH (distance=cosine, type=hnsw, lib=vsag)
    )
  `;

  try {
    await client.execute(createTableSQL);
    console.log('[init] documents 表结构已就绪（全文索引 + 向量索引）');
  } catch (e) {
    // 表可能已存在
    if (e.message && e.message.includes('already exists')) {
      console.log('[init] documents 表已存在');
    } else {
      throw e;
    }
  }
}

module.exports = { getClient, getAdminClient, closeClient, initSchema };
