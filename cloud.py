import os, re, sys, asyncio
from datetime import datetime
import httpx
from dotenv import load_dotenv
load_dotenv()

TAVILY_KEY = os.getenv("TAVILY_API_KEY", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODELS = [m.strip() for m in os.getenv(
    "CLOUD_MODELS", "gemini-3.5-flash,gemini-3.6-flash,gemini-3.5-flash-lite").split(",") if m.strip()]

TAVILY_URL = "https://api.tavily.com/search"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

SYSTEM = ("You are a research helper for a personal assistant. Answer the question using ONLY the "
          "search results provided. Be accurate and concise (under 100 words), in plain text with "
          "no markdown. Put the source number in square brackets after each claim, like [1]. "
          "If the results do not answer the question, say you could not find a reliable answer.")


def scrub(text):
    """Mask emails and phone-like numbers before anything leaves the Pi."""
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", text)
    text = re.sub(r"(?:\+?\d[\s().-]?){10,}", "[number]", text)
    return text


def _err(resp):
    try:
        return resp.json()["error"]["message"][:200]
    except Exception:
        return resp.text[:200]


def _clean(text):
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"^\s*[*-]\s+", "- ", text, flags=re.M)
    return text.strip()


async def search(question, client):
    r = await client.post(TAVILY_URL,
                          headers={"Authorization": f"Bearer {TAVILY_KEY}"},
                          json={"query": question, "search_depth": "basic", "max_results": 5})
    if r.status_code != 200:
        raise RuntimeError(f"Tavily HTTP {r.status_code}: {_err(r)}")
    results = r.json().get("results") or []
    if not results:
        raise RuntimeError("Tavily returned no results")
    return results


def _snippets(results):
    parts = []
    for i, r in enumerate(results, 1):
        content = re.sub(r"\s+", " ", r.get("content", "")).strip()[:500]
        parts.append(f"[{i}] {r.get('title', '')} ({r.get('url', '')})\n{content}")
    return "\n\n".join(parts)


async def write_answer(question, results, client):
    """Ask Gemini (no search tool) to write an answer from the results; tries each model in turn."""
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    prompt = (f"Today's date: {datetime.now().strftime('%d %B %Y')}\n\n"
              f"Question: {question}\n\nSearch results:\n{_snippets(results)}")
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
    }
    last = "no models configured"
    for model in MODELS:
        try:
            r = await client.post(GEMINI_URL.format(model),
                                  headers={"x-goog-api-key": GEMINI_KEY}, json=body)
        except httpx.HTTPError as e:
            last = f"{model}: {e!r}"
            print("gemini skip:", last)
            continue
        if r.status_code != 200:
            last = f"{model}: HTTP {r.status_code} {_err(r)}"
            print("gemini skip:", last)
            continue
        cands = r.json().get("candidates") or []
        parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
        text = "".join(p.get("text", "") for p in parts).strip()
        if text:
            return text
        last = f"{model}: empty answer"
        print("gemini skip:", last)
    raise RuntimeError(last)


def _fallback_text(results):
    lines = ["Here is what the top results say:"]
    for r in results[:3]:
        snippet = re.sub(r"\s+", " ", r.get("content", "")).strip()[:200]
        lines.append(f"- {r.get('title', 'result')}: {snippet}")
    return "\n".join(lines)


async def ask(question):
    """Returns (answer_text, [(title, url), ...]). Raises RuntimeError if the search itself fails."""
    if not TAVILY_KEY:
        raise RuntimeError("TAVILY_API_KEY is not set in .env")
    q = scrub(question)

    async with httpx.AsyncClient(timeout=60) as client:
        results = await search(q, client)
        try:
            text = await write_answer(q, results, client)
        except Exception as e:
            print("answer writer failed, using raw results:", repr(e))
            text = None

    if text is None:  # every Gemini model failed: still return the real results
        sources = [(r.get("title") or "source", r["url"]) for r in results[:3] if r.get("url")]
        return _fallback_text(results), sources

    cited = []
    for group in re.findall(r"\[([\d,\s]+)\]", text):       # handles [3] and [3, 4]
        for n in re.findall(r"\d+", group):
            i = int(n)
            if 1 <= i <= len(results) and i not in cited:
                cited.append(i)
    text = _clean(re.sub(r"\s*\[[\d,\s]+\]", "", text))
    picks = [results[i - 1] for i in cited] or results[:3]
    sources = [(r.get("title") or "source", r["url"]) for r in picks if r.get("url")]
    return text, sources[:3]


if __name__ == "__main__":
    # quick self-test:  python cloud.py "latest news on ISRO"
    q = " ".join(sys.argv[1:]) or "what is the latest news on ISRO"
    answer, srcs = asyncio.run(ask(q))
    print(answer)
    for title, link in srcs:
        print("-", title, link)
