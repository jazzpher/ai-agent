"""Model routing: biggest available model first. No network, no keys.

The owner rejects small fast models, so providers are ordered by model size (largest
first). Fallback on errors walks down that list and skips small models while a big one
is left. The model that answered is shown under the answer.
"""
import os
import re

# Order matters: first match wins. Sizes are billions of parameters (total, not active).
_HINTS = [
    (r"nemotron.*ultra|ultra.*nemotron", 500),
    (r"gemini.*pro", 400),
    (r"gemini.*flash-lite", 30),
    (r"gemini.*flash", 100),
    (r"deepseek.*(v3|v4|r1)", 600),
    (r"kimi.?k2", 1000),
    (r"qwen3.*coder", 480),
    (r"llama.*405", 405),
    (r"gpt-oss-120", 120),
    (r"gpt-oss-20", 20),
    (r"nemotron.*super", 100),
    (r"nemotron.*nano", 30),
    (r"mini|small|tiny|lite|instant|haiku", 8),
]
DEFAULT_SIZE = 40.0
MIN_FALLBACK_B = 30.0  # never fall back below this while a bigger model is available


def model_size_b(model: str) -> float:
    m = (model or "").lower()
    for pat, size in _HINTS:
        if re.search(pat, m):
            return float(size)
    nums = [float(x) for x in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)b(?![a-z])", m)]
    if nums:
        return max(nums)
    return DEFAULT_SIZE


def routing_enabled() -> bool:
    return os.environ.get("AGENT_ROUTING", "size").strip().lower() != "order"


def rank_providers(providers: list, need_vision: bool = False) -> list:
    """Largest model first. Stable for ties (the saved order breaks them).

    need_vision: providers that can see images go first (still largest first inside each group).
    """
    if not routing_enabled():
        return list(providers)
    def key(item):
        i, p = item
        return (0 if (not need_vision or p.get("vision")) else 1, -model_size_b(p.get("model", "")), i)
    return [p for _, p in sorted(enumerate(providers), key=key)]


def pick_for_model(providers: list, model: str):
    """The provider that owns this model id (the chat picker can name any of them)."""
    for p in providers:
        if p.get("model") == model:
            return p
    return None


def next_fallback(ranked: list, idx: int):
    """Index of the next provider to try after idx, or None.

    Big models (>= MIN_FALLBACK_B) first; a small one is used only when nothing bigger is left.
    """
    rest = list(range(idx + 1, len(ranked)))
    for j in rest:
        if model_size_b(ranked[j].get("model", "")) >= MIN_FALLBACK_B:
            return j
    return rest[0] if rest else None


def label(provider: dict) -> str:
    return f"{provider.get('model', '?')} ({provider.get('preset', '?')})"


def answered_by(model: str, provider_name: str = "", fell_back: bool = False) -> str:
    note = " - fallback, nag-error yung mas malaki" if fell_back else ""
    prov = f" via {provider_name}" if provider_name else ""
    return f"\n\n<sub>🤖 Sumagot: `{model}`{prov} (~{model_size_b(model):g}B){note}</sub>\n"
