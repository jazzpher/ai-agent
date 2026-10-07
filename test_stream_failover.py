from unittest.mock import Mock, patch
from openai import APIError
import httpx
import agent as agent_mod
import api_retry
from test_agent_loop import FakeClient, make_agent, run
from test_response_status import chunk


def exhausted():
    return APIError('Resource exhausted', httpx.Request('GET', 'https://example.invalid'),
                    body={'code': 'RESOURCE_EXHAUSTED'})


def setup_agent(first, second):
    a = make_agent(first)
    a._providers = [dict(model='first-550b',base_url='https://first',api_key='fake',preset='First'),
                    dict(model='second-550b',base_url='https://second',api_key='fake',preset='Second')]
    a._provider_idx = 0
    a._apply_provider(a._providers[0])
    a._get_client = lambda: [first, second][a._provider_idx]
    return a


def test_sse_resource_exhausted_is_transient_not_auth():
    assert api_retry.is_transient(exhausted())
    from test_api_retry import status
    assert not api_retry.is_transient(status(401))
    assert not api_retry.is_transient(APIError('bad key', httpx.Request('GET','https://x'), body={'code':'invalid_api_key'}))


def test_stream_exhaustion_falls_back_after_bounded_retries():
    first = FakeClient([])
    def broken(**kw):
        def stream():
            raise exhausted()
            yield
        return stream()
    first.chat.completions.create = Mock(side_effect=broken)
    second = FakeClient([('text', 'recovered')])
    a = setup_agent(first, second)
    out = run(a)
    assert 'recovered' in out
    assert a.provider_name == 'Second'
    assert first.chat.completions.create.call_count == 3
    assert 'fallback' in out


def test_partial_output_never_replayed_on_exhaustion():
    first = FakeClient([])
    def broken(**kw):
        def stream():
            yield chunk(content='partial output')
            raise exhausted()
        return stream()
    first.chat.completions.create = Mock(side_effect=broken)
    second = FakeClient([('text','must not run')])
    a = setup_agent(first, second)
    out = run(a)
    assert 'partial output' in out
    assert first.chat.completions.create.call_count == 1
    assert not second.calls
    assert 'Stream error' in out


def test_all_providers_exhausted_readable_not_raw_exception():
    first = FakeClient([])
    def broken(**kw):
        def stream():
            raise exhausted()
            yield
        return stream()
    first.chat.completions.create = Mock(side_effect=broken)
    a = make_agent(first)
    a._providers=[]
    out = run(a)
    assert 'limit' in out.lower()
    assert 'Resource exhausted' not in out


def test_real_openai_sse_error_routes_by_body_code():
    # Real SDK translates HTTP-200 SSE error into APIError, not RateLimitError.
    from openai import OpenAI
    def transport(request):
        return httpx.Response(200, headers={'content-type':'text/event-stream'},
                              text='data: {"error":{"message":"Resource exhausted","code":429}}\n\n')
    client = OpenAI(api_key='fake',base_url='https://fake.invalid',max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    a = setup_agent(client, FakeClient([('text','real SDK fallback worked')]))
    assert 'real SDK fallback worked' in run(a)


def test_prior_tool_not_replayed_when_later_stream_exhausted():
    first = FakeClient([('tool','list_files',{})])
    real = first.create
    def flaky(**kw):
        if kw.get('stream') and not first.script:
            def stream():
                raise exhausted()
                yield
            return stream()
        return real(**kw)
    first.chat.completions.create = flaky
    second = FakeClient([('text','done after tool')])
    a = setup_agent(first, second)
    calls=[]
    with patch.object(a, '_execute_tool', side_effect=lambda n,args: calls.append(n) or {'status':'success','output':'no files'}):
        out=run(a)
    assert calls == ['list_files']
    assert 'done after tool' in out
    assert len([m for m in a.messages if m.get('role')=='tool'])==1
