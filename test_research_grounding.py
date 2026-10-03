"""Budgets, evidence ledger and claim-to-source checks. No network, no keys."""
from unittest.mock import patch
import agent as agent_mod
import research_guard as rg
import tools
from test_agent_loop import FakeClient, make_agent, run

SEARCH_OK = {'status': 'success', 'output': '### Railway Pricing\nFree $0 with $1 monthly credits. Hobby $5.\nURL: https://railway.com/pricing\n'}
PAGE = {'status': 'success', 'output': 'Source: https://railway.com/pricing\nStatus: 200\n\nFree $0 includes $1 of usage credits each month. Hobby plan costs $5 per month.'}


def test_search_cap_is_enforced():
    led = rg.ResearchLedger()
    for _ in range(rg.MAX_SEARCHES):
        assert led.gate('web_search', {}) is None
        led.record('web_search', {}, SEARCH_OK)
    msg = led.gate('web_search', {})
    assert msg and 'limit' in msg.lower()


def test_fetch_cap_and_cache_are_free():
    led = rg.ResearchLedger()
    led.record('fetch_page', {'url': 'https://railway.com/pricing'}, PAGE)
    assert led.gate('fetch_page', {'url': 'https://www.railway.com/pricing/'}) is None
    assert led.fetches == 1
    for i in range(rg.MAX_FETCHES):
        led.record('fetch_page', {'url': f'https://x{i}.com'}, PAGE)
    assert 'Fetch limit' in led.gate('fetch_page', {'url': 'https://new.example'})


def test_empty_search_streak_stops_searching():
    led = rg.ResearchLedger()
    for _ in range(rg.MAX_EMPTY_SEARCHES):
        led.record('web_search', {}, {'status': 'error', 'output': 'No relevant results'})
    assert 'fetch' in led.gate('web_search', {}).lower()
    assert led.gate('fetch_page', {'url': 'https://a.com'}) is None


def test_time_budget():
    t = [0.0]
    led = rg.ResearchLedger(now=lambda: t[0])
    t[0] = rg.MAX_RESEARCH_SECONDS + 1
    assert 'time limit' in led.gate('fetch_page', {'url': 'https://a.com'}).lower()
    assert led.gate('run_python', {}) is None


def test_audit_flags_unseen_link_and_unsourced_figure():
    led = rg.ResearchLedger()
    led.record('web_search', {}, SEARCH_OK)
    led.record('fetch_page', {'url': 'https://railway.com/pricing'}, PAGE)
    good = 'Railway: Free $0 with $1 credits, Hobby $5. https://railway.com/pricing'
    assert led.audit(good) == []
    bad = 'Railway Hobby $7. See https://blog.example.com/railway'
    issues = led.audit(bad)
    assert any('blog.example.com' in i for i in issues)
    assert any('$7' in i for i in issues)


def test_audit_without_evidence():
    assert 'No search or fetch evidence' in rg.ResearchLedger().audit('Hosting costs $5')[0]


def test_excerpts_pick_relevant_sentences_and_parse_verdict():
    led = rg.ResearchLedger()
    led.record('fetch_page', {'url': 'https://railway.com/pricing'}, PAGE)
    ex = led.excerpts_for('Railway Hobby plan costs $5 per month')
    assert 'Hobby plan costs $5' in ex and 'railway.com/pricing' in ex
    got = rg.parse_claim_check('UNSUPPORTED: Netlify supports Python\nsome chatter\nCONTRADICTED: Railway trial | source says: Free $0')
    assert len(got) == 2
    assert rg.parse_claim_check('OK') == []


def test_official_hints_and_query_simplifying():
    h = rg.official_hints('compare Render, Railway and Netlify free tiers')
    assert 'render.com/pricing' in h and 'railway.com/pricing' in h
    assert 'job_search' in rg.official_hints('job openings in Lucena Quezon')
    assert rg.official_hints('cat pictures') == ''
    assert len(rg.simplify_query('compare the best free hosting tiers for small web apps in 2026 with sources').split()) <= 8


def test_web_search_falls_back_to_bing_and_adds_hints():
    bing = [{'title': 'Render pricing', 'body': 'Render free tier', 'href': 'https://render.com/pricing', 'date': ''}]
    with patch.object(tools, 'HAS_DDG', False), patch.object(rg, 'bing_rss_search', return_value=bing):
        r = tools.web_search('Render free tier')
    assert r['status'] == 'success' and 'URL: https://render.com/pricing' in r['output']
    assert 'Official pages to fetch directly' in r['output']


def test_web_search_empty_is_error_not_evidence():
    with patch.object(tools, 'HAS_DDG', False), patch.object(rg, 'bing_rss_search', return_value=[]):
        r = tools.web_search('zzzz qqqq')
    assert r['status'] == 'error' and 'Do not treat' in r['output']


def test_wrong_claim_gets_rewritten_once_then_marked():
    bad = 'Railway: Trial then $9 Hobby. https://railway.com/pricing'
    client = FakeClient([('tool', 'fetch_page', {'url': 'https://railway.com/pricing'}),
                         ('text', bad), ('text', bad)],
                        sync=['CONTRADICTED: Railway trial | source says: Free $0', 'OK', 'PASS'])
    a = make_agent(client)
    with patch.dict(agent_mod.TOOL_FUNCTIONS, {'fetch_page': lambda **kw: PAGE}), \
            patch.object(agent_mod, 'verify_enabled', return_value=False):
        out = run(a, 'research Railway pricing')
    assert 'Source check failed' in str([m.get('content') for m in a.messages])
    assert '$9' in out and 'Hindi na-verify' in out or 'Tugma' in out
    patch.stopall()


def test_budget_exhaustion_removes_research_tools_only():
    client = FakeClient([('tool', 'web_search', {'query': 'x'}), ('text', 'No verified findings, unverified.')], sync=['OK'])
    a = make_agent(client)
    orig = rg.ResearchLedger.exhausted
    with patch.object(rg.ResearchLedger, 'exhausted', lambda self: self.searches >= 1), \
            patch.dict(agent_mod.TOOL_FUNCTIONS, {'web_search': lambda **kw: SEARCH_OK}), \
            patch.object(agent_mod, 'verify_enabled', return_value=False):
        run(a, 'research Railway pricing')
    streams = [c for c in client.calls if c.get('stream')]
    n0 = {t['function']['name'] for t in streams[0]['tools']}
    n1 = {t['function']['name'] for t in streams[1]['tools']}
    assert 'web_search' in n0 and 'web_search' not in n1 and 'fetch_page' not in n1 and n1
    patch.stopall()


def test_search_for_known_platform_is_steered_to_official_page():
    led = rg.ResearchLedger()
    led.record('web_search', {}, SEARCH_OK)
    msg = led.gate('web_search', {'query': 'Netlify free tier limits 2026'})
    assert msg and 'netlify.com/pricing' in msg
    led.record('fetch_page', {'url': 'https://www.netlify.com/pricing/'}, PAGE)
    assert led.gate('web_search', {'query': 'Netlify free tier limits 2026'}) is None
    assert led.gate('web_search', {'query': 'unknownhost pricing'}) is None


def test_pseudo_tool_call_text_detected():
    assert rg.looks_like_tool_call('<tool_call>{"name":"fetch_page","arguments":{"url":"https://fly.io/pricing"}}</tool_call>')
    assert rg.looks_like_tool_call('fetch_page({"url": "https://fly.io/pricing"})')
    assert not rg.looks_like_tool_call('| Render | Free $0 | https://render.com/pricing |')
    assert not rg.looks_like_tool_call('')


def test_pseudo_call_reply_is_retried_and_tools_stay_for_other_work():
    pseudo = '<tool_call>{"name":"fetch_page","arguments":{"url":"https://fly.io/pricing"}}</tool_call>'
    client = FakeClient([('text', pseudo), ('text', 'Fly.io: unverified, not fetched.')], sync=['OK'])
    a = make_agent(client)
    with patch.object(rg.ResearchLedger, 'exhausted', lambda self: True), \
            patch.object(agent_mod, 'verify_enabled', return_value=False):
        out = run(a, 'research Fly.io pricing')
    assert 'Fly.io: unverified' in out
    streams = [c for c in client.calls if c.get('stream')]
    names = [t['function']['name'] for t in streams[0]['tools']]
    assert 'web_search' not in names and 'fetch_page' not in names and names
    patch.stopall()


def test_fetch_order_official_pages_first():
    led = rg.ResearchLedger()
    led.set_topic('compare Render, Railway and Netlify')
    msg = led.gate('fetch_page', {'url': 'https://kuberns.com/blogs/railway-free-tier/'})
    assert msg and 'render.com/pricing' in msg
    assert led.gate('fetch_page', {'url': 'https://docs.railway.com/deployments/serverless'}) is None
    for u in ('https://render.com/pricing', 'https://railway.com/pricing', 'https://www.netlify.com/pricing/'):
        led.record('fetch_page', {'url': u}, PAGE)
    assert led.gate('fetch_page', {'url': 'https://kuberns.com/blog'}) is None
    assert rg.MAX_FETCHES >= 14
