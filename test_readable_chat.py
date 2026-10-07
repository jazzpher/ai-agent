from ui_design import CSS


def test_readable_chat_overrides_follow_legacy_rules():
    tail=CSS.split('/* Readable conversation sizing.')[1]
    assert 'font-size:16px !important' in tail
    assert 'line-height:1.75 !important' in tail
    assert 'min-height:58dvh !important' in tail
    assert 'min-height:420px !important' in tail
    assert 'calc(100dvh - 104px)' in tail
    assert 'input, textarea, select, #file-status textarea {font-size:16px !important;}' in tail
    assert '.think-body' in tail and '.fc-pre' in tail
