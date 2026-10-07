from plan_ui import Plan,render,replace

def test_progress_not_guessed():
    p=Plan(['Read source','Build file','Verify'])
    p.select(1)
    assert p.steps[0]['status']=='running'
    p.select(2)
    assert p.steps[0]['status']=='running'  # a later command does not prove prior completion
    assert p.update(1,'done')
    assert p.steps[0]['status']=='done'

def test_invalid_updates_leave_state():
    p=Plan(['One'])
    assert not p.update(0,'done')
    assert not p.update(2,'done')
    assert not p.update(1,'invented')
    assert p.steps[0]['status']=='pending'

def test_snapshot_and_escaping():
    p=Plan(['Read <script>'])
    raw=p.marker()+'\n[[planstep:1]]\noutput'
    p.update(1,'done')
    text=render(replace(raw,p))
    assert 'Step 1 · Done' in text
    assert '&lt;script&gt;' in text
    assert 'Commands for step 1' in text
    assert '[[plan' not in text
    assert text.endswith('output')

def test_idempotent_and_malformed():
    p=Plan(['Task'])
    text=render(p.marker())
    assert render(text)==text
    assert render('[[plan:bad=]]')==''

def test_commands_nested_under_matching_rows():
    import worklog
    p=Plan(['Read','Write']);p.update(1,'done');p.update(2,'running')
    raw=p.marker()+"\n\n🔧 **Action:** `read_file`\n\n[[planstep:1]]\n✅ `read_file` — 0.01s — first\n\n---\n\n🔧 **Action:** `write_file`\n\n[[planstep:2]]\n✅ `write_file` — 0.01s — second\n\n---\n"
    rendered=worklog.render(raw)
    first=rendered.index('Step 1 · Done');second=rendered.index('Step 2 · In progress')
    assert first < rendered.index('first') < second < rendered.index('second')
    assert rendered.count('class="wl"')==2

def test_multiple_calls_and_plan_event_same_step():
    import worklog
    p=Plan(['Build'])
    raw=p.marker()+"\n\n🔧 **Action:** x\n\n[[planstep:1]]\n✅ `run_python` — 1.00s — one\n[[planstep:1]]\n✅ `run_bash` — 1.00s — two\n[[planstep:1]]\n✅ `update_plan` — 0.00s — Verified\n\n---\n"
    result=worklog.render(raw)
    assert 'Plan updates <b>1</b>' in result
    assert result.index('Step 1')<result.index('one')<result.index('two')

def test_agent_plan_snapshots_and_metadata_stripping():
    from unittest.mock import patch
    from test_agent_loop import FakeClient,make_agent
    import agent as module
    client=FakeClient([('tool','write_file',{'path':'x.txt','content':'ok','plan_step':1}),('tool','update_plan',{'step':1,'status':'done','note':'File checked'}),('text','Done')])
    a=make_agent(client);calls=[]
    with patch.object(a,'_should_analyze',return_value=True),patch.object(a,'_analyze_task',return_value='Plan:\n1. Build file\n2. Verify output\nSuccess criteria: file'),patch.object(a,'_evaluate_action',return_value='KEEP'),patch.dict(module.TOOL_FUNCTIONS,{'write_file':lambda **kw:calls.append(kw) or {'status':'success','output':'wrote'}}),patch.object(a,'_verify_final',return_value='PASS'):
        snapshots=list(a.chat_stream('build file please'))
    assert any('[[plan:' in s for s in snapshots)
    assert any('Step 1 · In progress' in render(s) for s in snapshots)
    assert a._public_plan.steps[0]['status']=='done'
    assert a._public_plan.steps[1]['status']=='pending'
    assert 'plan_step' not in calls[0]
    patch.stopall()

def test_failed_and_cancelled_step_never_claimed_done():
    from unittest.mock import patch
    from test_agent_loop import FakeClient,make_agent
    import agent as module
    client=FakeClient([('tool','write_file',{'path':'x.txt','content':'ok','plan_step':1}),('tool','update_plan',{'step':1,'status':'blocked','note':'Write failed'}),('text','Blocked')])
    a=make_agent(client)
    with patch.object(a,'_should_analyze',return_value=True),patch.object(a,'_analyze_task',return_value='Plan:\n1. Build file\nSuccess criteria: file'),patch.object(a,'_evaluate_action',return_value='KEEP'),patch.dict(module.TOOL_FUNCTIONS,{'write_file':lambda **kw:{'status':'error','output':'denied'}}),patch.object(a,'_verify_final',return_value='PASS'):
        list(a.chat_stream('build file please'))
    assert a._public_plan.steps[0]['status']=='blocked'
    a._public_plan=Plan(['Task']);a._public_plan.select(1);a.cancel_requested=True
    assert a._public_plan.steps[0]['status']!='done'
    patch.stopall()

def test_cancelled_execution_snapshot_blocks_active_step():
    from unittest.mock import patch
    from test_agent_loop import FakeClient,make_agent
    import agent as module
    a=make_agent(FakeClient([('tool','write_file',{'path':'x','content':'x','plan_step':1})]))
    def cancel(**kwargs):
        a.cancel_requested=True
        return {'status':'error','output':'Cancelled'}
    with patch.object(a,'_should_analyze',return_value=True),patch.object(a,'_analyze_task',return_value='Plan:\n1. Build\nSuccess criteria: file'),patch.dict(module.TOOL_FUNCTIONS,{'write_file':cancel}):
        snapshots=list(a.chat_stream('build a file'))
    assert a._public_plan.steps[0]['status']=='blocked'
    assert 'Step 1 · Blocked' in render(snapshots[-1])
    patch.stopall()

def test_failed_command_cannot_be_marked_done():
    from agent import AIAgent
    a=AIAgent(api_key='fake');a._public_plan=Plan(['Task']);a._plan_result_status={1:'error'}
    assert a._execute_tool('update_plan',{'step':1,'status':'done'})['status']=='error'
    assert a._public_plan.steps[0]['status']=='pending'
