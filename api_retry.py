"""Bounded retries before a completion starts, never replay a partial stream."""
import time
from openai import OpenAI, APIConnectionError, APIStatusError

REQUEST_TIMEOUT_SECONDS = 180.0
RETRY_DELAYS = (2, 4, 8, 16, 32, 13)  # 75 seconds of backoff, seven attempts
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
        try:
            return client.chat.completions.create(**request)
        except Exception as error:
            if not is_transient(error) or attempt == len(RETRY_DELAYS):
                raise
            delay = RETRY_DELAYS[attempt]
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise
            if on_retry:
                on_retry(attempt + 1, delay, type(error).__name__)
            wake_at = time.monotonic() + delay
            while time.monotonic() < wake_at:
                if cancelled():
                    raise CompletionCancelled()
                time.sleep(max(0, min(0.25, wake_at - time.monotonic())))
