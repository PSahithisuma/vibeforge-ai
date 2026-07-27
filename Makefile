# =============================================================================
# VibeForge — Makefile
# Convenience targets for local development.
# Assumes: Docker + Docker Compose v2 installed.
# =============================================================================

COMPOSE     := docker compose
ENV_FILE    := .env
PROJECT     := vibeforge

# Load .env for use in make targets (optional, compose reads it automatically)
ifneq (,$(wildcard $(ENV_FILE)))
    include $(ENV_FILE)
    export
endif

.DEFAULT_GOAL := help

.PHONY: help up down down-v restart logs ps health \
        init-minio seed db-shell redis-shell qdrant-shell \
        pg-tables pg-rls-test migrate-check clean

# -----------------------------------------------------------------------------
# HELP
# -----------------------------------------------------------------------------
help: ## Show this help message
	@echo ""
	@echo "  VibeForge — Local Dev Makefile"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

# -----------------------------------------------------------------------------
# LIFECYCLE
# -----------------------------------------------------------------------------
up: ## Start all infra services (detached)
	@echo "→ Starting VibeForge infra..."
	$(COMPOSE) up -d
	@echo "→ Waiting for services to become healthy..."
	@sleep 5
	@$(MAKE) health

down: ## Stop all services (preserve volumes)
	$(COMPOSE) down

down-v: ## ⚠️  Stop all services AND delete all volumes (destructive!)
	@echo "WARNING: This will delete all persistent data (Postgres, Redis, Qdrant, MinIO)."
	@read -p "Type 'yes' to confirm: " confirm && [ "$$confirm" = "yes" ]
	$(COMPOSE) down -v

restart: ## Restart all services
	$(COMPOSE) restart

# -----------------------------------------------------------------------------
# OBSERVABILITY
# -----------------------------------------------------------------------------
logs: ## Tail logs for all services (Ctrl-C to exit)
	$(COMPOSE) logs -f

logs-%: ## Tail logs for a specific service, e.g. make logs-postgres
	$(COMPOSE) logs -f $*

ps: ## Show service status
	$(COMPOSE) ps

health: ## Print health status of all running containers
	@echo ""
	@echo "  Container Health:"
	@docker ps --filter "name=vibeforge-" \
		--format "  {{.Names}}\t{{.Status}}" | column -t
	@echo ""
	@echo "  Service URLs:"
	@echo "  Keycloak Admin  → http://localhost:8080   (admin / $${KEYCLOAK_ADMIN_PASSWORD:-admin})"
	@echo "  Grafana         → http://localhost:3000   (admin / $${GRAFANA_ADMIN_PASSWORD:-admin})"
	@echo "  Prometheus      → http://localhost:9090"
	@echo "  MinIO Console   → http://localhost:9001   ($${MINIO_ROOT_USER:-vibeforge} / $${MINIO_ROOT_PASSWORD:-vibeforge_minio_secret})"
	@echo "  Langfuse        → http://localhost:3001"
	@echo "  Qdrant REST     → http://localhost:6333/dashboard"
	@echo ""

# -----------------------------------------------------------------------------
# INITIALISATION
# -----------------------------------------------------------------------------
init-minio: ## Create MinIO buckets (run after first `make up`)
	$(COMPOSE) run --rm minio-init

env: ## Copy .env.example → .env if .env doesn't exist
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "→ Created .env from .env.example. Fill in secrets before running."; \
	else \
		echo "→ .env already exists, skipping."; \
	fi

# -----------------------------------------------------------------------------
# DATABASE
# -----------------------------------------------------------------------------
db-shell: ## Open a psql shell in the vibeforge DB
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-vibeforge} -d $${POSTGRES_DB:-vibeforge}

pg-tables: ## List all tables in the vibeforge DB
	$(COMPOSE) exec postgres psql \
		-U $${POSTGRES_USER:-vibeforge} \
		-d $${POSTGRES_DB:-vibeforge} \
		-c "\dt public.*"

pg-rls-test: ## Run RLS sanity check — verifies cross-tenant block
	@echo "→ Running RLS sanity check..."
	$(COMPOSE) exec postgres psql \
		-U $${POSTGRES_USER:-vibeforge} \
		-d $${POSTGRES_DB:-vibeforge} \
		-c "SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000099'; SET ROLE vibeforge_app; SELECT count(*) AS cross_tenant_rows FROM jobs;" \
		-c "RESET ROLE;"
	@echo "→ Expected: cross_tenant_rows = 0 (RLS working correctly)"

pg-schema: ## Show full schema info (tables, RLS policies, indexes)
	$(COMPOSE) exec postgres psql \
		-U $${POSTGRES_USER:-vibeforge} \
		-d $${POSTGRES_DB:-vibeforge} \
		-c "SELECT schemaname, tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;" \
		-c "SELECT tablename, policyname, cmd, qual FROM pg_policies WHERE schemaname = 'public' ORDER BY tablename, policyname;"

seed: ## Insert seed dev tenant + budget (idempotent — safe to re-run)
	$(COMPOSE) exec postgres psql \
		-U $${POSTGRES_USER:-vibeforge} \
		-d $${POSTGRES_DB:-vibeforge} \
		-c "INSERT INTO tenants (id, name, slug, tier) VALUES ('00000000-0000-0000-0000-000000000001', 'Dev Tenant', 'dev-tenant', 'starter') ON CONFLICT (id) DO NOTHING;" \
		-c "INSERT INTO budgets (tenant_id, budget_usd, spent_usd, period_start, period_end) VALUES ('00000000-0000-0000-0000-000000000001', 100.00, 0, date_trunc('month', CURRENT_DATE)::date, (date_trunc('month', CURRENT_DATE) + interval '1 month - 1 day')::date) ON CONFLICT (tenant_id, period_start) DO NOTHING;"
	@echo "→ Seed data applied."

migrate-check: ## Check Alembic migration status (requires api service)
	$(COMPOSE) run --rm api alembic current

# -----------------------------------------------------------------------------
# OTHER SHELLS
# -----------------------------------------------------------------------------
redis-shell: ## Open redis-cli
	$(COMPOSE) exec redis redis-cli -a $${REDIS_PASSWORD:-redis_dev_secret}

qdrant-shell: ## Show Qdrant collections
	curl -s http://localhost:6333/collections | python3 -m json.tool

keycloak-token: ## Get a dev JWT (direct grant for admin@vibeforge.local)
	@curl -s -X POST \
		"http://localhost:8080/realms/vibeforge/protocol/openid-connect/token" \
		-H "Content-Type: application/x-www-form-urlencoded" \
		-d "client_id=$${KEYCLOAK_CLIENT_ID:-vibeforge-api}" \
		-d "client_secret=$${KEYCLOAK_CLIENT_SECRET:-vibeforge-api-secret-change-me}" \
		-d "username=admin@vibeforge.local" \
		-d "password=admin123" \
		-d "grant_type=password" \
		| python3 -m json.tool

# -----------------------------------------------------------------------------
# CLEANUP
# -----------------------------------------------------------------------------
clean: ## Remove stopped containers and dangling images
	docker container prune -f
	docker image prune -f
