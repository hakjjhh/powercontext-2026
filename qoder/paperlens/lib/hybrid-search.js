/**
 * 混合检索模块
 * 一条 SQL 同时使用 MATCH ... AGAINST (全文) 与 COSINE_DISTANCE (向量)
 */
const { getClient } = require('./db');
const { generateEmbedding, vectorToString } = require('./embedding');
const config = require('../config');

/**
 * 混合搜索：全文 + 向量，单条 SQL
 * @param {string} queryText - 自然语言查询
 * @param {object} opts - { topK, textWeight, vecWeight }
 * @returns {Promise<Array>} 结果列表，含 relevance 分数
 */
async function hybridSearch(queryText, opts = {}) {
  const client = await getClient();
  const topK = opts.topK || config.TOP_K;
  const wText = opts.textWeight ?? config.HYBRID_WEIGHTS.text;
  const wVec = opts.vecWeight ?? config.HYBRID_WEIGHTS.vector;
  const threshold = opts.distanceThreshold ?? config.DISTANCE_THRESHOLD;

  // 生成查询向量
  const queryVec = generateEmbedding(queryText);
  const queryVecStr = vectorToString(queryVec);

  // 混合检索 SQL：
  //   - MATCH(content) AGAINST(?) 得到全文相关度分数 (0~正无穷，越大越相关)
  //   - COSINE_DISTANCE(embedding, ?) 得到向量距离 (0~2，越小越相似)
  //   - 综合 relevance = wText * text_score + wVec * (1 - vec_distance)
  const sql = `
    SELECT
      id,
      title,
      content,
      authors,
      year,
      MATCH(title, content) AGAINST(? IN NATURAL LANGUAGE MODE) AS text_score,
      COSINE_DISTANCE(embedding, ?) AS vec_distance,
      (MATCH(title, content) AGAINST(? IN NATURAL LANGUAGE MODE) * ?
        + (1 - COSINE_DISTANCE(embedding, ?)) * ?
      ) AS relevance
    FROM documents
    WHERE
      MATCH(title, content) AGAINST(? IN NATURAL LANGUAGE MODE) > 0
      OR COSINE_DISTANCE(embedding, ?) < ?
    ORDER BY relevance DESC
    LIMIT ?
  `;

  const params = [
    queryText,         // MATCH AGAINST (SELECT)
    queryVecStr,       // COSINE_DISTANCE (SELECT)
    queryText,         // MATCH AGAINST (SELECT relevance calc)
    wText,             // text weight
    queryVecStr,       // COSINE_DISTANCE (relevance calc)
    wVec,              // vector weight
    queryText,         // MATCH AGAINST (WHERE)
    queryVecStr,       // COSINE_DISTANCE (WHERE)
    threshold,         // distance threshold
    topK,              // LIMIT
  ];

  const rows = await client.execute(sql, params);

  // 标准化结果
  return (Array.isArray(rows) ? rows : []).map(row => ({
    id: row.id,
    title: row.title,
    content: (row.content || '').slice(0, 300),
    authors: row.authors || '',
    year: row.year || null,
    text_score: parseFloat(row.text_score) || 0,
    vec_distance: parseFloat(row.vec_distance) || 1,
    relevance: parseFloat(row.relevance) || 0,
  }));
}

/**
 * 纯向量检索（备选）
 */
async function vectorSearch(queryText, opts = {}) {
  const client = await getClient();
  const topK = opts.topK || config.TOP_K;
  const queryVec = generateEmbedding(queryText);
  const queryVecStr = vectorToString(queryVec);

  const sql = `
    SELECT id, title, content, authors, year,
           COSINE_DISTANCE(embedding, ?) AS vec_distance,
           (1 - COSINE_DISTANCE(embedding, ?)) AS relevance
    FROM documents
    ORDER BY vec_distance ASC
    LIMIT ?
  `;

  const rows = await client.execute(sql, [queryVecStr, queryVecStr, topK]);
  return (Array.isArray(rows) ? rows : []).map(row => ({
    id: row.id,
    title: row.title,
    content: (row.content || '').slice(0, 300),
    authors: row.authors || '',
    year: row.year || null,
    relevance: parseFloat(row.relevance) || 0,
    vec_distance: parseFloat(row.vec_distance) || 1,
  }));
}

module.exports = { hybridSearch, vectorSearch };
