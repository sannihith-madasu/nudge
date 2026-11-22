import os
import httpx

URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("LLM_MODEL", "qwen2.5:1.5b")
KEEP = os.getenv("LLM_KEEP_ALIVE", "30m")

async def chat(messages, schema=None, temperature=0.0, max_tokens=220, model=None):
    body = {
        "model": model or MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": KEEP,
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 2048},
    }
    if schema:
        body["format"] = schema
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(f"{URL}/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]["content"]
