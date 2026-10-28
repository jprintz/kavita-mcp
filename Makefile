.PHONY: help test lint integration throwaway-up throwaway-down

help: ## Show the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "%-16s %s\n", $$1, $$2}'

test: ## Unit tests (no Kavita needed)
	uv run pytest

lint: ## Ruff
	uv run ruff check .

integration: ## Throwaway Kavita + File Browser, the image, and the integration tests
	scripts/throwaway.sh up
	scripts/throwaway.sh mcp
	uv run pytest -m integration

throwaway-up: ## Start the throwaway Kavita + File Browser (writes .throwaway/env)
	scripts/throwaway.sh up

throwaway-down: ## Remove the throwaway containers, network and volumes
	scripts/throwaway.sh down
