# Changelog

## v3.0.0 (2026-02-28)

### 🔌 Plugin System
- Hot-loadable plugin architecture (`plugins/base.py`, `plugins/loader.py`)
- Plugin chain with priority-based dispatch
- Built-in plugins: Weather (`/weather`), Translate (`/tr`), Image Generation (`/img`)
- Dynamic load/unload/reload from directory
- Command routing to plugins

### 🛡️ Rate Limiting
- Token bucket rate limiter (`middleware/`)
- Per-user + global RPM limits
- Whitelist bypass for VIP users
- Auto cleanup of inactive buckets

### 📤 Conversation Export
- Export chat history in JSON/Markdown/CSV (`services/`)
- Configurable system message inclusion
- `/export` command support

### 🏥 Health Monitoring
- System health checker (`services/health.py`)
- Engine connectivity, memory, runtime checks
- Summary and detailed health endpoints

### 🎬 Streaming Support
- SSE streaming response mixin (`engines/streaming.py`)
- Chunked delivery with SSE format output
- Stream collection utility

### 🖥️ Admin Dashboard
- FastAPI admin panel (`admin/`)
- HTML dashboard with engine stats, plugin management
- JWT/token authentication
- Plugin toggle/reload API endpoints
- REST API: `/admin/api/status`, `/admin/api/health`

### 📊 Testing
- 233 total tests (73 new)
- New test suites: plugins, rate_limit, export, health, streaming, admin

---

## v2.0.0 (2026-02-27)

### Features
- Dual engine architecture (OpenAI + Claude)
- Automatic failover with retry
- Context management with auto-summarization
- TF-IDF RAG knowledge base
- WeChat webhook integration
- FastAPI REST API
- Docker Compose (3-service stack)
- CI/CD with GitHub Actions
- 160 tests
