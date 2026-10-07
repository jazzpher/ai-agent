from ui_design import CSS


def test_readable_chat_overrides_follow_legacy_rules():
    tail=CSS.split('/* Readable conversation sizing.')[1]
    assert 'font-size:16px !important' in tail
    assert 'font-size:14px !important; line-height:1.6 !important' in tail
    assert 'min-height:58dvh !important' in tail
    assert 'min-height:420px !important' in tail
    assert 'calc(100dvh - 104px)' in tail
    assert 'input, textarea, select, #file-status textarea {font-size:16px !important;}' in tail
    assert '.think-body' in tail and '.fc-pre' in tail


def test_compact_plan_fonts_do_not_inherit_large_prose():
    tail=CSS.split('/* Readable conversation sizing.')[1]
    assert '.task-plan .plan-row {font-size:13px !important; line-height:1.55 !important;}' in tail
    assert '.task-plan .plan-title {font-size:13px;}' in tail
    assert '.task-plan .wl > summary {font-size:13px;}' in tail
    assert '.plan-label {display:block; font-size:12px;' in CSS
    assert '#agent-chat .wl, #agent-chat .fc {font-size:13px;' in tail


def test_mobile_10px_preserves_desktop_and_inputs():
    mobile=CSS.split('/* User-selected compact mobile text;')[1]
    assert 'font-size:10px !important; line-height:1.6 !important;' in mobile
    assert '.task-plan .plan-label' in mobile and '.task-plan .plan-row' in mobile
    assert '.wl > summary' in mobile and '.message .prose p' in mobile
    assert '#message-input' not in mobile
    assert '#agent-chat .task-plan .plan-title {font-size:13px;}' in CSS


def test_compact_horizontal_message_insets():
    assert 'padding:14px 6px !important' in CSS
    assert 'padding:13px 4px !important' in CSS
    assert 'margin-left:4px !important; margin-right:4px !important' in CSS
    assert 'max-width:calc(100% - 8px) !important' in CSS
