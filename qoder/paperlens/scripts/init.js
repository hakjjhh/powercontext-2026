/**
 * 初始化数据库 schema
 * 用法: node scripts/init.js
 */
const { initSchema, closeClient } = require('../lib/db');

async function main() {
  console.log('[init] 连接 seekdb:', require('../config').SEEKDB.host + ':' + require('../config').SEEKDB.port);
  await initSchema();
  await closeClient();
  console.log('[init] 完成');
}

main().catch(e => {
  console.error('[init] 失败:', e.message);
  process.exit(1);
});
