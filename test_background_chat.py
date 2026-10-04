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


def test_idle_poll_never_clears_textbox_or_files():
    a = AIAgent(api_key='fake')
    a._ui_history = [{'role': 'assistant', 'content': 'done'}]
    a._ui_running = False
    result = app.poll_background_chat(a)
    # file_status, uploaded_files, msg, agent_state must be untouched no-op updates
    assert result[4] == app.gr.update()
    assert result[5] == app.gr.update()
    assert result[6] == app.gr.update()


def test_submit_clears_textbox_once():
    a = AIAgent(api_key='fake')
    def stream(*args):
        yield [{'role': 'assistant', 'content': 'x'}], 'm'
    with patch.object(app, 'chat_stream', stream):
        out = app.begin_background_chat('hi', [], [], a)
        time.sleep(.1)
    assert out[6] == app.gr.update(value="")
