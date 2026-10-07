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
