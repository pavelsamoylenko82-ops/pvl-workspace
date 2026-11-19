import os
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)

LLM_API_URL = os.getenv("LLM_API_URL", "https://api.deepseek.com/v1/chat/completions")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
FALLBACK_PHONE = os.getenv("FALLBACK_PHONE", "")
MAX_MESSAGES = int(os.getenv("MAX_MESSAGES_PER_SESSION", "20"))

PROMPT_PATH = Path(__file__).parent / "system_prompt.md"

class ChatRequest(BaseModel):
    messages: list[dict]

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    if len(req.messages) > MAX_MESSAGES:
        return {"reply": f"Лимит сообщений в чате исчерпан. Позвоните нам: {FALLBACK_PHONE}"}

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else ""
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": system_prompt}, *req.messages],
        "max_tokens": 300,
        "temperature": 0.4,
    }
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(LLM_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return {"reply": data["choices"][0]["message"]["content"]}
    except Exception:
        return {"reply": f"Чат временно не отвечает, позвоните нам: {FALLBACK_PHONE}"}