/**
 * 文档管理路由: CRUD + 导入
 */
const { Router } = require('express');
const multer = require('multer');
const { v4: uuidv4 } = require('uuid');
const { getClient } = require('../lib/db');
const { generateEmbedding, vectorToString } = require('../lib/embedding');

const router = Router();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 10 * 1024 * 1024 } });

/**
 * POST /api/documents
 * 导入单篇文档 (JSON body)
 * body: { title, content, authors?, year? }
 */
router.post('/', async (req, res, next) => {
  try {
    const { title, content, authors = '', year = 0 } = req.body;
    if (!title || !content) {
      return res.status(400).json({ error: 'title 和 content 为必填字段' });
    }

    const client = await getClient();
    const id = uuidv4();
    const embedding = generateEmbedding(`${title} ${content}`);
    const vecStr = vectorToString(embedding);

    const sql = `
      INSERT INTO documents (id, title, content, authors, year, embedding)
      VALUES (?, ?, ?, ?, ?, ?)
    `;
    await client.execute(sql, [id, title, content, authors, parseInt(year) || 0, vecStr]);

    res.status(201).json({ ok: true, id, title });
  } catch (e) {
    next(e);
  }
});

/**
 * POST /api/documents/batch
 * 批量导入 (JSON body)
 * body: { documents: [{ title, content, authors?, year? }, ...] }
 */
router.post('/batch', async (req, res, next) => {
  try {
    const { documents } = req.body;
    if (!Array.isArray(documents) || documents.length === 0) {
      return res.status(400).json({ error: 'documents 数组不能为空' });
    }

    const client = await getClient();
    const results = [];

    for (const doc of documents) {
      const { title, content, authors = '', year = 0 } = doc;
      if (!title || !content) continue;

      const id = uuidv4();
      const embedding = generateEmbedding(`${title} ${content}`);
      const vecStr = vectorToString(embedding);

      const sql = `
        INSERT INTO documents (id, title, content, authors, year, embedding)
        VALUES (?, ?, ?, ?, ?, ?)
      `;
      await client.execute(sql, [id, title, content, authors, parseInt(year) || 0, vecStr]);
      results.push({ id, title });
    }

    res.status(201).json({ ok: true, count: results.length, documents: results });
  } catch (e) {
    next(e);
  }
});

/**
 * POST /api/documents/upload
 * 上传 JSON 文件批量导入 (multipart/form-data)
 * 文件内容: [{ title, content, authors?, year? }, ...]
 */
router.post('/upload', upload.single('file'), async (req, res, next) => {
  try {
    if (!req.file) {
      return res.status(400).json({ error: '请上传 JSON 文件' });
    }

    const text = req.file.buffer.toString('utf-8');
    const documents = JSON.parse(text);

    if (!Array.isArray(documents)) {
      return res.status(400).json({ error: '文件内容应为 JSON 数组' });
    }

    // 复用 batch 逻辑
    req.body = { documents };
    next();
  } catch (e) {
    if (e instanceof SyntaxError) {
      return res.status(400).json({ error: '文件不是有效的 JSON' });
    }
    next(e);
  }
}, async (req, res, next) => {
  // 内部转发到 batch handler
  const handler = router.stack.find(r => r.route && r.route.path === '/batch');
  if (handler) return handler.route.stack[0].handle(req, res, next);
  res.status(500).json({ error: '内部路由错误' });
});

/**
 * GET /api/documents
 * 获取文档列表（分页）
 */
router.get('/', async (req, res, next) => {
  try {
    const page = parseInt(req.query.page) || 1;
    const size = Math.min(parseInt(req.query.size) || 20, 100);
    const offset = (page - 1) * size;

    const client = await getClient();
    const rows = await client.execute(
      'SELECT id, title, authors, year, created_at FROM documents ORDER BY created_at DESC LIMIT ? OFFSET ?',
      [size, offset]
    );
    const countRows = await client.execute('SELECT COUNT(*) AS total FROM documents');

    res.json({
      documents: Array.isArray(rows) ? rows : [],
      total: countRows?.[0]?.total || 0,
      page,
      size,
    });
  } catch (e) {
    next(e);
  }
});

/**
 * DELETE /api/documents/:id
 */
router.delete('/:id', async (req, res, next) => {
  try {
    const client = await getClient();
    await client.execute('DELETE FROM documents WHERE id = ?', [req.params.id]);
    res.json({ ok: true, id: req.params.id });
  } catch (e) {
    next(e);
  }
});

module.exports = router;
