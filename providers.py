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

from api_retry import make_client, completion_with_retry

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


# Whether the model can look at images. Only a starting guess; set it per slot in the UI.
PRESET_VISION = {"Gemini": True}


def _blank():
    return {"preset": "Custom", "base_url": "", "model": "", "api_key": "", "enabled": False,
            "vision": False}


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
                    or not isinstance(p["enabled"], bool) or not isinstance(p["vision"], bool)
                    or p["preset"] not in PRESETS):
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
    start = time.time()
    try:
        client = make_client(base_url, api_key)
        completion_with_retry(client,
            model=model, max_tokens=128,
            messages=[{"role": "user", "content": "Reply with: ok"}],
        )
        return f"✅ Connected. {int((time.time() - start) * 1000)} ms"
    except Exception as e:  # message from the SDK never contains the key
        msg = str(e).replace(api_key, "***")
        return f"❌ {type(e).__name__} after {int((time.time() - start) * 1000)} ms: {msg[:200]}"


# ---------------------------------------------------------------
# Model list and benchmark. Both make real API calls, but only when
# the user presses the button. Nothing here runs at startup.
# ---------------------------------------------------------------

def list_models(base_url: str, api_key: str, client=None) -> list[str]:
    """Model ids from GET {base_url}/models, sorted. Raises on failure."""
    if not (base_url and api_key):
        raise ValueError("Fill base URL and key first.")
    client = client or make_client(base_url, api_key)
    last = None
    for attempt, delay in enumerate((0, 3, 8)):
        if delay:
            time.sleep(delay)
        try:
            data = client.models.list()
            ids = sorted({getattr(m, "id", "") for m in getattr(data, "data", data) if getattr(m, "id", "")})
            return ids
        except Exception as e:
            from api_retry import is_transient
            last = e
            if not is_transient(e):
                break
    raise last


_TOOL = [{"type": "function", "function": {
    "name": "get_weather", "description": "Get the weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                   "required": ["city"]}}}]


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _check_instruction(msg) -> bool:
    return (msg.content or "").strip().strip(".!").upper() == "PONG"


def _check_json(msg) -> bool:
    try:
        data = json.loads(_strip_fences(msg.content))
        return data.get("total") == 42 and data.get("items") == ["x", "y"]
    except Exception:
        return False


def _check_tool_call(msg) -> bool:
    try:
        call = msg.tool_calls[0]
        args = json.loads(call.function.arguments)
        return call.function.name == "get_weather" and "manila" in str(args.get("city", "")).lower()
    except Exception:
        return False


def _check_code(msg) -> bool:
    import ast
    try:
        tree = ast.parse(_strip_fences(msg.content))
    except SyntaxError:
        return False
    return any(isinstance(n, ast.FunctionDef) and n.name == "add"
               and any(isinstance(x, ast.Return) for x in ast.walk(n)) for n in ast.walk(tree))


def _check_tagalog(msg) -> bool:
    text = (msg.content or "").lower()
    words = set(text.replace(",", " ").replace(".", " ").split())
    markers = {"ang", "ay", "na", "sa", "kulay", "asul", "langit", "ng", "mga", "umaga"}
    return 0 < len(text) < 400 and len(words & markers) >= 2


BENCHMARK_TASKS = [
    ("Sundin ang instruction", [{"role": "user", "content": "Reply with exactly the word PONG and nothing else."}],
     None, _check_instruction),
    ("JSON lang", [{"role": "user", "content": 'Return only a JSON object: {"total": 17+25 as a number, "items": ["x","y"]}. No other text.'}],
     None, _check_json),
    ("Tool call", [{"role": "user", "content": "What is the weather in Manila? Use the tool."}],
     _TOOL, _check_tool_call),
    ("Python code", [{"role": "user", "content": "Write a Python function named add(a, b) that returns a+b. Return only the code."}],
     None, _check_code),
    ("Tagalog", [{"role": "user", "content": "Sumagot sa Tagalog sa isang maikling pangungusap: anong kulay ng langit sa umaga?"}],
     None, _check_tagalog),
]


def benchmark_model(base_url: str, model: str, api_key: str, client=None,
                    per_task_seconds: float = 90.0) -> list[dict]:
    """Run the fixed mini-suite. 5 small requests. Never echoes the key."""
    if not (base_url and model and api_key):
        raise ValueError("Fill base URL, model and key first.")
    client = client or make_client(base_url, api_key)
    results = []
    for name, messages, tools, check in BENCHMARK_TASKS:
        start = time.time()
        row = {"task": name, "passed": False, "ms": 0, "tokens": None, "error": ""}
        try:
            kwargs = dict(model=model, messages=messages, max_tokens=300, temperature=0)
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            resp = completion_with_retry(client, deadline=time.monotonic() + per_task_seconds, **kwargs)
            row["passed"] = bool(check(resp.choices[0].message))
            usage = getattr(resp, "usage", None)
            row["tokens"] = getattr(usage, "completion_tokens", None) if usage else None
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {str(e).replace(api_key, '***')[:120]}"
        row["ms"] = int((time.time() - start) * 1000)
        results.append(row)
    return results


def format_benchmark(model: str, results: list[dict]) -> str:
    passed = sum(1 for r in results if r["passed"])
    total_ms = sum(r["ms"] for r in results)
    lines = [f"**Benchmark: `{model}`** - {passed}/{len(results)} pumasa, {total_ms / 1000:.1f}s total", "",
             "| Task | Result | Time |", "|---|---|---|"]
    for r in results:
        mark = "✅" if r["passed"] else ("❌ " + r["error"] if r["error"] else "❌")
        lines.append(f"| {r['task']} | {mark} | {r['ms'] / 1000:.1f}s |")
    return "\n".join(lines)
