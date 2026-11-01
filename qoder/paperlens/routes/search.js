/**
 * 搜索路由
 */
const { Router } = require('express');
const { hybridSearch } = require('../lib/hybrid-search');

const router = Router();

/**
 * GET /api/search?q=...&topK=10
 * 混合检索（全文 + 向量）
 */
router.get('/', async (req, res, next) => {
  try {
    const q = (req.query.q || '').trim();
    if (!q) {
      return res.status(400).json({ error: '请提供查询关键词 q' });
    }

    const topK = parseInt(req.query.topK) || 10;
    const textWeight = parseFloat(req.query.textWeight) || undefined;
    const vecWeight = parseFloat(req.query.vecWeight) || undefined;

    const results = await hybridSearch(q, { topK, textWeight, vecWeight });

    res.json({
      query: q,
      count: results.length,
      results,
    });
  } catch (e) {
    next(e);
  }
});

module.exports = router;
