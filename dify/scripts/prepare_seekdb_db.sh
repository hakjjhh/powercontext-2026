#!/usr/bin/env bash
# Create an isolated schema for PowerContext inside an existing seekdb / OceanBase
# MySQL-mode instance and record before/after SHOW DATABASES as evidence.
#
# Required environment:
#   SEEKDB_MYSQL_HOST, SEEKDB_MYSQL_PORT, SEEKDB_MYSQL_USER  (admin account)
#   POWERCONTEXT_DATABASE_NAME, POWERCONTEXT_DB_USER, POWERCONTEXT_DB_PASSWORD
# Optional:
#   SEEKDB_MYSQL_PASSWORD, EVIDENCE_DIR (default: ./seekdb-evidence)
set -euo pipefail

: "${SEEKDB_MYSQL_HOST:?set SEEKDB_MYSQL_HOST}"
: "${SEEKDB_MYSQL_PORT:?set SEEKDB_MYSQL_PORT}"
: "${SEEKDB_MYSQL_USER:?set SEEKDB_MYSQL_USER}"
: "${POWERCONTEXT_DATABASE_NAME:?set POWERCONTEXT_DATABASE_NAME}"
: "${POWERCONTEXT_DB_USER:?set POWERCONTEXT_DB_USER}"
: "${POWERCONTEXT_DB_PASSWORD:?set POWERCONTEXT_DB_PASSWORD}"

EVIDENCE_DIR="${EVIDENCE_DIR:-./seekdb-evidence}"
MYSQL=(mysql -h "$SEEKDB_MYSQL_HOST" -P "$SEEKDB_MYSQL_PORT" -u "$SEEKDB_MYSQL_USER")
if [ -n "${SEEKDB_MYSQL_PASSWORD:-}" ]; then
  MYSQL+=("-p${SEEKDB_MYSQL_PASSWORD}")
fi
mkdir -p "$EVIDENCE_DIR"

{
  echo "# SHOW DATABASES before provisioning"
  date -Is
  "${MYSQL[@]}" -e 'SHOW DATABASES;'
  echo
  echo '# version / compatibility mode'
  "${MYSQL[@]}" -e 'SELECT @@version;'
  "${MYSQL[@]}" -e 'SHOW VARIABLES LIKE "ob_compatibility_mode";'
} | tee "$EVIDENCE_DIR/00-before.txt"

"${MYSQL[@]}" -e "CREATE DATABASE IF NOT EXISTS ${POWERCONTEXT_DATABASE_NAME};"
"${MYSQL[@]}" -e "CREATE USER IF NOT EXISTS '${POWERCONTEXT_DB_USER}' IDENTIFIED BY '${POWERCONTEXT_DB_PASSWORD}';"
"${MYSQL[@]}" -e "GRANT ALL PRIVILEGES ON ${POWERCONTEXT_DATABASE_NAME}.* TO '${POWERCONTEXT_DB_USER}';"

{
  echo "# SHOW DATABASES after provisioning ${POWERCONTEXT_DATABASE_NAME}"
  date -Is
  "${MYSQL[@]}" -e 'SHOW DATABASES;'
  echo
  echo "# grants for ${POWERCONTEXT_DB_USER} (password not shown)"
  "${MYSQL[@]}" -e "SHOW GRANTS FOR '${POWERCONTEXT_DB_USER}';"
} | tee "$EVIDENCE_DIR/01-after.txt"

echo "database=${POWERCONTEXT_DATABASE_NAME} user=${POWERCONTEXT_DB_USER}"
