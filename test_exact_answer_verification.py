from test_agent_loop import FakeClient, make_agent
from unittest.mock import patch


def test_verifier_receives_complete_goal_and_trailing_punctuation():
    c=FakeClient([],sync=['PASS']);a=make_agent(c)
    a._turn_evidence=[('write_file','success','created')]
    goal='Make a plan and write a file. '+('Keep the file safe. '*20)+ 'Final answer exactly: Note checked successfully.'
    assert a._verify_final(c,goal,'Note checked successfully.')=='PASS'
    text=c.calls[0]['messages'][1]['content']
    assert goal in text
    assert 'Final answer exactly: Note checked successfully.' in text
    assert 'Never shorten, trim, paraphrase' in c.calls[0]['messages'][0]['content']
    patch.stopall()


def test_verifier_preserves_quoted_exact_string():
    c=FakeClient([],sync=['PASS']);a=make_agent(c);a._turn_evidence=[]
    goal='Return exactly "Done!  File checked: OK." including punctuation.'
    a._verify_final(c,goal,'Done!  File checked: OK.')
    text=c.calls[0]['messages'][1]['content']
    assert 'Done!  File checked: OK.' in text
    assert 'including punctuation' in text
    patch.stopall()


def test_execution_keeps_full_goal_not_200_char_summary():
    c=FakeClient([('text','Note checked successfully.')]);a=make_agent(c)
    goal=('Detailed instruction. '*15)+'Final answer exactly: Note checked successfully.'
    list(a.chat_stream(goal))
    assert a._current_goal==goal
    patch.stopall()
