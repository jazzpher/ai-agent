import time
from unittest.mock import patch
import app
from agent import AIAgent


def test_worker_survives_poll_and_does_not_replay():
    a = AIAgent(api_key='fake')
    calls = []
    def stream(*args):
        calls.append(1)
        yield [{'role':'assistant', 'content':'working'}], 'metrics'
        time.sleep(.1)
        yield [{'role':'assistant', 'content':'finished'}], 'metrics'
    with patch.object(app, 'chat_stream', stream):
        app.begin_background_chat('test', [], [], a)
        app.begin_background_chat('test', [], [], a)
        app.poll_background_chat(a)
        time.sleep(.2)
        result = app.poll_background_chat(a)
        assert calls == [1]
        assert result[0][-1]['content'] == 'finished'
        assert not a._ui_running


def test_worker_errors_retained_for_reconnect():
    a = AIAgent(api_key='fake')
    def stream(*args):
        raise ValueError('fake failure')
        yield
    with patch.object(app, 'chat_stream', stream):
        app.begin_background_chat('test', [], [], a)
        time.sleep(.1)
    assert 'fake failure' in app.poll_background_chat(a)[0][-1]['content']
