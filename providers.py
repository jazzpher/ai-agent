"""
LLM provider settings: presets, local storage, masking, connection test.

Keys live ONLY in providers.json (gitignored, chmod 600) or in the
NVIDIA_API_KEY env var / .env. They are never logged and never sent back to
the browser (the UI only gets a masked hint).

Order of the list = fallback order (first enabled provider is tried first).
"""
import json
import os
import time

from config import NVIDIA_API_KEY, NVIDIA_BASE_URL, DEFAULT_MODEL

PROVIDERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json")
MAX_SLOTS = 3

# name -> (base_url, suggested model). Models change often: edit in the UI.
PRESETS = {
    "NVIDIA NIM": (NVIDIA_BASE_URL, DEFAULT_MODEL),
    "Groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    "Gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash"),
    "OpenRouter": ("https://openrouter.ai/api/v1", "openai/gpt-oss-120b:free"),
    "Custom": ("", ""),
}


def _blank():
    return {"preset": "Custom", "base_url": "", "model": "", "api_key": "", "enabled": False}


def environment_providers() -> list[dict]:
    """Durable deployment configuration; never include raw JSON in errors."""
    raw = os.environ.get("AGENT_PROVIDERS_JSON", "")
    if not raw:
        return []
    try:
        items = json.loads(raw)
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_SLOTS:
            raise ValueError()
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError()
            p = {**_blank(), **item}
            if (not all(isinstance(p[k], str) for k in ("preset", "base_url", "model", "api_key"))
                    or not isinstance(p["enabled"], bool) or p["preset"] not in PRESETS):
                raise ValueError()
            normalized.append(p)
        return normalized
    except (ValueError, TypeError):
        raise ValueError("AGENT_PROVIDERS_JSON must be a JSON list of 1 to 3 valid provider objects.") from None


def load_providers() -> list[dict]:
    """Saved providers (with keys). Falls back to NVIDIA_API_KEY from env/.env."""
    try:
        with open(PROVIDERS_FILE, encoding="utf-8") as f:
            items = json.load(f).get("providers", [])
        items = [{**_blank(), **p} for p in items][:MAX_SLOTS]
    except (OSError, ValueError):
        items = []
    if not items:
        items = environment_providers()
    if not items and NVIDIA_API_KEY:
        base, model = PRESETS["NVIDIA NIM"]
        items = [{"preset": "NVIDIA NIM", "base_url": base, "model": model,
                  "api_key": NVIDIA_API_KEY, "enabled": True}]
    return items


def active_providers() -> list[dict]:
    """Enabled providers that have a key and URL, in fallback order."""
    return [p for p in load_providers()
            if p.get("enabled") and p.get("api_key") and p.get("base_url") and p.get("model")]


def save_providers(items: list[dict]) -> None:
    data = json.dumps({"providers": items[:MAX_SLOTS]}, indent=2)
    fd = os.open(PROVIDERS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    try:
        os.chmod(PROVIDERS_FILE, 0o600)
    except OSError:
        pass


def mask_key(key: str) -> str:
    if not key:
        return "no key saved"
    if len(key) <= 8:
        return "saved (••••)"
    return f"saved ({key[:3]}…{key[-4:]})"


def test_connection(base_url: str, model: str, api_key: str) -> str:
    """One tiny completion. Returns a status line with latency. Never echoes the key."""
    if not (base_url and model and api_key):
        return "⚠️ Fill base URL, model and key first."
    from openai import OpenAI
    start = time.time()
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=30, max_retries=0)
        client.chat.completions.create(
            model=model, max_tokens=8,
            messages=[{"role": "user", "content": "Reply with: ok"}],
        )
        return f"✅ Connected. {int((time.time() - start) * 1000)} ms"
    except Exception as e:  # message from the SDK never contains the key
        msg = str(e).replace(api_key, "***")
        return f"❌ {type(e).__name__} after {int((time.time() - start) * 1000)} ms: {msg[:200]}"
