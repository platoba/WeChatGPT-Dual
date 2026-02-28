.PHONY: install dev test lint clean docker run webhook help

help:  ## Show help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install production deps
	pip install -e .

dev:  ## Install dev deps
	pip install -e ".[dev]"

test:  ## Run tests
	pytest tests/ -v --tb=short

coverage:  ## Run tests with coverage
	pytest tests/ --cov=. --cov-report=term-missing --cov-report=html

lint:  ## Run linter
	ruff check . --exclude=.git

fix:  ## Auto-fix lint issues
	ruff check . --exclude=.git --fix

run:  ## Run Telegram bot
	python bot.py

webhook:  ## Run webhook API server
	uvicorn webhook:app --host 0.0.0.0 --port 8900 --reload

docker:  ## Build Docker image
	docker build -t wechatgpt-dual .

up:  ## Start all services (Docker Compose)
	docker compose up -d

down:  ## Stop all services
	docker compose down

logs:  ## View Docker logs
	docker compose logs -f

clean:  ## Clean build artifacts
	rm -rf __pycache__ .pytest_cache .ruff_cache htmlcov .coverage
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
