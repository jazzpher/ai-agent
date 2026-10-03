"""Research grounding: tool budgets, an evidence ledger, and claim-to-source checks.

Everything here is deterministic and offline-testable. The model never decides
whether a claim is backed up on its own: figures and links in the final answer
are compared with the text the tools actually returned this turn.
"""
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, urlparse

import httpx

RESEARCH_TOOLS = ("web_search", "fetch_page", "image_search")
MAX_SEARCHES = 6
MAX_FETCHES = 14
MAX_EMPTY_SEARCHES = 3
MAX_RESEARCH_SECONDS = 420

OFFICIAL_PAGES = {
    "render": ["https://render.com/pricing", "https://render.com/docs/free"],
    "vercel": ["https://vercel.com/pricing", "https://vercel.com/docs/limits"],
    "netlify": ["https://www.netlify.com/pricing/"],
    "railway": ["https://railway.com/pricing"],
    "fly.io": ["https://fly.io/docs/about/pricing/"],
    "cloudflare": ["https://developers.cloudflare.com/pages/platform/limits/"],
    "github pages": ["https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits"],
    "koyeb": ["https://www.koyeb.com/pricing"],
    "supabase": ["https://supabase.com/pricing"],
    "neon": ["https://neon.com/pricing"],
    "pythonanywhere": ["https://www.pythonanywhere.com/pricing/"],
    "hugging face": ["https://huggingface.co/pricing"],
    "heroku": ["https://www.heroku.com/pricing"],
}

JOB_HINT = (
    "Job research: PhilJobNet (https://philjobnet.gov.ph/) is fetchable. JobStreet and Indeed "
    "block page fetches, so use only what their search-result snippets say, and label it "
    "'snippet only' with the date shown. Never invent vacancy counts, employers or salaries."
)

_STOP = {"the", "and", "for", "with", "2026", "site", "current", "latest", "best", "free",
         "compare", "comparison", "research", "deep", "sources", "source", "please", "about",
         "what", "how", "are", "tiers", "tier", "small", "apps", "app", "web"}


def official_hints(query: str) -> str:
    q = query.lower()
    lines = []
    for name, urls in OFFICIAL_PAGES.items():
        if name in q:
            lines.append(f"- {name}: " + ", ".join(urls))
    out = ""
    if lines:
        out += "Official pages to fetch directly (prefer these over blog posts):\n" + "\n".join(lines) + "\n"
    if re.search(r"\b(job|jobs|hiring|vacanc\w*|trabaho|salary|peso|lucena|quezon)\b", q):
        out += JOB_HINT + "\n"
    return out


def simplify_query(query: str, max_words: int = 8) -> str:
    words = re.findall(r"[A-Za-z0-9.\-']+", query)
    keep = [w for w in words if w.lower() not in _STOP]
    return " ".join(keep[:max_words])


def bing_rss_search(query: str, max_results: int = 6, timeout: float = 12.0) -> list:
    """Fallback search through Bing's RSS feed. Returns [{title, body, href}]."""
    url = "https://www.bing.com/search?format=rss&q=" + quote_plus(query)
    resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                     headers={"User-Agent": "Mozilla/5.0 (compatible; ai-agent/1.0)"})
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    out = []
    for item in root.iter("item"):
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "body": (item.findtext("description") or "").strip(),
            "href": (item.findtext("link") or "").strip(),
            "date": (item.findtext("pubDate") or "").strip(),
        })
        if len(out) >= max_results:
            break
    return out


def _norm_url(u: str) -> str:
    u = u.strip().rstrip(".,);]>\"'")
    p = urlparse(u)
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
_MONEY_RE = re.compile(r"(?:\$|US\$|₱|PHP\s?|Php\s?)\s?\d[\d,]*(?:\.\d+)?")


def _money_key(tok: str) -> str:
    return re.sub(r"[^\d.]", "", tok).rstrip(".")


class ResearchLedger:
    """Per-turn research record: budgets in, evidence out."""

    def __init__(self, now=time.monotonic):
        self._now = now
        self.started = now()
        self.searches = 0
        self.fetches = 0
        self.empty_streak = 0
        self.refusals = 0
        self.steers = 0
        self.topic = ""
        self.pages = {}      # normalized url -> (url, text) for pages the model read
        self.snippets = {}   # normalized url -> (url, text) from search results

    def set_topic(self, text: str):
        self.topic = (text or "").lower()

    def pending_official(self) -> list:
        """Official pages for platforms the user named that were not fetched yet."""
        return [u for name, urls in OFFICIAL_PAGES.items() if name in self.topic
                for u in urls[:1] if _norm_url(u) not in self.pages]

    # ---- budgets -------------------------------------------------------
    def exhausted(self) -> bool:
        return (self.searches >= MAX_SEARCHES and self.fetches >= MAX_FETCHES) or \
            self._now() - self.started > MAX_RESEARCH_SECONDS or self.refusals >= 3

    def gate(self, tool: str, args: dict):
        """Return a refusal message when this call is over budget, else None."""
        if tool not in ("web_search", "fetch_page", "image_search"):
            return None
        done = ("Research budget reached. Do not call more search or fetch tools. Write the final "
                "answer now from the evidence already gathered, and mark anything not backed by a "
                "fetched page as 'unverified'.")
        if self._now() - self.started > MAX_RESEARCH_SECONDS:
            self.refusals += 1
            return "Research time limit reached. " + done
        if tool == "web_search":
            if self.searches >= MAX_SEARCHES:
                self.refusals += 1
                return f"Search limit ({MAX_SEARCHES}) reached. " + done
            if self.searches >= 1 and self.steers < 4:
                q = str((args or {}).get("query", "")).lower()
                todo = [u for name, urls in OFFICIAL_PAGES.items() if name in q
                        for u in urls if _norm_url(u) not in self.pages]
                if todo:
                    self.steers += 1
                    return ("Do not search for this. Fetch the official page directly with fetch_page "
                            "(one search costs ~13s, a fetch ~1s): " + ", ".join(todo[:3]))
            if self.empty_streak >= MAX_EMPTY_SEARCHES:
                self.refusals += 1
                return ("Search keeps returning nothing useful. Stop searching; fetch the official pages "
                        "directly with fetch_page, or report the missing evidence plainly.")
        if tool == "fetch_page":
            url = str((args or {}).get("url", ""))
            if _norm_url(url) in self.pages:
                return None  # cached, free
            pending = self.pending_official()
            host = urlparse(url).netloc.lower().removeprefix("www.")
            official_hosts = {urlparse(u).netloc.lower().removeprefix("www.")
                              for us in OFFICIAL_PAGES.values() for u in us}
            if pending and self.steers < 8 and not any(host == h or host.endswith("." + h) for h in official_hosts):
                self.steers += 1
                return ("Fetch the official pages for every platform the user named before any other "
                        "site. Still to fetch: " + ", ".join(pending[:5]))
            if self.fetches >= MAX_FETCHES:
                self.refusals += 1
                return f"Fetch limit ({MAX_FETCHES}) reached. " + done
        return None

    # ---- recording -----------------------------------------------------
    def record(self, tool: str, args: dict, result):
        out = str(result.get("output", "")) if isinstance(result, dict) else str(result)
        ok = isinstance(result, dict) and result.get("status") == "success"
        if tool == "web_search":
            self.searches += 1
            if ok and "URL:" in out:
                self.empty_streak = 0
                for block in out.split("### ")[1:]:
                    m = re.search(r"URL:\s*(\S+)", block)
                    if m:
                        self.snippets[_norm_url(m.group(1))] = (m.group(1), block)
            else:
                self.empty_streak += 1
        elif tool == "fetch_page":
            url = str((args or {}).get("url", ""))
            key = _norm_url(url)
            if key in self.pages:
                return
            self.fetches += 1
            if ok:
                self.pages[key] = (url, out)

    def cached_page(self, url: str):
        hit = self.pages.get(_norm_url(url))
        return hit[1] if hit else None

    # ---- audit ---------------------------------------------------------
    def has_evidence(self) -> bool:
        return bool(self.pages or self.snippets)

    def _all_text(self) -> str:
        return "\n".join(t for _, t in list(self.pages.values()) + list(self.snippets.values()))

    def audit(self, answer: str) -> list:
        """Deterministic issues: links no tool returned, figures no source contains."""
        issues = []
        known = set(self.pages) | set(self.snippets)
        seen = set()
        for u in _URL_RE.findall(answer):
            k = _norm_url(u)
            if k in seen:
                continue
            seen.add(k)
            if k not in known:
                issues.append(f"Link never returned by a tool this turn: {u.rstrip('.,);')}")
        if not self.has_evidence():
            issues.append("No search or fetch evidence was gathered, so no claim here is verified.")
            return issues
        corpus = self._all_text()
        corpus_keys = {_money_key(m) for m in _MONEY_RE.findall(corpus)}
        missing = []
        for tok in _MONEY_RE.findall(answer):
            key = _money_key(tok)
            if key and key not in corpus_keys and tok.strip() not in missing:
                missing.append(tok.strip())
        if missing:
            issues.append("Figures not found in any gathered source: " + ", ".join(missing[:10]))
        return issues

    def excerpts_for(self, answer: str, per_page: int = 1800, total: int = 12000) -> str:
        """Pick the source sentences that overlap most with the answer, per page."""
        words = {w for w in re.findall(r"[a-z0-9$₱.]{3,}", answer.lower())} - _STOP
        parts, used = [], 0
        for url, text in list(self.pages.values()) + list(self.snippets.values()):
            sents = re.split(r"(?<=[.!?])\s+|\n+", text)
            scored = []
            for i, s in enumerate(sents):
                sw = set(re.findall(r"[a-z0-9$₱.]{3,}", s.lower()))
                score = len(sw & words)
                if score >= 2:
                    scored.append((score, i, s.strip()))
            scored.sort(reverse=True)
            picked, size = [], 0
            for _, i, s in scored:
                if size + len(s) > per_page:
                    break
                picked.append((i, s))
                size += len(s)
            if not picked:
                continue
            body = "\n".join(s for _, s in sorted(picked))
            chunk = f"[{url}]\n{body}\n"
            if used + len(chunk) > total:
                break
            parts.append(chunk)
            used += len(chunk)
        return "\n".join(parts)


CLAIM_CHECK_SYSTEM = """You audit a research answer against the source text the tools actually returned.

For each concrete claim (price, free-tier limit, plan name, feature support, job count, date), decide:
- SUPPORTED: an excerpt below says it.
- CONTRADICTED: an excerpt says something different. Quote the excerpt.
- UNSUPPORTED: no excerpt mentions it.

Reply with EXACTLY `OK` if every concrete claim is SUPPORTED. Otherwise reply with one line per problem:
CONTRADICTED: <claim> | source says: <short quote>
UNSUPPORTED: <claim>
Max 8 lines. No other text. Only judge claims about the facts; ignore style. If an excerpt shows a plan named differently or a different price than the answer, that is CONTRADICTED."""


def parse_claim_check(verdict: str) -> list:
    lines = []
    for ln in (verdict or "").splitlines():
        ln = ln.strip()
        if re.match(r"(CONTRADICTED|UNSUPPORTED)\b", ln, re.I):
            lines.append(ln[:300])
    return lines[:8]


def footer(issues: list) -> str:
    body = "\n".join(f"- {i}" for i in issues[:10])
    return ("\n\n⚠️ **Hindi na-verify sa mga nakuhang source (huwag munang pagkatiwalaan):**\n"
            + body + "\n")


def looks_like_tool_call(text: str) -> bool:
    """True when a reply is a tool call written as text (invisible to the user)."""
    t = (text or "").strip()
    if not t:
        return False
    if re.search(r"<\s*/?\s*tool_call|<\|?python_tag|\bfunction_call\b", t, re.I):
        return len(re.sub(r"<[^>]*>|\{.*?\}", "", t, flags=re.S).strip()) < 200
    return bool(re.fullmatch(r"\s*(?:fetch_page|web_search)\s*[\(\{].*", t, re.S))
