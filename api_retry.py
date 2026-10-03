"""Bounded retries before a completion starts, never replay a partial stream."""
import time
import json
from openai import OpenAI, APIConnectionError, APIStatusError

REQUEST_TIMEOUT_SECONDS = 30.0
RETRY_DELAYS = (1, 2)  # three attempts, at most three seconds of backoff
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


class CompletionCancelled(Exception):
    pass


def make_client(base_url, api_key):
    # Own retries here so SDK retries do not multiply attempts or hide waits.
    return OpenAI(base_url=base_url, api_key=api_key,
                  timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)


def is_transient(error):
    return (isinstance(error, APIConnectionError) or
            isinstance(error, APIStatusError) and error.status_code in TRANSIENT_STATUSES)


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
        request["timeout"] = min(REQUEST_TIMEOUT_SECONDS, remaining) if remaining is not None else REQUEST_TIMEOUT_SECONDS
        print(json.dumps({"event": "request_start", "attempt": attempt + 1,
                          "timeout_seconds": request["timeout"],
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
