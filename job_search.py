"""Job listings from public job-board JSON endpoints, with source, location and date on every row.

JobStreet and Kalibrr serve search results as JSON; their HTML pages (and Indeed) are
blocked for plain fetches. Every row says where it came from and what the source itself
shows for location and posting date. Nothing here is guessed: a missing field is printed
as 'not shown'.
"""
import re
from datetime import datetime, timezone

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
JOBSTREET_API = "https://ph.jobstreet.com/api/jobsearch/v5/search"
KALIBRR_API = "https://www.kalibrr.com/kjs/job_board/search"
NEAR_REGIONS = ("quezon", "lucena", "calabarzon", "batangas", "laguna", "cavite", "rizal", "marinduque")


def _get(url, params, timeout=15.0):
    r = httpx.get(url, params=params, timeout=timeout, follow_redirects=True,
                  headers={"User-Agent": UA, "Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def _is_local(loc: str) -> bool:
    l = (loc or "").lower()
    if "metro manila" in l or "quezon city" in l:
        return False  # Quezon City is not Quezon province
    return "lucena" in l or "quezon" in l


def _date(iso: str) -> str:
    if not iso:
        return "not shown"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - d).days
        return f"{d.date().isoformat()} ({days}d ago)" if days >= 0 else d.date().isoformat()
    except Exception:
        return iso[:10]


def jobstreet(where: str, keywords: str = "", n: int = 20, get=_get) -> list:
    params = {"siteKey": "PH-Main", "where": where, "pageSize": min(max(n, 1), 30),
              "locale": "en-PH", "sortmode": "ListedDate"}
    if keywords:
        params["keywords"] = keywords
    data = get(JOBSTREET_API, params)
    rows = []
    for j in data.get("data", []):
        locs = [l.get("label", "") for l in j.get("locations", []) if l.get("label")]
        rows.append({
            "source": "JobStreet (ph.jobstreet.com search API)",
            "title": j.get("title", ""), "company": j.get("companyName") or (j.get("employer") or {}).get("name", ""),
            "location": ", ".join(locs) or "not shown", "date": _date(j.get("listingDate", "")),
            "salary": j.get("salaryLabel") or "not shown",
            "url": f"https://ph.jobstreet.com/job/{j.get('id')}",
            "teaser": (j.get("teaser") or "")[:200],
        })
    return rows


def kalibrr(keywords: str = "", n: int = 100, get=_get) -> list:
    data = get(KALIBRR_API, {"limit": n, "offset": 0, "text": keywords})
    rows = []
    for j in data.get("jobs", []):
        ac = (j.get("google_location") or {}).get("address_components") or {}
        city, region = ac.get("city", ""), ac.get("region", "")
        loc = ", ".join(x for x in (city, region) if x)
        if "metro manila" in loc.lower() or not any(k in loc.lower() for k in NEAR_REGIONS):
            continue
        sal = ""
        if j.get("salary_shown") and j.get("base_salary"):
            hi = f" - {j['maximum_salary']:,}" if j.get("maximum_salary") else ""
            sal = f"{j.get('salary_currency', 'PHP')} {j['base_salary']:,}{hi} per {j.get('salary_interval', 'month')}"
        code = (j.get("company") or {}).get("code") if isinstance(j.get("company"), dict) else ""
        rows.append({
            "source": "Kalibrr (kalibrr.com search API)", "title": j.get("name", ""),
            "company": j.get("company_name", ""), "location": loc,
            "date": _date(j.get("activation_date", "")), "salary": sal or "not shown",
            "url": f"https://www.kalibrr.com/c/{code}/jobs/{j.get('id')}/{j.get('slug', '')}" if code else "",
            "teaser": "",
        })
    return rows


def search(where: str = "Lucena City Quezon", keywords: str = "", max_results: int = 15, fetchers=None) -> dict:
    """Return {'status','output'}. fetchers: dict name->callable for tests."""
    fetchers = fetchers or {"jobstreet": jobstreet, "kalibrr": kalibrr}
    rows, notes = [], []
    try:
        rows += fetchers["jobstreet"](where, keywords, 30)
    except Exception as e:
        notes.append(f"JobStreet not reachable ({type(e).__name__}: {str(e)[:80]}).")
    try:
        rows += fetchers["kalibrr"](keywords, 100)
    except Exception as e:
        notes.append(f"Kalibrr not reachable ({type(e).__name__}: {str(e)[:80]}).")
    if not rows:
        return {"status": "error", "output": "No job listings retrieved. " + " ".join(notes) +
                " Say plainly that no job evidence was found; do not invent vacancies. "
                "PhilJobNet (https://philjobnet.gov.ph/) is a manual fallback, and Indeed blocks fetches."}
    local = [r for r in rows if _is_local(r["location"])]
    near = [r for r in rows if not _is_local(r["location"])]
    key = lambda r: r["date"]
    local.sort(key=key, reverse=True)
    near.sort(key=key, reverse=True)
    out = [f"Job listings for '{where}'" + (f" keywords '{keywords}'" if keywords else "") +
           f". {len(local)} listed in Lucena/Quezon province, {len(near)} nearby (other towns; "
           "JobStreet searches ~50 km around the place). Location and date are exactly what the "
           "source shows; 'not shown' means the source did not say. Indeed and Glassdoor were not "
           "checked (they block automated access)."]
    if notes:
        out.append("Notes: " + " ".join(notes))
    for label, group in (("LOCAL (Lucena / Quezon province)", local), ("NEARBY (not Lucena/Quezon)", near)):
        for r in group[:max_results if label.startswith("LOCAL") else max(3, max_results // 2)]:
            out.append(f"### {r['title']} - {r['company']}\n[{label}] Source: {r['source']}\n"
                       f"Location: {r['location']}\nPosted: {r['date']}\nSalary: {r['salary']}\n"
                       + (f"Note: {r['teaser']}\n" if r["teaser"] else "") + f"URL: {r['url']}\n")
    return {"status": "success", "output": "\n".join(out)}
