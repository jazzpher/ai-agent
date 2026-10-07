from unittest.mock import patch
from test_agent_loop import FakeClient, make_agent


def test_step_checker_sees_completed_prior_steps():
    c=FakeClient([],sync=['KEEP']);a=make_agent(c)
    a._turn_evidence=[('run_python','success','phase 1 done'),('run_python','success','phase 2 done'),('run_python','success','phase 3 done')]
    assert a._evaluate_action(c,'run three phases','phase 3','phase 3 done')=='KEEP'
    text=c.calls[0]['messages'][1]['content']
    assert 'phase 1 done' in text and 'phase 2 done' in text
    assert 'Do not request repeats' in text
    patch.stopall()
