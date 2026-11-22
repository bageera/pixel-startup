# Pixel Startup — build, pull & run (python backend fronts the office; node behind it)
IMAGE   := pixel-startup
BACKEND_IMAGE := ghcr.io/bageera/pixel-startup-backend
PORT    ?= 19000
HERMES_HOME ?= $(HOME)/.hermes
ENVFILE := $(wildcard .env)
VERSION := $(shell node -p "require('./package.json').version")

.DEFAULT_GOAL := help
.PHONY: help install test build push-image pull start stop restart run logs clean tag contracts python-setup python-test

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-10s %s\n", $$1, $$2}'

install: ## Install npm dependencies
	npm install

test: ## Run integration/presence self-checks
	npm test

build: ## Build the local backend image (python tier; CI publishes GHCR on push)
	docker build -f Dockerfile.python -t $(IMAGE):latest -t $(BACKEND_IMAGE):local .

push-image: ## Push the locally built backend image to GHCR (pull side is public, no login)
	docker tag $(IMAGE):latest $(BACKEND_IMAGE):latest
	docker push $(BACKEND_IMAGE):latest

pull: ## Pull the CI-published backend image (public package, no docker login)
	docker pull $(BACKEND_IMAGE):latest

start: ## Local dev: build (cached) from source + run (office on $(PORT)), Hermes presence mounts
ifeq ($(ENVFILE),)
	@echo "no .env found: DASHBOARD_PASSWORD/PIXEL_SESSION_SECRET/PIXEL_API_TOKEN must come from the environment"
endif
	@grep -q "^DASHBOARD_PASSWORD=" .env 2>/dev/null || echo "note: without DASHBOARD_PASSWORD the backend refuses to boot"
	docker network inspect pixelnet >/dev/null 2>&1 || docker network create pixelnet
	@$(MAKE) minio >/dev/null 2>&1 || echo "minio sidecar not started (run `make minio` to see why)"
	docker build -q -f Dockerfile.python -t $(IMAGE):latest .
	-docker stop $(IMAGE)
	-docker rm $(IMAGE)
	docker run -d --name $(IMAGE) \
	  --user "$(shell id -u):$(shell id -g)" \
	  --network pixelnet \
	  -p $(PORT):19000 \
	  --pid=host \
	  $(if $(ENVFILE),--env-file .env) \
	  -v $(CURDIR)/data:/app/data \
	  -v $(HERMES_HOME):/hermes-home:ro -e HERMES_HOME=/hermes-home \
	  -e PIXEL_HERMES_LIVENESS=file \
	  $(IMAGE):latest

stop: ## Stop and remove the running stack
	-docker stop $(IMAGE)
	-docker rm $(IMAGE)
	-docker stop pixel-minio
	-docker rm pixel-minio

restart: ## stop + start (rebuilds from current source, cached)
	$(MAKE) stop
	$(MAKE) start

minio: ## Local-dev object storage (access-log bucket); creds from .env MINIO_*
	docker network inspect pixelnet >/dev/null 2>&1 || docker network create pixelnet
	docker rm -f pixel-minio 2>/dev/null || true
	docker run -d --name pixel-minio --network pixelnet \
	  -p 9000:9000 -p 9001:9001 \
	  -e MINIO_ROOT_USER=$$(grep '^MINIO_ACCESS_KEY=' .env | cut -d= -f2-) \
	  -e MINIO_ROOT_PASSWORD=$$(grep '^MINIO_SECRET_KEY=' .env | cut -d= -f2-) \
	  -v pixel-minio-data:/bitnami/minio/data \
	  -e MINIO_SERVER_CONSOLE_ADDRESS=:9001 \
	  bitnamilegacy/minio:latest
	docker run --rm --network pixelnet --env-file .env --entrypoint sh bitnamilegacy/minio-client:latest \
	  -ec 'until mc alias set local http://pixel-minio:9000 "$$MINIO_ACCESS_KEY" "$$MINIO_SECRET_KEY" >/dev/null 2>&1; do sleep 2; done; mc mb "local/$$MINIO_BUCKET" >/dev/null 2>&1 || true; mc ls "local/$$MINIO_BUCKET" >/dev/null && echo bucket ready'

logs: ## Tail stack logs
	docker logs -f $(IMAGE)

run: ## Back-compat alias for start
	$(MAKE) start

tag: ## Tag the current commit as annotated v$(VERSION) (SemVer, from package.json) and push the tag
	git tag -a v$(VERSION) -m "v$(VERSION)"
	git push origin v$(VERSION)

contracts: ## Validate G3 language-neutral contracts (node + python), fixture parity
	node tests/contracts/validate_contracts.js
	python3 tests/contracts/validate_contracts.py

python-setup: ## Sync the Python backend environment (uv; creates .venv + uv.lock)
	uv sync

python-test: ## Python backend gate: pytest, ruff lint + format, pyright, pip-audit
	uv run pytest
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run pyright src tests
	uv run pip-audit

clean: ## Stop the container and remove the image
	-docker stop $(IMAGE)
	-docker rmi $(IMAGE):latest
