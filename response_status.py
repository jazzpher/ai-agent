"""Keep UI updates flowing while synchronous provider I/O is blocked.

A worker owns each iterator and closes it. The caller never replays a partial
stream. Cancelling stops the UI promptly; a blocked HTTP read may run until its
configured timeout before the daemon worker exits.
"""
import queue
import threading
import time

from api_retry import CompletionCancelled


def with_status(factory, *, deadline, cancelled=lambda: False, interval=1.0):
    events = queue.Queue(maxsize=8)
    stopped = threading.Event()

    def emit(kind, value):
        while not stopped.is_set():
            try:
                events.put((kind, value), timeout=0.1)
                return True
            except queue.Full:
                pass
        return False

    def close_item(item):
        close = getattr(item, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass

    def work():
        iterator = None
        try:
            iterator = iter(factory())
            for item in iterator:
                if stopped.is_set():
                    close_item(item)
                    break
                if not emit("item", item):
                    close_item(item)
                    break
            emit("done", None)
        except Exception as error:
            emit("error", error)
        finally:
            close = getattr(iterator, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass

    threading.Thread(target=work, daemon=True).start()
    last_status = time.monotonic()
    try:
        while True:
            if cancelled():
                raise CompletionCancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Conversation time budget exhausted")
            try:
                kind, value = events.get(timeout=min(interval, remaining, 0.1))
            except queue.Empty:
                kind, value = "wait", None
            if kind == "error":
                raise value
            if kind == "done":
                return
            if kind == "item":
                yield kind, value
            # Also tick when reasoning chunks arrive continuously.
            if time.monotonic() - last_status >= interval:
                last_status = time.monotonic()
                yield "wait", None
    finally:
        stopped.set()
        while True:
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                break
            if kind == "item":
                close_item(value)
