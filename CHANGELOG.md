# Changelog

## [2.1.0] - 2026-03-02

### Added
- **Multimodal Support**: GPT-4V image understanding + DALL-E 3 generation
  - Auto-analyze photos sent to bot
  - `/generate` command for image creation
  - `/transform` command for style transfer
  - REST API endpoints for programmatic access
- New service: `services/multimodal.py`
- Telegram handlers: `telegram/multimodal_handler.py`
- Comprehensive test suite: `tests/test_multimodal.py`

### Features
- Image understanding with custom prompts
- High-quality image generation (1024x1024, 1792x1024, 1024x1792)
- Image-to-image transformation pipeline
- Token usage tracking for vision API
- Error handling and logging

### API
- `POST /api/multimodal/understand` - Analyze images
- `POST /api/multimodal/generate` - Create images
- `POST /api/multimodal/transform` - Transform image styles

---

# Changelog

## v6.0.0 (2026-03-01)

### 💬 User Feedback Service (`services/feedback_service.py`)
- Thumbs up/down rating with comment and metadata
- 1-5 star ratings with per-message tracking
- Correction submissions (user provides better answer for fine-tuning)
- Quality metrics aggregation: satisfaction rate, avg rating, model scores
- Per-model quality comparison dashboard
- Common issue detection from negative feedback
- Improvement suggestions engine (auto-analyze and recommend)
- Export: JSON + CSV with model/time filters
- SQLite persistence with indexed queries

### 🔧 Function Calling Registry (`services/function_registry.py`)
- Register custom functions with JSON Schema parameter definitions
- Auto-generate OpenAI function calling schema
- Auto-generate Claude tool_use schema
- Function dispatch with parameter validation (type checking + enum)
- Async function support (auto-detect coroutines)
- Function categories and permission-based access control
- Per-function rate limiting (token bucket, per-minute)
- Execution logging and metrics (call count, latency, success rate)
- OpenAI tool_call dispatch: parse + validate + execute + format response
- Claude tool_use dispatch: parse + validate + execute + format result
- Built-in functions: get_current_time, calculator, string_tools, json_format
- Enable/disable individual functions at runtime

### 🧵 Conversation Threading (`services/conversation_threading.py`)
- Create conversation threads with topic branching
- Thread-scoped context isolation (system prompt + summary + variables)
- Thread state management: active → paused → archived/closed
- Add messages to threads with role tracking
- Thread search by title/topic (case-insensitive)
- Child thread support (branch from parent)
- Thread merge: combine related threads (move messages + archive source)
- Pin/unpin messages within thread context
- Thread tagging, labeling, and title updates
- Thread statistics: per-conversation, per-user, state breakdown
- SQLite persistence with full indexing

### 🧪 Tests
- 146 new tests (3 test files): feedback_service (56) + function_registry (55) + conversation_threading (35)
- Total: 1347 tests, all passing

## v5.0.0 (2026-02-28)

### 🔧 Bug Fix
- Fixed FTS5 rebuild_index() corruption on external content tables (16 test failures → 0)
- Use FTS5 built-in 'rebuild' command instead of manual DELETE+INSERT

### 📱 Telegram Handler Module (`telegram/handler.py`)
- Modular TelegramHandler with command/text/callback/media routing
- InlineKeyboardBuilder: fluent API for inline keyboard construction
- MessageFormatter: Markdown V2 + HTML escape, bold/italic/code/pre/link helpers
- Message chunking for 4096-char limit (smart split at newline/space)
- Group chat support: mention detection, reply-only mode, @mention stripping
- Per-user rate limiting (token bucket algorithm)
- Media message handler (photo/document/voice/video/audio)
- Typing action indicator
- TelegramUser + CallbackQuery data models

### 💾 Response Cache Service (`services/response_cache.py`)
- SQLite-backed cache with configurable TTL
- Exact hash match + cosine word-overlap similarity fallback
- Cache hit/miss/eviction statistics (daily aggregation)
- Per-user cache invalidation
- Auto-eviction when over max_entries (LRU by hit count)
- Query normalization (case-insensitive, trim whitespace)
- Tokens-saved tracking for cost analysis

### 📊 Usage Quota Service (`services/usage_quota.py`)
- Per-user daily/monthly token quotas with automatic period reset
- 4-tier system: Free(1K/day) → Basic(10K) → Premium(100K) → Unlimited
- Quota check before API call + consumption recording
- Admin bypass capability
- Usage history with engine/action tracking
- Top users leaderboard
- Formatted status message (Chinese UI)

### 💿 Backup Service (`services/backup.py`)
- Export conversations to JSON, Markdown, HTML formats
- Per-user and per-channel filtering
- Date range filtering (since/until timestamps)
- Restore from JSON backup with overwrite option
- Backup manifest with checksums and metadata
- HTML export with XSS-safe escaping and styled layout
- Markdown export with date grouping and role emojis
- List available backups in directory

### 📈 Tests
- From 451 → 530 tests (+79 new tests, 4 new test files)
- All 530 tests passing (was 435/451 before, now 530/530)
- test_telegram_handler.py (25 tests)
- test_response_cache.py (18 tests)
- test_usage_quota.py (17 tests)
- test_backup.py (15 tests)
- Fixed 16 conversation_search test errors

## v4.0.0 (2026-02-28)

### 📬 Async Message Queue
- Priority-based message queue (`services/message_queue.py`)
- 5 priority levels: CRITICAL > HIGH > NORMAL > LOW > BULK
- Automatic retry with exponential backoff
- Dead Letter Queue (DLQ) with SQLite persistence
- Configurable max workers for concurrent processing
- Back-pressure protection (queue capacity limit)
- DLQ replay & purge operations

### 🛡️ Auto Moderation Engine
- Content filter with blacklist words + regex patterns (`services/auto_moderation.py`)
- 6 built-in spam detection patterns (short links, TG invites, ads)
- Flood detection (rate + duplicate message hashing)
- Progressive enforcement: ALLOW → WARN → BLOCK → MUTE
- Auto-mute after configurable violation threshold
- Per-user moderation state tracking
- Admin whitelist bypass

### 🌐 Multi-language i18n
- 4-language support: zh/en/ja/ko (`services/i18n.py`)
- 35 translation keys per language (UI messages, errors, commands)
- Automatic language detection (CJK/Hangul/Latin analysis)
- User language preference persistence
- Variable interpolation with `{name}` syntax
- Fallback chain: target → locale fallback → English → key
- Custom translation loading from JSON files

### 📊 Analytics Dashboard
- Real-time metrics collector (`services/analytics_dashboard.py`)
- Daily reports: messages, tokens, users, latency, errors
- User profiling: engagement score, session analysis, active hours
- Hour×Day interaction heatmap
- User retention calculation (Day 1/3/7)
- Channel & engine usage breakdown
- Report export: Text / JSON / CSV

### 📈 Test Coverage
- 132 new tests (message_queue: 32, auto_moderation: 42, i18n: 32, analytics: 26)
- Total: 319 → 451 tests

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
