// StepFun mini knowledge base: browser-side embeddings + seekdb hybrid retrieval.
//
// The browser (Transformers.js / ONNX Runtime Web) computes every embedding;
// this server never calls a model. seekdb-js is used to create the collection
// and to run one SQL statement that combines full-text MATCH with vector
// distance, so ranking happens inside seekdb.
import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { SeekdbClient, Schema, FulltextIndexConfig, VectorIndexConfig } from 'seekdb';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 8787);
const DIM = 384;
const COLLECTION = process.env.COLLECTION || 'kb';
const DB = {
  host: process.env.SEEKDB_HOST || '127.0.0.1',
  port: Number(process.env.SEEKDB_PORT || 10000),
  user: process.env.SEEKDB_USER || 'root',
  password: process.env.SEEKDB_PASSWORD || '',
  database: process.env.SEEKDB_DATABASE || 'stepfun',
};

const client = new SeekdbClient(DB);
const schema = new Schema({
  fulltextIndex: new FulltextIndexConfig('ik', { ik_mode: 'smart' }),
  vectorIndex: new VectorIndexConfig({
    hnsw: { dimension: DIM, distance: 'cosine' },
    embeddingFunction: null,
  }),
});

let collection = null;
let tableName = null;

async function ensureCollection() {
  if (collection) return collection;
  try {
    collection = await client.getCollection({ name: COLLECTION });
  } catch {
    try {
      collection = await client.createCollection({ name: COLLECTION, schema });
    } catch {
      collection = await client.getCollection({ name: COLLECTION });
    }
  }
  return collection;
}

async function resolveTable() {
  if (tableName) return tableName;
  const rows = await client.execute(`SHOW TABLES FROM \`${DB.database}\``);
  const names = rows.map((r) => String(Object.values(r)[0])).filter((n) => n.startsWith('c$v2$'));
  if (!names.length) throw new Error('no collection table found');
  tableName = names[names.length - 1];
  return tableName;
}

const sqlQuote = (s) => String(s).replace(/\\/g, '\\\\').replace(/'/g, "''");
const vecLiteral = (v) => `[${v.map((x) => Number(x).toFixed(7)).join(',')}]`;
const idText = (id) => (Buffer.isBuffer(id) ? id.toString('utf8') : String(id));

const app = express();
app.use(express.json({ limit: '32mb' }));
app.use(express.static(path.join(__dirname, '..', 'web')));

app.get('/api/health', async (_req, res) => {
  try {
    const t = await resolveTable();
    const rows = await client.execute(`SELECT COUNT(*) AS n FROM \`${t}\``);
    res.json({ ok: true, collection: COLLECTION, table: t, docs: Number(rows[0].n || 0), dimension: DIM });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

app.post('/api/docs', async (req, res) => {
  try {
    const { docs = [], embeddings = [] } = req.body || {};
    if (!Array.isArray(docs) || docs.length !== embeddings.length) {
      return res.status(400).json({ ok: false, error: 'docs and embeddings must have equal length' });
    }
    for (const v of embeddings) {
      if (!Array.isArray(v) || v.length !== DIM) {
        return res.status(400).json({ ok: false, error: `embedding dim must be ${DIM}` });
      }
    }
    if (!docs.length) return res.json({ ok: true, added: 0 });
    // The collection already exists in this deployment; touching the SDK
    // collection object would try to resolve the (uninstalled) default-embed.
    const t = await resolveTable();
    // seekdb-js `collection.add()` insists on a registered embedding function,
    // so vectors are inserted through the same SDK's SQL entry point instead.
    const values = docs.map((d, i) => {
      const meta = JSON.stringify({ title: String(d.title || '').slice(0, 200) });
      return `('${sqlQuote(d.id)}', '${sqlQuote(d.text)}', '${vecLiteral(embeddings[i])}', '${sqlQuote(meta)}')`;
    }).join(',');
    await client.execute(
      `INSERT INTO \`${t}\` (_id, document, embedding, metadata) VALUES ${values}`
    );
    res.json({ ok: true, added: docs.length });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

app.post('/api/search', async (req, res) => {
  try {
    const { query = '', embedding, k = 5 } = req.body || {};
    if (!Array.isArray(embedding) || embedding.length !== DIM) {
      return res.status(400).json({ ok: false, error: `embedding dim must be ${DIM}` });
    }
    const limit = Math.min(Math.max(Number(k) || 5, 1), 20);
    const t = await resolveTable();
    const vec = vecLiteral(embedding);
    const q = sqlQuote(query);
    // One statement, ranking produced by seekdb: cosine distance + BM25-ish full-text score.
    const sql = `
      SELECT _id, document, metadata,
             COSINE_DISTANCE(embedding, '${vec}') AS dist,
             MATCH(document) AGAINST ('${q}') AS ft
      FROM \`${t}\`
      ORDER BY (0.7 * COSINE_DISTANCE(embedding, '${vec}')
                + 0.3 * (1 - LEAST(MATCH(document) AGAINST ('${q}'), 1))) ASC
      LIMIT ${limit}`;
    const t0 = performance.now();
    const rows = await client.execute(sql);
    const ms = performance.now() - t0;
    res.json({
      ok: true,
      ms: Number(ms.toFixed(2)),
      results: rows.map((r) => ({
        id: idText(r._id),
        text: r.document,
        metadata: typeof r.metadata === 'string' ? JSON.parse(r.metadata) : r.metadata,
        distance: Number(r.dist),
        fulltextScore: Number(r.ft),
      })),
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

app.post('/api/reset', async (_req, res) => {
  try {
    const t = await resolveTable();
    await client.execute(`DELETE FROM \`${t}\``);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.message });
  }
});

app.listen(PORT, () => console.log(`stepfun mini-kb listening on http://0.0.0.0:${PORT}`));
