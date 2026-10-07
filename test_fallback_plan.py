import agentic
import plan_ui
from test_agent_loop import make_agent, FakeClient


def test_one_named_fallback():
    request="Make a one-step public plan named 'Check note'. write_file x with content 'OTHER'."
    assert agentic.parse_plan(plan_ui.fallback_analysis(request)) == ['Check note']


def test_two_titles_preserve_order():
    request="Make a public plan with exactly two short titles: 'Create test note' and 'Read and check note'."
    assert plan_ui.requested_steps(request)==['Create test note','Read and check note']
    wrong='**Restate:** test\n\n**Plan:**\n1. Generic\n2. Another\n3. Report\n\n**Success criteria:** Verify'
    fixed=plan_ui.align_analysis(wrong,request)
    assert agentic.parse_plan(fixed)==['Create test note','Read and check note']
    assert '**Success criteria:** Verify' in fixed


def test_count_only_and_no_count_are_honest():
    assert len(agentic.parse_plan(plan_ui.fallback_analysis('Do this in 4 steps.')))==4
    assert agentic.parse_plan(plan_ui.fallback_analysis('Create a document'))==['Complete requested task']
    assert plan_ui.requested_steps("Write 'one step' in a document") is None


def test_failed_planner_uses_owner_named_step():
    a=make_agent(FakeClient([]))
    a._get_client=lambda:None
    result=a._analyze_task(None,"Make a one-step plan named 'Check note'.",'')
    assert agentic.parse_plan(result)==['Check note']


def test_count_only_replaces_wrong_count_but_keeps_matching_outcomes():
    wrong='**Plan:**\n1. Generic\n2. Another\n3. Report'
    fixed=plan_ui.align_analysis(wrong,'Make a plan with exactly one step.')
    assert agentic.parse_plan(fixed)==['Complete requested step 1']
    good='**Plan:**\n1. Build document'
    assert plan_ui.align_analysis(good,'Make a plan with exactly one step.')==good
