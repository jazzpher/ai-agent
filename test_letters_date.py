from letters import resolve_date_text, today_text


def test_empty_uses_today():
    assert resolve_date_text(None) == today_text()
    assert resolve_date_text("  ") == today_text()


def test_stale_model_date_replaced_with_today():
    assert resolve_date_text("September 27, 2025") == today_text()


def test_future_or_recent_date_kept():
    assert resolve_date_text("December 25, 2099") == "December 25, 2099"


def test_unparseable_text_kept():
    assert resolve_date_text("the 5th of next month") == "the 5th of next month"
from letters import resolve_date_text, today_text


def test_system_prompt_has_today(tmp_path):
    import agent
    a = agent.AIAgent()
    assert today_text() in a.system_prompt


def test_load_files_only_never_raises():
    import app
    assert app.load_files_only() is None or isinstance(app.load_files_only(), list)
