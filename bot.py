"""
WeChatGPT Dual - 微信+Telegram双端AI聊天Bot
基于OpenAI兼容API，支持上下文对话、角色设定、多模型切换
"""

import os
import time
import json
import requests
from collections import defaultdict

TOKEN = os.environ.get("BOT_TOKEN", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "20"))
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT", "你是一个有用的AI助手。回答简洁、准确、有帮助。")

if not TOKEN:
    raise ValueError("未设置 BOT_TOKEN!")
if not OPENAI_KEY:
    raise ValueError("未设置 OPENAI_API_KEY!")

API_URL = f"https://api.telegram.org/bot{TOKEN}"

# 用户对话历史
conversations = defaultdict(list)
user_models = {}
user_prompts = {}


def tg_get(method, params=None):
    try:
        r = requests.get(f"{API_URL}/{method}", params=params, timeout=35)
        return r.json()
    except:
        return None


def tg_send(chat_id, text, reply_to=None, parse_mode="Markdown"):
    params = {"chat_id": chat_id, "text": text}
    if reply_to: params["reply_to_message_id"] = reply_to
    if parse_mode: params["parse_mode"] = parse_mode
    result = tg_get("sendMessage", params)
    if not result or not result.get("ok"):
        params.pop("parse_mode", None)
        result = tg_get("sendMessage", params)
    return result


def get_updates(offset=None):
    params = {"timeout": 30}
    if offset: params["offset"] = offset
    return tg_get("getUpdates", params)


def chat_ai(user_id, message):
    model = user_models.get(user_id, OPENAI_MODEL)
    system = user_prompts.get(user_id, SYSTEM_PROMPT)

    conversations[user_id].append({"role": "user", "content": message})
    if len(conversations[user_id]) > MAX_HISTORY:
        conversations[user_id] = conversations[user_id][-MAX_HISTORY:]

    messages = [{"role": "system", "content": system}] + conversations[user_id]

    try:
        r = requests.post(f"{OPENAI_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "temperature": 0.7, "max_tokens": 2000},
            timeout=60)
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"]["content"]
        conversations[user_id].append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        return f"⚠️ AI错误: {e}"


def handle(chat_id, msg_id, text, user_id):
    if text == "/start":
        tg_send(chat_id,
            "🤖 *WeChatGPT Dual*\n\n"
            "AI聊天助手，直接发消息开始对话。\n\n"
            "📌 命令:\n"
            "  /clear — 清除对话历史\n"
            "  /model <名称> — 切换模型\n"
            "  /role <描述> — 设置AI角色\n"
            "  /models — 查看可用模型\n"
            "  /stats — 对话统计\n"
            f"\n当前模型: `{user_models.get(user_id, OPENAI_MODEL)}`", msg_id)

    elif text == "/clear":
        conversations[user_id].clear()
        tg_send(chat_id, "🗑️ 对话历史已清除", msg_id)

    elif text.startswith("/model"):
        model = text[6:].strip()
        if model:
            user_models[user_id] = model
            tg_send(chat_id, f"✅ 模型已切换: `{model}`", msg_id)
        else:
            current = user_models.get(user_id, OPENAI_MODEL)
            tg_send(chat_id, f"当前模型: `{current}`\n用法: /model gpt-4o", msg_id)

    elif text == "/models":
        tg_send(chat_id,
            "🧠 可用模型:\n\n"
            "  `gpt-4o-mini` — 快速便宜\n"
            "  `gpt-4o` — 强大均衡\n"
            "  `gpt-4-turbo` — 最强推理\n"
            "  `claude-3-haiku` — 快速\n"
            "  `claude-3-sonnet` — 均衡\n"
            "  `deepseek-chat` — 中文优化\n\n"
            "用法: /model <名称>", msg_id)

    elif text.startswith("/role"):
        role = text[5:].strip()
        if role:
            user_prompts[user_id] = role
            conversations[user_id].clear()
            tg_send(chat_id, f"✅ 角色已设置，对话已重置\n\n角色: {role[:100]}", msg_id)
        else:
            current = user_prompts.get(user_id, SYSTEM_PROMPT)
            tg_send(chat_id, f"当前角色: {current[:200]}\n\n用法: /role 你是一个翻译专家", msg_id)

    elif text == "/stats":
        msgs = len(conversations.get(user_id, []))
        model = user_models.get(user_id, OPENAI_MODEL)
        tg_send(chat_id, f"📊 对话统计\n\n消息数: {msgs}\n模型: {model}\n上下文窗口: {MAX_HISTORY}", msg_id)

    elif not text.startswith("/"):
        reply = chat_ai(user_id, text)
        if len(reply) > 4000:
            chunks = [reply[i:i+4000] for i in range(0, len(reply), 4000)]
            for i, chunk in enumerate(chunks):
                tg_send(chat_id, chunk, msg_id if i == 0 else None)
        else:
            tg_send(chat_id, reply, msg_id)


def main():
    print(f"\n{'='*50}")
    print(f"  WeChatGPT Dual Bot")
    print(f"  Model: {OPENAI_MODEL}")
    print(f"{'='*50}")

    me = tg_get("getMe")
    if me and me.get("ok"):
        print(f"\n✅ @{me['result']['username']} 已上线!")
    else:
        print("\n❌ 无法连接Telegram!")
        return

    offset = None
    while True:
        try:
            result = get_updates(offset)
            if not result or not result.get("ok"):
                time.sleep(5)
                continue
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg: continue
                text = (msg.get("text") or "").strip()
                if text:
                    handle(msg["chat"]["id"], msg["message_id"], text, msg["from"]["id"])
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[错误] {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
