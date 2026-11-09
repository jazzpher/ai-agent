"""Local, workspace-bound checkpoints. No provider credentials or approvals saved."""
from __future__ import annotations
import json
import os
import re
import tempfile
import time
import copy
import math
from pathlib import Path

ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
CONTEXT_FIELDS = ('step_count', 'task_goal', 'task_steps', 'current_step', 'task_status', 'facts')
METRICS = ('total_prompt_tokens', 'total_completion_tokens', 'tool_call_count', 'errors', 'compactions')

class SessionStore:
    def __init__(self, workspace, directory=None):
        self.workspace = str(Path(workspace).resolve())
        self.directory = Path(directory or Path.home() / '.ai-agent' / 'sessions')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, sid):
        if not isinstance(sid, str) or not ID.fullmatch(sid):
            raise ValueError('Invalid session ID')
        return self.directory / (sid + '.json')

    def save(self, agent):
        w = getattr(agent, 'workflow', None)
        c = agent.context
        data = dict(version=1, id=agent.session_id, workspace=self.workspace,
                    updated=time.time(), model=agent.model, messages=agent.messages,
                    checks=getattr(getattr(agent, "checks", None), "receipts", []),
                    metrics={k:getattr(agent,k,0) for k in METRICS},
                    context={k:getattr(c,k) for k in CONTEXT_FIELDS},
                    workflow=({'mode':w.mode, 'approved_plan':w.approved_plan,
                               'pending_plan':w.pending_plan, 'baselines':w.baselines} if w else None))
        # Atomic replace preserves the previous checkpoint on a failed write.
        target = self.path(agent.session_id)
        fd, name = tempfile.mkstemp(dir=self.directory, prefix='.checkpoint-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, target)
        finally:
            if os.path.exists(name): os.unlink(name)
        return agent.session_id

    def read(self, sid):
        try:
            path = self.path(sid)
            if path.stat().st_size > 20_000_000: raise ValueError('Session too large')
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('Session unavailable or corrupt') from exc
        if not isinstance(data,dict) or data.get('version') != 1 or data.get('id') != sid:
            raise ValueError('Invalid session format')
        if data.get('workspace') != self.workspace:
            raise ValueError('Session belongs to a different workspace')
        return data

    def listing(self):
        found = []
        for p in self.directory.glob('*.json'):
            try:
                data = self.read(p.stem)
                if type(data.get('updated')) not in (int,float) or not math.isfinite(data['updated']): continue
                found.append({'id':data['id'], 'updated':data['updated'], 'model':data.get('model','')})
            except (ValueError, KeyError, TypeError): pass
        return sorted(found, key=lambda d:d['updated'], reverse=True)

    def restore(self, agent, sid):
        from context_manager import ContextManager
        from workflow import Workflow
        data = self.read(sid)
        messages = data.get('messages')
        if not isinstance(messages,list) or any(not isinstance(m,dict) or m.get('role') not in {'system','user','assistant','tool'} for m in messages):
            raise ValueError('Invalid conversation')
        # Preserve completed receipts. Unknown outcomes are not replayed: answer
        # the unfinished calls explicitly so the next model must inspect first.
        messages = copy.deepcopy(messages)
        pending = {}
        repaired = []
        for m in messages:
            calls = m.get('tool_calls') or []
            if not isinstance(calls,list): raise ValueError('Invalid tool calls')
            if calls:
                if pending or m['role'] != 'assistant': raise ValueError('Invalid tool exchange')
                for t in calls:
                    if not isinstance(t,dict) or not isinstance(t.get('id'),str) or not isinstance(t.get('function'),dict):
                        raise ValueError('Invalid tool call')
                    if t['id'] in pending: raise ValueError('Duplicate tool call')
                    pending[t['id']] = t
            elif m['role'] != 'tool' and pending:
                raise ValueError('Incomplete tool exchange in conversation')
            if m['role'] == 'tool':
                tid = m.get('tool_call_id')
                if tid not in pending: raise ValueError('Orphan tool result')
                del pending[tid]
            repaired.append(m)
        for tid in pending:
            repaired.append({'role':'tool','tool_call_id':tid,'content':
                'Interrupted: outcome unknown. Do not replay this action. Inspect current files/state before proposing any further action; ask if the outcome cannot be verified.'})
        if pending:
            repaired.append({'role':'assistant','content':
                'Session interrupted with unknown tool outcomes. Waiting for your instruction; inspect state before any retry.'})
        messages = repaired
        ctx_data = data.get('context') or {}
        if not isinstance(ctx_data,dict): raise ValueError('Invalid context')
        wf_data = data.get('workflow') or {}
        if not isinstance(wf_data,dict): raise ValueError('Invalid workflow')
        metrics = data.get('metrics') or {}
        if not isinstance(metrics,dict): raise ValueError('Invalid metrics')
        if not isinstance(data.get('model'),str): raise ValueError('Invalid model')
        if wf_data.get('mode', 'build') not in {'plan','build'}: raise ValueError('Invalid mode')
        baselines = wf_data.get('baselines',{})
        if not isinstance(baselines,dict) or any(not isinstance(k,str) or (v is not None and not isinstance(v,str)) for k,v in baselines.items()):
            raise ValueError('Invalid diff baseline')
        w = Workflow(self.workspace, wf_data.get('mode','build'))
        for k in baselines: w.path(k)  # reject outside workspace
        w.baselines = baselines
        w.pending_plan = str(wf_data.get('pending_plan',''))
        w.approved_plan = str(wf_data.get('approved_plan',''))
        c = ContextManager(sid)
        for k in CONTEXT_FIELDS:
            if k in ctx_data:
                expected = type(getattr(c,k))
                if type(ctx_data[k]) is not expected: raise ValueError('Invalid context field '+k)
                setattr(c,k,ctx_data[k])
        from verification import Checks
        checks = Checks(self.workspace)
        receipts = data.get('checks', [])
        if not isinstance(receipts, list) or len(receipts) > 20:
            raise ValueError('Invalid check receipts')
        for r in receipts:
            if (not isinstance(r, dict) or not isinstance(r.get('command'), str)
                    or type(r.get('passed')) is not bool
                    or not (r.get('returncode') is None or type(r.get('returncode')) is int)
                    or not (r.get('fingerprint') is None or isinstance(r.get('fingerprint'), str))):
                raise ValueError('Invalid check receipt')
        checks.receipts = receipts
        # Validate fully before changing the current agent.
        if data.get('model'): agent.use_model(data['model'])
        agent.session_id = sid
        agent.messages = messages
        agent.context = c
        agent.workflow = w
        agent.checks = checks
        agent._compact_floor = 0
        agent.cancel_requested = False
        for k,v in metrics.items():
            if k in METRICS and isinstance(v,(int,float)): setattr(agent,k,v)
        # Select the model from current providers; NEVER restore keys, URLs,
        # pending gate requests, subprocesses, or automatic consent.
        return data
