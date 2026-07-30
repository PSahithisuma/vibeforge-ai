#!/usr/bin/env bash
# =============================================================================
# 00_create_dbs.sh — Create sibling databases for Keycloak, Langfuse, LiteLLM.
#
# Docker's postgres entrypoint runs every file in /docker-entrypoint-initdb.d/
# in lexicographic order on first boot only. .sh files are executed as shell
# scripts; .sql files are piped to psql. CREATE DATABASE cannot run inside a
# transaction block, so this must be a shell script.
# =============================================================================
set -euo pipefail

echo "[init] Creating sibling databases: keycloak, langfuse, litellm"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE keycloak'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec

    SELECT 'CREATE DATABASE langfuse'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec

    SELECT 'CREATE DATABASE litellm'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'litellm')\gexec

    GRANT ALL PRIVILEGES ON DATABASE keycloak TO "$POSTGRES_USER";
    GRANT ALL PRIVILEGES ON DATABASE langfuse TO "$POSTGRES_USER";
    GRANT ALL PRIVILEGES ON DATABASE litellm TO "$POSTGRES_USER";
EOSQL

echo "[init] Sibling databases ready (keycloak, langfuse, litellm)."
