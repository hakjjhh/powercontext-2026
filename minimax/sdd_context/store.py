"""seekdb storage for engineering artifacts (specs, code, tests)."""

import os
import pathlib

import pymysql


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sdd_artifacts (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  kind VARCHAR(16) NOT NULL,
  path VARCHAR(512) NOT NULL,
  content MEDIUMTEXT NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FULLTEXT KEY ft_artifacts (path, content) WITH PARSER ik PARSER_PROPERTIES=(ik_mode="smart"),
  UNIQUE KEY uk_path (path)
) DEFAULT CHARSET = utf8mb4
"""


def connect():
    return pymysql.connect(
        host=os.getenv("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.getenv("SEEKDB_PORT", "10000")),
        user=os.getenv("SEEKDB_USER", "root"),
        password=os.getenv("SEEKDB_PASSWORD", ""),
        database=os.getenv("SEEKDB_DATABASE", "minimax_sdd"),
        charset="utf8mb4",
        autocommit=True,
    )


def ensure_schema():
    db = os.getenv("SEEKDB_DATABASE", "minimax_sdd")
    conn = pymysql.connect(
        host=os.getenv("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.getenv("SEEKDB_PORT", "10000")),
        user=os.getenv("SEEKDB_USER", "root"),
        password=os.getenv("SEEKDB_PASSWORD", ""),
        charset="utf8mb4",
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}`")
        cur.execute(f"USE `{db}`")
        cur.execute(TABLE_SQL)
    conn.close()


def _kind_for(path: pathlib.Path) -> str:
    parts = {p.lower() for p in path.parts}
    if "tests" in parts or path.name.startswith("test_"):
        return "test"
    if "spec" in parts or path.suffix.lower() in {".md", ".txt"}:
        return "spec"
    return "code"


def index_dir(root: str):
    root_path = pathlib.Path(root).resolve()
    rows = []
    for sub in ("spec", "src", "tests"):
        base = root_path / sub
        if not base.exists():
            continue
        for file in sorted(base.rglob("*")):
            if not file.is_file() or file.suffix not in {".md", ".py", ".txt"}:
                continue
            rows.append(
                (
                    _kind_for(file),
                    str(file.relative_to(root_path)),
                    file.read_text(encoding="utf-8"),
                )
            )
    conn = connect()
    with conn.cursor() as cur:
        for kind, rel, content in rows:
            cur.execute(
                """
                INSERT INTO sdd_artifacts (kind, path, content) VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE kind=VALUES(kind), content=VALUES(content)
                """,
                (kind, rel, content),
            )
    conn.close()
    return rows


def search(query: str, limit: int = 8, kinds=None):
    conn = connect()
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        params = [query, query]
        sql = (
            "SELECT kind, path, content, "
            "MATCH(path, content) AGAINST (%s) AS score "
            "FROM sdd_artifacts "
            "WHERE MATCH(path, content) AGAINST (%s)"
        )
        if kinds:
            sql += " AND kind IN (" + ",".join(["%s"] * len(kinds)) + ")"
            params.extend(kinds)
        sql += " ORDER BY score DESC LIMIT %s"
        params.append(limit)
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            like = f"%{query}%"
            cur.execute(
                "SELECT kind, path, content, 0 AS score FROM sdd_artifacts "
                "WHERE content LIKE %s OR path LIKE %s ORDER BY path LIMIT %s",
                (like, like, limit),
            )
            rows = cur.fetchall()
    conn.close()
    return rows
