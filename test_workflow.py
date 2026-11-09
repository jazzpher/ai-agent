"""Enforced planning and direct-edit review; no network or real keys."""
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import agent as agent_mod
import cli
from workflow import Workflow
from test_agent_loop import FakeClient, make_agent


def test_plan_dispatch_allowlist():
    with tempfile.TemporaryDirectory() as root:
        a = make_agent(FakeClient([]))
        a.workflow = Workflow(root, 'plan')
        for tool in ('run_bash', 'run_python', 'pip_install', 'write_file', 'edit_file',
                     'delegate_tasks', 'remember', 'view_file', 'unknown'):
            assert a._execute_tool(tool, {})['status'] == 'blocked'
        assert a.workflow.block('read_file') is None
    patch.stopall()


def test_preview_diff_and_ambiguous_edit():
    with tempfile.TemporaryDirectory() as root:
        p = Path(root, 'a.py'); p.write_text('x = 1\n')
        w = Workflow(root)
        diff = w.preview('edit_file', {'path':'a.py', 'old_string':'1','new_string':'2'})
        assert '-x = 1' in diff and '+x = 2' in diff
        assert p.read_text() == 'x = 1\n'
        p.write_text('x = 2\n')
        assert '+x = 2' in w.diff()
        p.write_text('a a')
        with pytest.raises(ValueError):
            w.preview('edit_file', {'path':'a.py', 'old_string':'a','new_string':'b'})
        with pytest.raises(ValueError):
            w.preview('write_file', {'path':'../outside','content':'bad'})
        with pytest.raises(ValueError):
            w.preview('edit_file', {'path':'missing','old_string':'a','new_string':'b'})


def test_invalid_preview_fails_closed():
    with tempfile.TemporaryDirectory() as root:
        a = make_agent(FakeClient([])); a.workflow = Workflow(root)
        executed = []
        with patch.dict(agent_mod.TOOL_FUNCTIONS, {'write_file':lambda **kw:executed.append(kw)}):
            assert a._execute_tool('write_file', {'path':'../outside','content':'bad'})['status'] == 'blocked'
        assert not executed
    patch.stopall()


def test_plan_loop_blocks_model_write_even_auto_yes():
    with tempfile.TemporaryDirectory() as root:
        a = make_agent(FakeClient([('tool','write_file',{'path':'oops','content':'bad'}),
                                   ('text','Design: inspect files, implement, test.')]))
        a.workflow = Workflow(root, 'plan')
        out = io.StringIO()
        s = cli.Session(a, cli.Renderer(out), auto_yes=True, stdin=io.StringIO(''), out=out)
        s.turn('make a plan')
        assert not Path(root,'oops').exists()
        assert a.workflow.pending_plan
        assert a.workflow.mode == 'plan'
        assert 'read-only' in out.getvalue().lower()
    patch.stopall()


def test_approval_requires_completed_plan():
    with tempfile.TemporaryDirectory() as root:
        a = make_agent(FakeClient([])); a.workflow = Workflow(root,'plan')
        out = io.StringIO(); s = cli.Session(a,cli.Renderer(out),out=out)
        s.command('/approve')
        assert a.workflow.mode == 'plan'
        a.workflow.pending_plan = 'Build x, run tests'
        with patch.object(s,'turn') as turn:
            s.command('/approve'); turn.assert_called_once()
        assert a.workflow.mode == 'build'
        assert a.workflow.approved_plan == 'Build x, run tests'
    patch.stopall()
