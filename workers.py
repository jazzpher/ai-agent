"""Manager + parallel workers.

The main agent is the manager: for a big task it calls `delegate_tasks` with 2-5 independent
subtasks. Each subtask runs in its own worker (a small tool loop on the same model) at the same
time. Workers are READ-ONLY (search, fetch, read files) so parallel runs never fight over files
or ask for approvals. Writing files, running code and the final answer stay with the manager.

No step limit (the owner does not want one). Each worker has a wall-clock budget, stops after
5 identical consecutive tool calls, and obeys the Stop button.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor

MAX_TASKS = 5
MAX_WORKERS = 3            # NVIDIA trial is 40 req/min; keep the burst small
WORKER_SECONDS = 600
MAX_RESULT_CHARS = 6000
WORKER_TOOLS = ("web_search", "fetch_page", "job_search", "read_file", "list_files")

WORKER_SYSTEM = (
    "You are a worker agent. A manager gave you ONE subtask of a bigger job. Do only that subtask, "
    "using the tools if needed, then reply with a compact result (facts, numbers, source URLs; "
    "write 'unverified' for anything you did not see in a tool result). Do not ask questions. "
    "You cannot write files or run code; just report what you found. Match the subtask's language.")


def _short(text, n):
    text = str(text or "")
    return text if len(text) <= n else text[: n - 20] + "\n...[truncated]"


def run_worker(task: dict, client, model: str, tool_defs: list, execute, cancelled=lambda: False,
               seconds: float = WORKER_SECONDS, create=None) -> dict:
    """One worker's loop. Never raises. `create(client, **kw)` can be injected for tests."""
    title = str(task.get("title") or "subtask")[:80]
    t0 = time.monotonic()
    messages = [{"role": "system", "content": WORKER_SYSTEM},
                {"role": "user", "content": str(task.get("instructions") or title)}]
    last_sig, repeats, steps = None, 0, 0
    if create is None:
        from api_retry import completion_with_retry  # 429/5xx get retried, not turned into worker failures
        create = lambda c, **kw: completion_with_retry(c, cancelled=cancelled, **kw)
    try:
        while True:
            if cancelled():
                return {"title": title, "status": "cancelled", "output": "", "steps": steps}
            if time.monotonic() - t0 > seconds:
                return {"title": title, "status": "timeout", "steps": steps,
                        "output": "Worker ran out of time before finishing."}
            resp = create(client, model=model, messages=messages, tools=tool_defs, tool_choice="auto",
                          temperature=0.2, max_tokens=2000, timeout=120)
            msg = resp.choices[0].message
            calls = getattr(msg, "tool_calls", None) or []
            if not calls:
                text = (msg.content or "").strip()
                if not text:
                    return {"title": title, "status": "error", "steps": steps, "output": "Worker returned no answer."}
                return {"title": title, "status": "success", "steps": steps, "output": _short(text, MAX_RESULT_CHARS)}
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]})
            for c in calls:
                steps += 1
                name = c.function.name
                sig = (name, c.function.arguments)
                repeats = repeats + 1 if sig == last_sig else 1
                last_sig = sig
                if repeats >= 5:
                    return {"title": title, "status": "error", "steps": steps,
                            "output": "Worker stopped: same tool call 5 times in a row."}
                if name not in WORKER_TOOLS:
                    out = {"status": "error", "output": f"Tool {name} is not available to workers."}
                else:
                    try:
                        args = json.loads(c.function.arguments or "{}")
                        out = execute(name, args)
                    except Exception as e:
                        out = {"status": "error", "output": f"Tool raised: {type(e).__name__}: {e}"}
                messages.append({"role": "tool", "tool_call_id": c.id,
                                 "content": _short(json.dumps(out, ensure_ascii=False)
                                                   if isinstance(out, dict) else out, 8000)})
    except Exception as e:
        return {"title": title, "status": "error", "steps": steps,
                "output": f"Worker failed: {type(e).__name__}: {str(e)[:200]}"}


def run_workers(tasks, client, model: str, tool_defs: list, execute, cancelled=lambda: False,
                max_workers: int = MAX_WORKERS, seconds: float = WORKER_SECONDS, create=None) -> dict:
    """Run subtasks at the same time. Result order matches input order."""
    if not isinstance(tasks, list) or not tasks:
        return {"status": "error", "output": "Give `tasks`: a list of 2-5 objects with `title` and `instructions`."}
    tasks = [t if isinstance(t, dict) else {"title": str(t)[:40], "instructions": str(t)} for t in tasks][:MAX_TASKS]
    defs = [d for d in tool_defs if d["function"]["name"] in WORKER_TOOLS]
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(tasks)))) as pool:
        futures = [pool.submit(run_worker, t, client, model, defs, execute, cancelled, seconds, create) for t in tasks]
        results = [f.result() for f in futures]
    ok = sum(1 for r in results if r["status"] == "success")
    lines = [f"{len(results)} workers finished in {time.monotonic() - t0:.0f}s ({ok} succeeded)."]
    for i, r in enumerate(results, 1):
        lines.append(f"\n### Worker {i}: {r['title']} [{r['status']}, {r['steps']} tool calls, model {model}]\n{r['output']}")
    lines.append("\nManager: combine these results, check them against each other, and write the final answer. "
                 "Redo any failed subtask yourself.")
    return {"status": "success" if ok else "error", "output": "\n".join(lines), "workers": results}
