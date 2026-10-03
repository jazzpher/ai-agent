"""Job search: local vs nearby split, honest labels, no network."""
import job_search as js
import research_guard as rg

JS = [
    {"source": "JobStreet", "title": "Auditor", "company": "St. Peter", "location": "Lucena City, Quezon",
     "date": "2026-09-30 (3d ago)", "salary": "not shown", "url": "https://ph.jobstreet.com/job/1", "teaser": ""},
    {"source": "JobStreet", "title": "Cook", "company": "Foo", "location": "Lipa City, Batangas",
     "date": "2026-10-03 (0d ago)", "salary": "not shown", "url": "https://ph.jobstreet.com/job/2", "teaser": ""},
    {"source": "JobStreet", "title": "QC job", "company": "Bar", "location": "Quezon City, Metro Manila",
     "date": "2026-10-03", "salary": "not shown", "url": "https://ph.jobstreet.com/job/3", "teaser": ""},
]


def test_quezon_city_is_not_quezon_province():
    assert js._is_local("Lucena City, Quezon") and js._is_local("Pagbilao, Quezon")
    assert not js._is_local("Quezon City, Metro Manila") and not js._is_local("Quezon, Metro Manila")
    assert not js._is_local("Lipa City, Batangas")


def test_search_groups_and_labels():
    r = js.search(fetchers={"jobstreet": lambda *a: JS, "kalibrr": lambda *a: []})
    out = r["output"]
    assert r["status"] == "success"
    assert out.index("LOCAL") < out.index("NEARBY")
    assert "Location: Lucena City, Quezon" in out and "Posted: 2026-09-30" in out
    assert "1 listed in Lucena/Quezon" in out and "2 nearby" in out
    assert "Indeed and Glassdoor were not checked" in out


def test_all_sources_down_says_no_evidence():
    def boom(*a):
        raise RuntimeError("blocked")
    r = js.search(fetchers={"jobstreet": boom, "kalibrr": boom})
    assert r["status"] == "error" and "do not invent" in r["output"]


def test_ledger_records_job_urls_and_caps():
    led = rg.ResearchLedger()
    r = js.search(fetchers={"jobstreet": lambda *a: JS, "kalibrr": lambda *a: []})
    assert led.gate("job_search", {}) is None
    led.record("job_search", {}, r)
    assert "https://ph.jobstreet.com/job/1" in led.snippets["ph.jobstreet.com/job/1"][0]
    assert led.audit("Auditor https://ph.jobstreet.com/job/1") == []
    assert any("never returned" in i for i in led.audit("see https://ph.jobstreet.com/job/999"))
    for _ in range(rg.MAX_JOB_SEARCHES):
        led.record("job_search", {}, r)
    assert "limit" in led.gate("job_search", {}).lower()
