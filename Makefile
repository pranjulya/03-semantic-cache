.PHONY: help setup test demo serve report
help:  ## list shortcuts
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'
setup: ## one-time setup (needs network)
	scripts/setup.sh
test:  ## run the offline test suite
	uv run pytest -q -m "not smoke"
demo:  ## scripted walk-through (needs local Ollama)
	uv run python -m scache.cli demo
serve: ## start the API on localhost:8000
	uv run uvicorn scache.api:app
report: ## rebuild report/ from saved results
	uv run python -m scache.cli report
