"""Bounded retries before a completion starts, never replay a partial stream."""
import time
import json
import httpx
from openai import OpenAI, APIConnectionError, APIStatusError, APIError

# Read timeout is the max silence between bytes. Slow models (Nemotron Ultra)
# can stay quiet for a minute or more while writing a long tool call, so 30s
# cut real work off. Connect stays short so a dead host still fails fast.
REQUEST_TIMEOUT_SECONDS = 240.0
CONNECT_TIMEOUT_SECONDS = 20.0


def build_timeout(read_seconds):
    return httpx.Timeout(read_seconds, connect=min(CONNECT_TIMEOUT_SECONDS, read_seconds),
                         write=30.0, pool=30.0)
RETRY_DELAYS = (1, 2)  # three attempts, at most three seconds of backoff
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


class CompletionCancelled(Exception):
    pass


def make_client(base_url, api_key):
    # Own retries here so SDK retries do not multiply attempts or hide waits.
    return OpenAI(base_url=base_url, api_key=api_key,
                  timeout=build_timeout(REQUEST_TIMEOUT_SECONDS), max_retries=0)


def is_rate_limited(error):
    """Recognize HTTP and provider SSE rate/quota exhaustion, not auth failures."""
    if isinstance(error, APIStatusError):
        return error.status_code == 429
    if not isinstance(error, APIError):
        return False
    body = getattr(error, "body", None) or {}
    if isinstance(body, dict):
        code = str(body.get("code") or body.get("type") or body.get("status") or "").lower()
        if code in {"429", "resource_exhausted", "rate_limit_exceeded", "rate_limit_error", "insufficient_quota"}:
            return True
    text = str(error).lower()
    return any(term in text for term in ("resource exhausted", "resource_exhausted", "rate limit", "quota exceeded"))


def public_error(error, *, partial=False):
    """User-facing failure without leaking provider payloads or request data."""
    if is_rate_limited(error):
        text = "Naabot ang free provider limit. Walang available na fallback ngayon; subukan ulit mamaya."
    elif is_transient(error):
        text = "Pansamantalang hindi available ang model. Subukan ulit mamaya."
    else:
        text = "Hindi natapos ang model response. Error type: " + type(error).__name__ + "."
    if partial:
        text += " May partial output, kaya hindi ko inulit ang request para maiwasang madoble ang actions."
    return text


def is_transient(error):
    # Some providers send an SSE error with only a message (no HTTP status).
    # Never retry authentication/permission/validation failures based on wording.
    if isinstance(error, APIStatusError):
        return error.status_code in TRANSIENT_STATUSES
    if isinstance(error, APIError) and isinstance(getattr(error, "body", None), dict):
        code = str(error.body.get("code") or error.body.get("status") or "")
        if code.isdigit() and int(code) in TRANSIENT_STATUSES:
            return True
    return (is_rate_limited(error) or isinstance(error, (APIConnectionError, httpx.TransportError)) or
            isinstance(error, (TimeoutError, ConnectionError)) or
            any(term in str(error).lower() for term in (
                "service temporarily overloaded", "temporarily unavailable",
                "connection reset", "connection closed", "incomplete chunked read")))


def completion_with_retry(client, *, cancelled=lambda: False, deadline=None,
                          on_retry=None, **kwargs):
    """Retry request creation only; caller owns consumption of streaming results.

    deadline is a monotonic timestamp. Cancellation is checked during backoff;
    an in-flight synchronous HTTP request can still take up to its timeout.
    """
    for attempt in range(len(RETRY_DELAYS) + 1):
        if cancelled():
            raise CompletionCancelled()
        remaining = deadline - time.monotonic() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Conversation time budget exhausted")
        request = dict(kwargs)
        read_seconds = min(REQUEST_TIMEOUT_SECONDS, remaining) if remaining is not None else REQUEST_TIMEOUT_SECONDS
        request["timeout"] = build_timeout(read_seconds)
        print(json.dumps({"event": "request_start", "attempt": attempt + 1,
                          "timeout_seconds": read_seconds,
                          "stream": bool(request.get("stream"))}), flush=True)
        try:
            result = client.chat.completions.create(**request)
            print(json.dumps({"event": "request_ready", "attempt": attempt + 1}), flush=True)
            return result
        except Exception as error:
            print(json.dumps({"event": "request_error", "attempt": attempt + 1,
                              "error_type": type(error).__name__,
                              "status": getattr(error, "status_code", None)}), flush=True)
            if not is_transient(error) or attempt == len(RETRY_DELAYS):
                raise
            delay = RETRY_DELAYS[attempt]
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise
            print(json.dumps({"event": "request_retry", "next_attempt": attempt + 2,
                              "delay_seconds": delay}), flush=True)
            if on_retry:
                on_retry(attempt + 1, delay, type(error).__name__)
            wake_at = time.monotonic() + delay
            while time.monotonic() < wake_at:
                if cancelled():
                    raise CompletionCancelled()
                time.sleep(max(0, min(0.25, wake_at - time.monotonic())))
