# WeChatGPT Dual

🤖 AI chatbot for Telegram (WeChat bridge planned). Multi-model, context-aware, role-switching.

## Features

- 💬 Context-aware conversations (configurable history)
- 🧠 Multi-model: GPT-4o / Claude / DeepSeek / any OpenAI-compatible
- 🎭 Custom roles: translator, coder, writer, etc.
- 🔄 Per-user model & role settings
- 📊 Usage stats

## Quick Start

```bash
git clone https://github.com/platoba/WeChatGPT-Dual.git
cd WeChatGPT-Dual
pip install requests
BOT_TOKEN=xxx OPENAI_API_KEY=sk-xxx python bot.py
```

## Commands

| Command | Description |
|---------|-------------|
| `/clear` | Clear conversation history |
| `/model <name>` | Switch AI model |
| `/role <desc>` | Set AI persona |
| `/models` | List available models |
| `/stats` | Conversation stats |

## License

MIT

## 🔗 Related

- [AI-Listing-Writer](https://github.com/platoba/AI-Listing-Writer) - AI listing generator
- [OmniMessage-Gateway](https://github.com/platoba/OmniMessage-Gateway) - Multi-channel messaging
