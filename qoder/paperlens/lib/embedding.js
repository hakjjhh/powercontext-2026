/**
 * 简易确定性向量生成器（384 维）
 * 对相同文本始终产生相同向量，无需外部模型。
 * 生产环境应替换为 @seekdb/default-embed 或 OpenAI Embedding。
 */
const { VECTOR_DIM } = require('../config');

/**
 * 基于多轮 FNV-1a hash 的伪随机向量
 * @param {string} text
 * @returns {number[]} 长度为 VECTOR_DIM 的单位向量
 */
function generateEmbedding(text) {
  const vec = new Array(VECTOR_DIM).fill(0);

  // 字符级 hash: 对每个字符用不同位置的 seed 做 FNV-1a
  const str = text.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff\s]/g, ' ');
  const bytes = Buffer.from(str, 'utf-8');

  for (let d = 0; d < VECTOR_DIM; d++) {
    let hash = 0x811c9dc5; // FNV offset basis
    for (let i = 0; i < bytes.length; i++) {
      hash ^= (bytes[i] + d * 31) & 0xff;
      hash = Math.imul(hash, 0x01000193); // FNV prime
      hash >>>= 0; // keep as uint32
    }
    // 映射到 [-1, 1]
    vec[d] = ((hash % 2000) - 1000) / 1000;
  }

  // L2 归一化 → 单位向量，便于 COSINE_DISTANCE 计算
  const norm = Math.sqrt(vec.reduce((s, v) => s + v * v, 0)) || 1;
  return vec.map(v => v / norm);
}

/**
 * 将向量数组转为 seekdb VECTOR 列所需的字符串格式
 * @param {number[]} vec
 * @returns {string} 如 "[0.12, -0.34, ...]"
 */
function vectorToString(vec) {
  return '[' + vec.map(v => v.toFixed(6)).join(',') + ']';
}

module.exports = { generateEmbedding, vectorToString };
