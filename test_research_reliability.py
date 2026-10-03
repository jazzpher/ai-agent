"""Bounded recovery and fresh, tool-grounded research. No live API keys."""
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import agent as agent_mod
from api_retry import is_transient
from test_agent_loop import FakeClient, make_agent, run, stream_chunks


def test_provider_message_and_connection_errors_are_transient():
    assert is_transient(RuntimeError('Service temporarily overloaded'))
    assert is_transient(httpx.RemoteProtocolError('peer closed connection'))
    assert not is_transient(ValueError('invalid request'))


def test_current_date_in_analysis_and_short_scope():
    client = FakeClient([], sync=['a short plan'])
    a = make_agent(client)
    a._analyze_task(client, 'research hosting', '')
    kw = client.calls[0]
    assert agent_mod._today_text() in kw['messages'][0]['content']
    assert kw['max_tokens'] == 800
    assert '3-6 sources' in kw['messages'][0]['content']
    patch.stopall()


def test_prompt_refreshes_each_turn():
    a = make_agent(FakeClient([('text', 'hello')]))
    with patch.object(agent_mod, '_today_text', return_value='October 3, 2026'):
        run(a, 'hello')
    assert 'October 3, 2026' in a.messages[0]['content']
    patch.stopall()


def test_research_requires_search_first():
    client = FakeClient([('tool', 'web_search', {'query': 'Lucena jobs 2026'}),
                         ('text', 'No verified findings available')], sync=['KEEP', 'PASS'])
    a = make_agent(client)
    with patch.dict(agent_mod.TOOL_FUNCTIONS, {'web_search': lambda **kw: {'status': 'success', 'output': 'No results'}}):
        run(a, 'research Lucena job market')
    calls = [c for c in client.calls if c.get('stream')]
    assert calls[0]['tool_choice']['function']['name'] == 'web_search'
    assert calls[1]['tool_choice'] == 'auto'
    patch.stopall()


def test_reasoning_only_overload_retries_without_executing_partial_calls():
    def broken():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            reasoning_content='thinking', content=None, tool_calls=None), finish_reason=None)])
        raise RuntimeError('Service temporarily overloaded')
    class Client(FakeClient):
        def create(self, **kw):
            if kw.get('stream') and not self.calls:
                self.calls.append(kw)
                return broken()
            return super().create(**kw)
    a = make_agent(Client([('text', 'recovered')]))
    assert 'recovered' in run(a)
    patch.stopall()


def test_partial_answer_not_replayed():
    def broken():
        yield next(stream_chunks(('text', 'partial answer')))
        raise RuntimeError('Service temporarily overloaded')
    client = FakeClient([])
    client.create = lambda **kw: broken()
    client.chat.completions.create = client.create
    a = make_agent(client)
    out = run(a)
    assert 'Stream error' in out
    assert 'retrying' not in out
    patch.stopall()
