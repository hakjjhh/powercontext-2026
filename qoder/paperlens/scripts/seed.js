/**
 * 导入 sample data 到数据库
 * 用法: node scripts/seed.js
 */
const fs = require('fs');
const path = require('path');
const { v4: uuidv4 } = require('uuid');
const { getClient, closeClient } = require('../lib/db');
const { generateEmbedding, vectorToString } = require('../lib/embedding');

async function main() {
  const dataPath = path.join(__dirname, '..', 'data', 'sample-papers.json');
  const papers = JSON.parse(fs.readFileSync(dataPath, 'utf-8'));

  console.log(`[seed] 准备导入 ${papers.length} 篇论文`);
  const client = await getClient();

  let count = 0;
  for (const paper of papers) {
    const id = uuidv4();
    const embedding = generateEmbedding(`${paper.title} ${paper.content}`);
    const vecStr = vectorToString(embedding);

    const sql = `
      INSERT INTO documents (id, title, content, authors, year, embedding)
      VALUES (?, ?, ?, ?, ?, ?)
    `;
    try {
      await client.execute(sql, [id, paper.title, paper.content, paper.authors || '', paper.year || 0, vecStr]);
      count++;
      process.stdout.write(`  ${count}. ${paper.title.slice(0, 50)}\n`);
    } catch (e) {
      console.error(`  ! 插入失败 (${paper.title}):`, e.message);
    }
  }

  await closeClient();
  console.log(`[seed] 完成，成功导入 ${count}/${papers.length} 篇`);
}

main().catch(e => {
  console.error('[seed] 失败:', e.message);
  process.exit(1);
});
