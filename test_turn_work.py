from types import SimpleNamespace
import worklog
import thinking


def test_live_work_is_open_and_expandable():
    a=SimpleNamespace(_display_final=None)
    thinking.add(a, 'Reasoning <safe>')
    out=worklog.render_turn('Planning\n\nWorking',a)
    assert '<details class="turn-work" open>' in out
    assert 'Reasoning &lt;safe&gt;' in out
    assert 'Planning' in out


def test_final_outside_collapsed_work_and_keeps_history():
    a=SimpleNamespace(_display_final='The answer')
    thinking.add(a,'reasoning retained');thinking.finish(a)
    text='Plan and commands\n\nThe answer\n\nChecking my answer\n\n**Mga file na ginawa**\nfile.txt\n🤖 Sumagot: model'
    out=worklog.render_turn(text,a)
    hidden,shown=out.split('</details>\n\n',1) if not a.reasoning_blocks else out.rsplit('</details>\n\n',1)
    assert '<details class="turn-work">' in hidden
    assert 'reasoning retained' in hidden and 'Checking my answer' in hidden
    assert 'The answer' in shown and 'file.txt' in shown
    assert 'Plan and commands' not in shown
    assert text.startswith('Plan and commands')


def test_no_accepted_final_never_hides_work():
    out=worklog.render_turn('Cancelled or failed',SimpleNamespace(_display_final=None))
    assert ' open>' in out and 'Cancelled or failed' in out


def test_plain_messages_keep_code_container():
    from ui_design import CSS
    assert '#agent-chat .message, #agent-chat .bubble-wrap' in CSS
    assert 'border:0 !important; border-radius:0 !important;' in CSS
    assert '#agent-chat .message pre, #agent-chat .wl-box {border:1px solid' in CSS


def test_toggle_choice_recorded_before_stream_hydration():
    from ui_design import PAGE_JS
    assert 'workChoices.set(detailKey(detail), !detail.open)' in PAGE_JS
    assert '}, true);' in PAGE_JS


def test_final_attribution_on_separate_paragraph():
    out=worklog.render_turn('Work\n\nAnswer\n\n🤖 Sumagot: model',SimpleNamespace(_display_final='Answer'))
    assert 'Answer\n\n🤖 Sumagot:' in out
