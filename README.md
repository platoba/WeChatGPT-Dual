# WeChatGPT-Dual

[![CI](https://github.com/platoba/WeChatGPT-Dual/actions/workflows/ci.yml/badge.svg)](https://github.com/platoba/WeChatGPT-Dual/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

🤖 **Dual-engine AI chatbot** — Telegram + WeChat with auto failover, TF-IDF RAG knowledge base, webhook API, and SQLite analytics.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Telegram    │     │  WeChat     │     │  REST API   │
│  Long Poll   │     │  Webhook    │     │  /api/chat  │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                    │
       └───────────┬───────┴────────────────────┘
                   ▼
           ┌──────────────┐
           │ Engine       │──→ Primary (OpenAI/Claude)
           │ Manager      │──→ Secondary (auto failover)
           └──────┬───────┘
                  │
        ┌─────────┼──────────┐
        ▼         ▼          ▼
   ┌─────────┐ ┌──────┐ ┌────────┐
   │ Context │ │  KB  │ │  DB    │
   │ Manager │ │ RAG  │ │ SQLite │
   └─────────┘ └──────┘ └────────┘
```

## Features

| Feature | Description |
|---------|-------------|
| 🧠 **Dual Engine** | OpenAI + Claude with automatic failover |
| 💬 **Multi-Platform** | Telegram Bot + WeChat webhook + REST API |
| 📚 **Knowledge Base** | TF-IDF RAG retrieval (no external deps) |
| 🔄 **Auto Summarize** | Context window control with AI summarization |
| 📊 **Analytics** | SQLite message logging + daily usage tracking |
| 👤 **User Management** | Block/unblock, activity tracking |
| 🐳 **Docker** | 3-service stack (Bot + API + Redis) |
| ⚡ **Webhook API** | FastAPI endpoints for custom integrations |
| 🛡️ **Rate Limiting** | Per-engine rate limit detection + cooldown |

## Quick Start

```bash
git clone https://github.com/platoba/WeChatGPT-Dual.git
cd WeChatGPT-Dual

# Setup
cp .env.example .env
# Edit .env with your keys

# Install & run
pip install -e ".[dev]"
python bot.py
```

### Docker

```bash
docker compose up -d
```

Services:
- **bot** — Telegram long-polling bot
- **webhook** — FastAPI webhook server (port 8900)
- **redis** — Redis for future caching/queues

## API Reference

### Webhook (WeChat)
```bash
curl -X POST http://localhost:8900/webhook/wechat \
  -H "Content-Type: application/json" \
  -d '{"sender_id": "user1", "content": "Hello", "msg_type": "text"}'
```

### Direct Chat
```bash
curl -X POST http://localhost:8900/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is Python?", "user_id": "test"}'
```

### Engine Management
```bash
# List engines
curl http://localhost:8900/api/engines

# Switch primary
curl -X POST http://localhost:8900/api/engine/switch/claude

# Health check
curl http://localhost:8900/health
```

## Bot Commands

| Command | Description |
|---------|-------------|
| `/status` | System status (engines, context, KB) |
| `/switch <engine>` | Switch primary engine |
| `/clear` | Clear conversation history |
| `/usage` | Usage statistics |
| `/model` | View current model |
| `/role <desc>` | Set AI role/persona |
| `/kb <query>` | Search knowledge base |
| `/help` | Show all commands |

## Configuration

All settings via environment variables (see [.env.example](.env.example)):

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | — | Telegram bot token |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `CLAUDE_API_KEY` | — | Anthropic Claude API key |
| `PRIMARY_ENGINE` | `openai` | Primary engine (`openai`/`claude`) |
| `FAILOVER_ENABLED` | `true` | Auto failover on error |
| `SYSTEM_PROMPT` | Default | System prompt for AI |
| `MAX_HISTORY` | `20` | Max conversation history |
| `SUMMARY_THRESHOLD` | `16` | Trigger auto-summary at N messages |
| `WEBHOOK_TOKEN` | — | Webhook authentication token |
| `DB_PATH` | `wechatgpt.db` | SQLite database path |

## Development

```bash
# Install dev deps
make dev

# Run tests
make test

# Coverage report
make coverage

# Lint
make lint

# Auto-fix
make fix
```

## Project Structure

```
├── bot.py                  # Telegram bot (entry point)
├── webhook.py              # FastAPI webhook API server
├── database.py             # SQLite persistence layer
├── config.py               # Configuration from env
├── engines/
│   ├── base.py             # Engine ABC + stats + response model
│   ├── engine_manager.py   # Dual engine + failover logic
│   ├── openai_engine.py    # OpenAI API client
│   └── claude_engine.py    # Claude API client
├── context/
│   ├── manager.py          # Per-user context + auto-summary
│   └── summarizer.py       # AI/simple summarization
├── knowledge/
│   ├── store.py            # TF-IDF RAG knowledge base
│   └── loader.py           # Document loader (TXT/MD/JSON/CSV)
├── commands/
│   └── handler.py          # Bot command router
├── wechat/
│   ├── handler.py          # WeChat message handler
│   └── message.py          # WeChat message model
├── tests/                  # 85+ tests (10 test files)
├── docker-compose.yml      # 3-service Docker stack
├── Dockerfile              # Production container
├── Makefile                # Dev commands
├── pyproject.toml          # Python packaging
└── .github/workflows/ci.yml
```

## License

MIT
