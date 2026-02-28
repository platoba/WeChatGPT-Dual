# Changelog

## [3.0.0] - 2026-02-28

### Added
- **Webhook API** (FastAPI): `/webhook/wechat` + `/api/chat` + `/health` + `/stats` + engine management endpoints
- **Database layer** (SQLite): Message logging, daily usage tracking, user management (block/unblock)
- **Test suite**: 85+ tests across 10 test files covering all modules
- **Docker Compose**: Bot + Webhook API + Redis (3-service stack)
- **CI/CD**: GitHub Actions with lint + test + coverage + Docker build (Python 3.10-3.13)
- **pyproject.toml**: Proper Python packaging with optional deps groups
- **Makefile**: 12 targets (install, dev, test, coverage, lint, run, webhook, docker, up, down, logs, clean)
- **.env.example**: All 25+ environment variables documented

### Improved
- Full Dockerfile with health check
- Documentation with API reference, deployment guide, architecture diagram

## [2.0.0] - 2026-02-27

### Added
- Dual AI engine architecture (OpenAI + Claude)
- Automatic failover with rate limit detection
- Context manager with auto-summarization
- Knowledge base with TF-IDF RAG retrieval
- WeChat webhook handler with group @mention support
- 8 management commands (/status /switch /clear /usage /model /role /kb /help)
- Document loader (TXT, MD, JSON, CSV)
- 18 modular source files

## [1.0.0] - 2026-02-27

### Added
- Initial release
- Basic Telegram bot with single AI engine
