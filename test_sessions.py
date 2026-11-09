"""Durable sessions: synthetic state, no provider calls."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import cli
from sessions import SessionStore, CONTEXT_FIELDS
from workflow import Workflow


def make(root):
    a = SimpleNamespace(session_id='abc123', model='fake', messages=[{'role':'user','content':'task'}],
                        workflow=Workflow(root,'plan'), api_key='DO-NOT-SAVE', cancel_requested=False,
                        context=SimpleNamespace(step_count=3, task_goal='task', task_steps=['read'],
                        current_step=0, task_status='planning', facts=['synthetic']))
    a.use_model = lambda m:setattr(a,'model',m)
    return a


def test_roundtrip_workspace_plan_and_context(tmp_path):
    a=make(tmp_path); store=SessionStore(tmp_path,tmp_path/'store')
    a.workflow.pending_plan='implement x, run tests'
    a.workflow.baselines={'x.py':'before'}
    store.save(a)
    a.messages=[]
    store.restore(a,'abc123')
    assert a.messages[0]['content']=='task'
    assert a.workflow.mode=='plan'
    assert a.workflow.pending_plan=='implement x, run tests'
    assert a.workflow.baselines=={'x.py':'before'}
    assert a.context.step_count==3
    assert a.context.task_status=='planning'
    assert 'DO-NOT-SAVE' not in store.path('abc123').read_text()
    assert 'api_key' not in store.path('abc123').read_text()


def test_wrong_workspace_and_traversal(tmp_path):
    store=SessionStore(tmp_path,tmp_path/'store'); store.save(make(tmp_path))
    with pytest.raises(ValueError,match='different workspace'):
        SessionStore(tmp_path/'other',tmp_path/'store').read('abc123')
    for bad in ('../oops','/tmp/a','', 'a.json', 'x'*65):
        with pytest.raises(ValueError):store.path(bad)


def test_atomic_failed_replace_keeps_previous(tmp_path):
    a=make(tmp_path);store=SessionStore(tmp_path,tmp_path/'store');store.save(a)
    previous=store.path(a.session_id).read_bytes()
    a.messages.append({'role':'assistant','content':'new'})
    with patch('sessions.os.replace',side_effect=OSError('disk error')):
        with pytest.raises(OSError):store.save(a)
    assert store.path(a.session_id).read_bytes()==previous
    assert not list(store.directory.glob('.checkpoint-*'))


def test_corrupt_and_unsafe_baseline_fail(tmp_path):
    a=make(tmp_path);store=SessionStore(tmp_path,tmp_path/'store');store.save(a)
    data=store.read(a.session_id);data['workflow']['baselines']={'../outside':'x'}
    store.path(a.session_id).write_text(json.dumps(data))
    with pytest.raises(ValueError):store.restore(a,a.session_id)
    store.path(a.session_id).write_text('{')
    assert store.listing()==[]
    with pytest.raises(ValueError):store.restore(a,a.session_id)


def test_incomplete_calls_removed_not_replayed(tmp_path):
    a=make(tmp_path);store=SessionStore(tmp_path,tmp_path/'store')
    a.messages += [{'role':'assistant','content':None,'tool_calls':[
        {'id':'call1','function':{'name':'run_bash','arguments':'{}'}},
        {'id':'call2','function':{'name':'run_bash','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':'call1','content':'done'}]
    store.save(a);store.restore(a,a.session_id)
    assert a.messages[2]['content']=='done'
    assert 'outcome unknown' in a.messages[3]['content']
    assert a.messages[3]['tool_call_id']=='call2'


def test_resume_resets_auto_consent(tmp_path):
    a=make(tmp_path);store=SessionStore(tmp_path,tmp_path/'store');store.save(a)
    out=io.StringIO();s=cli.Session(a,cli.Renderer(out),auto_yes=True,out=out,store=store)
    s.command('/resume abc123')
    assert not s.auto_yes
    assert 'resumed abc123' in out.getvalue()
    s.command('/sessions'); assert 'fake' in out.getvalue()


def test_sessions_list_without_provider(tmp_path):
    with patch('cli.SessionStore',create=True): pass
    with patch('sessions.Path.home',return_value=tmp_path),patch('cli.build_agent',side_effect=AssertionError('must not build')):
        assert cli.main(['-C',str(tmp_path),'--sessions'])==0


def test_malformed_shapes_rejected_without_changing_agent(tmp_path):
    a=make(tmp_path); store=SessionStore(tmp_path,tmp_path/'store');store.save(a)
    original=store.read('abc123')
    for field,bad in [('workflow',[]),('metrics',[]),('messages',[{'role':'assistant','tool_calls':[{}]}])]:
        data=dict(original);data[field]=bad or ['bad']
        store.path('abc123').write_text(json.dumps(data))
        before=a.messages
        with pytest.raises(ValueError):store.restore(a,'abc123')
        assert a.messages is before


def test_checkpoint_failure_before_execution_fails_closed(tmp_path):
    import agent as am
    from test_agent_loop import FakeClient,make_agent
    a=make_agent(FakeClient([('tool','write_file',{'path':'x','content':'bad'})]))
    a.workflow=Workflow(tmp_path)
    out=io.StringIO();store=SessionStore(tmp_path,tmp_path/'store')
    sess=cli.Session(a,cli.Renderer(out),auto_yes=True,out=out,store=store)
    with patch.object(store,'save',side_effect=OSError('disk full')):
        with pytest.raises(OSError,match='disk full'):sess.turn('write x')
    assert not (tmp_path/'x').exists()
    patch.stopall()
