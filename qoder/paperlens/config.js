/**
 * PaperLens 配置
 */
module.exports = {
  // Express 服务端口
  PORT: process.env.PORT || 3789,

  // seekdb 连接配置 (Server 模式)
  SEEKDB: {
    host: process.env.SEEKDB_HOST || '127.0.0.1',
    port: parseInt(process.env.SEEKDB_PORT || '10000', 10),
    user: process.env.SEEKDB_USER || 'root',
    password: process.env.SEEKDB_PASSWORD || '',
    database: process.env.SEEKDB_DATABASE || 'paperlens',
  },

  // 向量维度
  VECTOR_DIM: 384,

  // 混合检索权重
  HYBRID_WEIGHTS: {
    text: 0.4,    // 全文检索权重
    vector: 0.6,  // 向量检索权重
  },

  // 默认返回结果数
  TOP_K: 10,

  // COSINE_DISTANCE 阈值 (超过此值的视为不相关)
  DISTANCE_THRESHOLD: 0.95,
};
