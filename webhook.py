"""
Webhook API Server - FastAPI endpoint for WeChat and external integrations
Provides /webhook/wechat endpoint + /health + /stats
"""

import os
import time
import logging
import hashlib
import hmac
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Response, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(
    title="WeChatGPT-Dual Webhook API",
    version="3.0.0",
    description="Webhook receiver for WeChat and external integrations",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global references (set during startup)
_wechat_handler = None
_engine_manager = None
_db = None


def set_handler(wechat_handler, engine_manager, db=None):
    """Set global handler references (called from main)"""
    global _wechat_handler, _engine_manager, _db
    _wechat_handler = wechat_handler
    _engine_manager = engine_manager
    _db = db


class WebhookPayload(BaseModel):
    """Generic webhook payload"""
    msg_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    content: str = ""
    msg_type: str = "text"
    is_group: bool = False
    group_id: str = ""
    group_name: str = ""
    is_at_me: bool = False
    timestamp: Optional[float] = None
    token: Optional[str] = None


class ChatRequest(BaseModel):
    """Simple chat API request"""
    message: str
    user_id: str = "api_user"
    system_prompt: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2000


class ChatResponse(BaseModel):
    """Chat API response"""
    reply: str
    engine: str
    model: str
    tokens_used: int = 0
    latency: float = 0.0
    from_failover: bool = False


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "ok",
        "version": "3.0.0",
        "timestamp": time.time(),
        "engine_available": _engine_manager is not None,
    }


@app.get("/stats")
async def stats():
    """System statistics"""
    if not _wechat_handler:
        raise HTTPException(503, "Handler not initialized")
    return _wechat_handler.get_stats()


@app.post("/webhook/wechat")
async def wechat_webhook(payload: WebhookPayload):
    """
    WeChat message webhook receiver
    Compatible with multiple WeChat bot frameworks
    """
    if not _wechat_handler:
        raise HTTPException(503, "Handler not initialized")

    # Verify token if configured
    webhook_token = os.environ.get("WEBHOOK_TOKEN")
    if webhook_token and payload.token != webhook_token:
        raise HTTPException(403, "Invalid token")

    # Convert to dict for handler
    data = payload.model_dump()
    if data.get("timestamp") is None:
        data["timestamp"] = time.time()

    reply = _wechat_handler.handle_raw(data)

    if _db:
        _db.log_message(
            user_id=payload.sender_id,
            role="user",
            content=payload.content,
            channel="wechat",
        )
        if reply:
            _db.log_message(
                user_id=payload.sender_id,
                role="assistant",
                content=reply,
                channel="wechat",
            )

    return {
        "reply": reply or "",
        "has_reply": reply is not None,
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat_api(req: ChatRequest):
    """
    Direct chat API — send a message and get a response
    Useful for custom integrations
    """
    if not _engine_manager:
        raise HTTPException(503, "Engine not initialized")

    messages = []
    if req.system_prompt:
        messages.append({"role": "system", "content": req.system_prompt})
    messages.append({"role": "user", "content": req.message})

    try:
        response = _engine_manager.chat(
            messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
        return ChatResponse(
            reply=response.content,
            engine=response.engine_name,
            model=response.model,
            tokens_used=response.tokens_used,
            latency=round(response.latency, 3),
            from_failover=response.from_failover,
        )
    except Exception as e:
        raise HTTPException(500, f"Engine error: {e}")


@app.get("/api/engines")
async def list_engines():
    """List available engines and their status"""
    if not _engine_manager:
        raise HTTPException(503, "Engine not initialized")
    return _engine_manager.get_status()


@app.post("/api/engine/switch/{engine_name}")
async def switch_engine(engine_name: str):
    """Switch the primary engine"""
    if not _engine_manager:
        raise HTTPException(503, "Engine not initialized")

    if _engine_manager.switch_primary(engine_name):
        return {"status": "ok", "primary": engine_name}
    else:
        available = _engine_manager.list_engines()
        raise HTTPException(
            400, f"Unknown engine: {engine_name}. Available: {available}"
        )
