"""Presentation-only assets for the responsive Gradio workspace."""
HEADER = '''<header class="workspace-header">
<div class="brand"><span class="brand-mark" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M5 18V6l7 6 7-6v12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span><div><strong>AI Agent</strong><span>YOUR PERSONAL WORKSPACE</span></div></div>
<div class="header-note">Think clearly. Make things happen.</div></header>'''
EMPTY_CHAT = '''<div class="empty-chat"><span class="eyebrow">A LITTLE HELP, A LOT OF POSSIBILITY</span><h1>What are we working on?</h1><p>Ask a question, build something, or bring a file.<br>We will take it one step at a time.</p><div class="empty-tags"><span>Write &amp; explore</span><span>Code &amp; create</span><span>Read your files</span></div></div>'''
CSS = '''

:root { color-scheme: light dark; }
.gradio-container {
 --ws-bg:#f3f4ef; --ws-surface:#ffffff; --ws-panel:#fafbf8; --ws-soft:#eef3ec;
 --ws-ink:#183e38; --ws-muted:#52645a; --ws-line:#dce2d8;
 --ws-accent:#244e40; --ws-accent-hover:#183b30; --ws-on-accent:#ffffff;
 --ws-warning-bg:#fff7e5; --ws-warning-ink:#614720; --ws-warning-line:#e9d7ad; --ws-focus:#3c7763;
 color:var(--ws-ink);
}
.dark .gradio-container, .gradio-container.dark {
 --ws-bg:#111b17; --ws-surface:#192720; --ws-panel:#1e2e26; --ws-soft:#294237;
 --ws-ink:#edf5ee; --ws-muted:#b3c6b9; --ws-line:#3b5145;
 --ws-accent:#a9d6bc; --ws-accent-hover:#c4e6d1; --ws-on-accent:#122b20;
 --ws-warning-bg:#382c19; --ws-warning-ink:#ffe0a3; --ws-warning-line:#695434; --ws-focus:#a9d6bc;
}
body { background:#f3f4ef; }
@media (prefers-color-scheme:dark) { body { background:#111b17; } }
.gradio-container { max-width: 1180px !important; margin: auto !important; padding: 0 28px 20px !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important; }
.workspace-header { display:flex; align-items:center; justify-content:space-between; padding:24px 0 22px; }
.brand {display:flex; align-items:center; gap:12px; color:var(--ws-ink); }
.brand-mark { width:40px; height:40px; background:var(--ws-soft); border-radius:12px; display:grid; place-items:center; }
.brand strong { display:block; font-size:20px; line-height:24px; font-weight:650; letter-spacing:-.5px; }
.brand span:not(.brand-mark) { display:block; margin-top:3px; font-size:9px; letter-spacing:1.5px; color:var(--ws-muted); }
.header-note {color:var(--ws-muted); font-size:12px;}
#workspace-tabs > .tab-nav { border-bottom:1px solid var(--ws-line); gap:22px; padding:0 0 10px; margin-bottom:16px; }
#workspace-tabs > .tab-nav button {font-size:14px; color:var(--ws-muted); border:0; padding:8px 0; border-radius:0; background:transparent; min-width:0;}
#workspace-tabs > .tab-nav button.selected {color:var(--ws-ink); font-weight:650; box-shadow:0 2px var(--ws-ink);}
#workspace-tabs > .tabitem {padding:0; border:0; background:transparent;}
#chat-workspace {height:calc(100dvh - 193px); min-height:500px; gap:0; background:var(--ws-surface); border:1px solid var(--ws-line); border-radius:18px; overflow:hidden; box-shadow:0 8px 28px #173e3805; }
#conversation-heading { padding:16px 22px; border-bottom:1px solid var(--ws-line); }
#conversation-heading p {display:flex; justify-content:space-between; align-items:center; font-size:13px; color:var(--ws-muted); margin:0; }
#conversation-heading strong {color:var(--ws-ink); font-size:14px; font-weight:600;}
#agent-chat { flex:1; min-height:160px !important; height:0 !important; border:0; background:var(--ws-surface); border-radius:0; }
#agent-chat .message {font-size:14px; line-height:1.6;}
#agent-chat .message.user {background:var(--ws-soft); color:var(--ws-ink); border-radius:16px 16px 4px 16px;}
#agent-chat .message.bot {background:var(--ws-panel); border:1px solid var(--ws-line); border-radius:16px 16px 16px 4px;}
#agent-chat pre {max-width:100%; overflow-x:auto; white-space:pre;}
#agent-chat .prose {overflow-wrap:anywhere;}
.empty-chat {text-align:center; padding:30px 18px; color:var(--ws-ink);}
.eyebrow {font-size:9px; font-weight:600; letter-spacing:1.6px; color:var(--ws-muted);}
.empty-chat h1 {font-size:34px; letter-spacing:-1.2px; font-weight:500; line-height:1.2; margin:16px 0 12px; color:var(--ws-ink);}
.empty-chat p {font-size:14px; line-height:1.75; color:var(--ws-muted);}
.empty-tags {display:flex; justify-content:center; gap:8px; margin-top:25px; flex-wrap:wrap;}
.empty-tags span {font-size:11px; border:1px solid var(--ws-line); padding:7px 11px; border-radius:20px; color:var(--ws-muted);}
#composer { flex:none; position:sticky; bottom:0; background:var(--ws-surface); padding:12px 18px; border-top:1px solid var(--ws-line); gap:8px; }
#compose-row {gap:10px; align-items:stretch; flex-wrap:nowrap;}
#message-input {flex:1; min-width:0 !important;}
#message-input textarea {border:1px solid var(--ws-line); border-radius:12px; padding:12px 14px; background:var(--ws-panel); font-size:14px; line-height:21px; max-height:130px;}
#send-button, #stop-button {flex:none; min-width:76px !important; width:76px; border-radius:12px; font-size:14px; min-height:48px;}
#send-button {background:var(--ws-accent); color:var(--ws-on-accent); border:1px solid var(--ws-accent);}
#send-button:hover {background:var(--ws-accent-hover);}
#attachment-row {align-items:center; flex-wrap:nowrap; gap:10px;}
#upload-button, #clear-button {flex:none; width:auto; min-width:0 !important; padding:6px 10px; background:transparent; border:0; box-shadow:none; font-size:12px; color:var(--ws-muted); min-height:34px;}
#file-status {flex:1; min-width:0 !important; border:0; background:transparent; box-shadow:none;}
#file-status textarea {background:transparent; border:0; padding:0; font-size:11px; color:var(--ws-muted); height:28px; min-height:28px;}
#examples {width:100%; border:0; background:var(--ws-panel); border-radius:10px; padding:6px 12px; box-shadow:none;}
#examples input {font-size:12px;}
#examples .wrap {border:0; background:transparent; box-shadow:none;}
#approval-banner {flex:none; max-height:24dvh; overflow-y:auto; background:var(--ws-warning-bg); color:var(--ws-warning-ink); border-bottom:1px solid var(--ws-warning-line); padding:14px 20px; }
#approval-actions {flex:none; margin:0; padding:8px 20px; background:var(--ws-warning-bg); gap:10px;}
#approval-actions button {min-height:44px;}
#settings-panel, #session-panel {background:var(--ws-surface); border:1px solid var(--ws-line); border-radius:18px; padding:24px;}
#settings-panel h2, #session-panel h2 {font-size:24px; font-weight:500; letter-spacing:-.6px; color:var(--ws-ink);}
#settings-panel .block, #session-panel .block {box-shadow:none;}
#provider-settings {border:1px solid var(--ws-line); border-radius:12px;}
.provider-slot {border:1px solid var(--ws-line) !important; border-radius:10px !important; margin-bottom:8px;}
#render-notice {font-size:12px; background:var(--ws-warning-bg); padding:12px 16px; border-radius:10px; margin-bottom:12px;}
button, input, textarea {font-family:inherit;}
button:focus-visible, input:focus-visible, textarea:focus-visible {outline:2px solid var(--ws-focus) !important; outline-offset:3px;}
footer {display:none !important;}
@media (max-width:640px) {
 .gradio-container {padding:0 12px 12px !important;}
 .workspace-header {padding:18px 2px 16px;}
 .header-note {display:none;}
 .brand strong {font-size:18px;}
 #workspace-tabs > .tab-nav {margin-bottom:12px; gap:24px;}
 #chat-workspace {height:calc(100dvh - 175px); min-height:390px; border-radius:14px;}
 #conversation-heading {padding:13px 16px;}
 #conversation-heading p {font-size:11px;}
 .empty-chat {padding:18px 10px;}
 .empty-chat h1 {font-size:27px; letter-spacing:-.9px;}
 .empty-chat p {font-size:13px;}
 .eyebrow {font-size:8px; letter-spacing:1.2px;}
 .empty-tags {gap:5px; margin-top:18px;}
 .empty-tags span {font-size:10px; padding:6px 8px;}
 #composer {padding:10px 12px max(10px, env(safe-area-inset-bottom));}
 #compose-row {gap:8px;}
 #message-input textarea {font-size:14px !important; line-height:21px; padding:10px;}
 #agent-chat .message {font-size:13px; line-height:1.6;}
 #send-button, #stop-button {width:65px; min-width:65px !important;}
 #settings-panel, #session-panel {padding:18px 14px; border-radius:14px;}
 input, textarea {font-size:16px !important;}
 #approval-banner {padding:10px 14px;}
 #approval-actions {padding:8px 14px;}
}
@media (prefers-reduced-motion:reduce) { * {scroll-behavior:auto !important; transition:none !important;} }

.gradio-container {width:100% !important; background:var(--ws-bg) !important;}
.gradio-container > .main, .gradio-container .contain {width:100%;}
.gradio-container .app {padding:0 !important;}
#composer {flex:0 0 auto !important;}
#conversation-heading, #approval-banner, #approval-actions {flex:0 0 auto !important;}
#agent-chat {border:0 !important;}
#file-status {border:0 !important; overflow:hidden;}
#file-status textarea {font-size:11px !important; white-space:nowrap; overflow:hidden; resize:none;}
#message-input {border:0 !important;}
#examples {border:0 !important;}
#examples input::placeholder {color:var(--ws-muted); opacity:1;}
#agent-chat .wrapper, #agent-chat .bubble-wrap {height:100%; min-height:0;}
#composer > .row {flex:0 0 auto !important;}

#approval-actions {flex-wrap:nowrap;}
#approval-actions button {min-width:0 !important; flex:1;}
#approval-actions button.primary {background:var(--ws-accent); color:var(--ws-on-accent);}
#approval-banner pre {white-space:pre-wrap; overflow-wrap:anywhere;}
#message-input textarea {white-space:pre-wrap; overflow-wrap:anywhere;}
#file-status textarea {text-overflow:ellipsis;}
#agent-chat .bubble-wrap {overflow-y:auto;}

#approval-banner pre, #approval-banner code {white-space:pre-wrap !important; overflow-wrap:anywhere !important; word-break:break-word;}
#agent-chat .wrapper {overflow:hidden; display:flex; min-height:0;}
#agent-chat .bubble-wrap {flex:1; min-height:0;}

/* Gradio's native prose and controls share the palette; no white-on-white inheritance. */
#agent-chat .message, #agent-chat .prose, #message-input textarea, #examples input,
#settings-panel, #session-panel {color:var(--ws-ink);}
#agent-chat .placeholder {background:var(--ws-surface);}
#agent-chat pre, #agent-chat code, #agent-chat code span {color:var(--ws-ink);}
#agent-chat .brand strong, .workspace-header .brand strong {color:var(--ws-ink);}
#message-input textarea::placeholder, #examples input::placeholder {color:var(--ws-muted); opacity:1;}
#workspace-tabs button[role="tab"] {color:var(--ws-muted);}
#workspace-tabs button[role="tab"][aria-selected="true"] {color:var(--ws-ink); border-color:var(--ws-ink);}
#approval-actions button.primary, #send-button {color:var(--ws-on-accent);}
#render-notice, #approval-banner, #approval-banner .prose {color:var(--ws-warning-ink);}
#message-input textarea {font-size:14px !important; line-height:21px !important; height:64px !important; min-height:64px !important; max-height:64px !important; padding:10px !important; box-sizing:border-box; white-space:pre-wrap; overflow-wrap:anywhere; overflow-y:auto;}
#agent-chat .message .prose, #agent-chat .message .prose p, #agent-chat .message .prose li {font-size:14px !important; line-height:1.6 !important;}
@media (max-width:640px) {
 #agent-chat .message .prose, #agent-chat .message .prose p, #agent-chat .message .prose li {font-size:13px !important;}
 #examples input {font-size:12px !important; text-overflow:ellipsis; white-space:nowrap;}
 #send-button, #stop-button {align-self:flex-end; height:48px;}
}
/* Wake-page visual rhythm: soft cards, clear type and restrained borders. */
.workspace-header {padding:24px 0;}
.brand-mark {background:var(--ws-soft);}
#chat-workspace, #settings-panel, #session-panel {box-shadow:0 8px 28px #173e3808;}
#conversation-heading {padding:18px 22px;}
#agent-chat .message {box-shadow:none;}
#agent-chat .message.bot {border-color:var(--ws-line);}
#composer {padding:16px 18px;}
#message-input textarea {border:1px solid var(--ws-line) !important; outline:none; box-shadow:none;}
#message-input:focus-within {box-shadow:none;}
#message-input .input-container {box-shadow:none !important; border:0 !important;}
#examples {padding:0; background:transparent;}
#examples .wrap {border:1px solid var(--ws-line) !important; border-radius:12px; padding:10px 12px; background:var(--ws-panel);}
#examples .wrap-inner, #examples .secondary-wrap {border:0 !important; box-shadow:none; background:transparent !important;}
#settings-panel > .block, #session-panel > .block {border:0; background:transparent; padding:0;}
#settings-panel .accordion, #session-panel .accordion {border-radius:12px;}
#provider-settings {padding:14px;}
#settings-panel button, #session-panel button {border-radius:12px;}
.empty-chat {padding:40px 20px;}
.empty-chat h1 {font-size:30px; letter-spacing:-1px;}
.empty-tags span {background:var(--ws-panel);}
@media (max-width:640px) {
 .workspace-header {padding:18px 2px 16px;}
 #conversation-heading {padding:14px 16px;}
 #composer {padding:12px 12px max(12px, env(safe-area-inset-bottom));}
 .empty-chat {padding:26px 12px;}
 .empty-chat h1 {font-size:26px;}
}

/* Mobile: give the conversation the screen. Files and starter prompts live in a collapsed accordion. */
#extras {border:0 !important; background:transparent !important; box-shadow:none !important;}
#extras > button, #extras .label-wrap {font-size:12px; color:var(--ws-muted); padding:2px 4px !important; min-height:0 !important;}
@media (max-width:640px) {
 .workspace-header {padding:10px 2px 8px;}
 .brand-mark {width:32px; height:32px;}
 .brand span:not(.brand-mark) {display:none;}
 #workspace-tabs > .tab-nav {margin-bottom:8px; padding-bottom:4px;}
 #chat-workspace {height:calc(100dvh - 156px); min-height:420px;}
 #conversation-heading {display:none;}
 #agent-chat {min-height:240px !important;}
 #composer {padding:8px 10px max(8px, env(safe-area-inset-bottom)); gap:2px;}
 #message-input textarea {height:48px !important; min-height:48px !important; max-height:96px !important;}
 #attachment-row {min-height:0;}
}

'''


def workspace_theme():
    """Pair every native Gradio surface with readable light/dark foregrounds.

    The native auth screen uses this theme too; Blocks CSS only covers the app.
    """
    import gradio as gr
    pairs = {
        "body_background_fill": ("#f3f4ef", "#111b17"),
        "background_fill_primary": ("#ffffff", "#192720"),
        "background_fill_secondary": ("#fafbf8", "#1e2e26"),
        "block_background_fill": ("#ffffff", "#192720"),
        "panel_background_fill": ("#fafbf8", "#1e2e26"),
        "input_background_fill": ("#fafbf8", "#1e2e26"),
        "input_background_fill_hover": ("#eef3ec", "#294237"),
        "body_text_color": ("#183e38", "#edf5ee"),
        "body_text_color_subdued": ("#52645a", "#b3c6b9"),
        "block_label_text_color": ("#183e38", "#edf5ee"),
        "block_title_text_color": ("#183e38", "#edf5ee"),
        "block_info_text_color": ("#52645a", "#b3c6b9"),
        "input_placeholder_color": ("#52645a", "#b3c6b9"),
        "accordion_text_color": ("#183e38", "#edf5ee"),
        "border_color_primary": ("#dce2d8", "#3b5145"),
        "block_border_color": ("#dce2d8", "#3b5145"),
        "input_border_color": ("#dce2d8", "#3b5145"),
        "code_background_fill": ("#eef3ec", "#294237"),
        "link_text_color": ("#244e40", "#a9d6bc"),
        "button_primary_background_fill": ("#244e40", "#a9d6bc"),
        "button_primary_background_fill_hover": ("#183b30", "#c4e6d1"),
        "button_primary_text_color": ("#ffffff", "#122b20"),
        "button_primary_text_color_hover": ("#ffffff", "#122b20"),
        "button_primary_border_color": ("#244e40", "#a9d6bc"),
        "button_secondary_background_fill": ("#eef3ec", "#294237"),
        "button_secondary_text_color": ("#183e38", "#edf5ee"),
        "button_secondary_text_color_hover": ("#183e38", "#edf5ee"),
    }
    values = {key + suffix: pair[i] for key, pair in pairs.items()
              for i, suffix in enumerate(("", "_dark"))}
    return gr.themes.Base(primary_hue="emerald", neutral_hue="stone", font=["system-ui"]).set(**values)


LOGIN_STYLE = """
<style>
.gradio-container:has(#workspace-login) {background:#f3f4ef !important; max-width:none !important; padding:0 !important;}
.dark .gradio-container:has(#workspace-login), .gradio-container.dark:has(#workspace-login) {background:#111b17 !important;}
.gradio-container main:has(#workspace-login) {padding:0 !important;}
.gradio-container main > .wrap:has(#workspace-login) {
 width:100%; background:transparent;
 min-height:100dvh; display:flex; align-items:center; justify-content:center;
 padding:24px 16px calc(24px + env(safe-area-inset-bottom));
}
.gradio-container .wrap:has(#workspace-login) > .panel {
 width:100%; max-width:440px; min-width:0 !important; gap:16px;
 background:var(--background-fill-primary); border:1px solid var(--block-border-color);
 border-radius:18px; padding:28px; box-shadow:0 8px 28px #173e3808;
}
.gradio-container .wrap:has(#workspace-login) h2 {display:none;}
.gradio-container .wrap:has(#workspace-login) p.auth {margin:0; padding:0;}
#workspace-login {display:block; color:var(--body-text-color); text-align:left;}
#workspace-login .login-brand {display:flex; align-items:center; gap:12px; margin-bottom:24px;}
#workspace-login .login-mark {width:40px; height:40px; border-radius:12px; background:var(--background-fill-secondary); display:grid; place-items:center; flex:none;}
#workspace-login strong {display:block; font-size:20px; line-height:24px; font-weight:650; letter-spacing:-.5px;}
#workspace-login .login-sub {display:block; font-size:9px; letter-spacing:1.5px; color:var(--body-text-color-subdued); margin-top:3px;}
#workspace-login .login-eyebrow {display:block; font-size:9px; letter-spacing:1.6px; color:var(--body-text-color-subdued); font-weight:600;}
#workspace-login .login-title {display:block; font-size:30px; line-height:1.2; font-weight:500; letter-spacing:-1px; margin:14px 0 10px;}
#workspace-login .login-lead {display:block; font-size:14px; line-height:1.7; color:var(--body-text-color-subdued); margin-bottom:4px;}
.gradio-container .wrap:has(#workspace-login) .form {border:0; box-shadow:none; background:transparent; gap:16px;}
.gradio-container .wrap:has(#workspace-login) .block {border:0 !important; padding:0 !important; background:transparent;}
.gradio-container .wrap:has(#workspace-login) input {border:1px solid var(--input-border-color); border-radius:12px; min-height:46px; padding:12px 14px; font-size:16px;}
.gradio-container .wrap:has(#workspace-login) button {min-height:46px; border-radius:12px; margin-top:4px;}
.gradio-container .wrap:has(#workspace-login) .creds {color:var(--error-text-color); background:var(--error-background-fill); padding:12px; border-radius:10px; font-size:14px;}
.gradio-container .wrap:has(#workspace-login) input:focus-visible,
.gradio-container .wrap:has(#workspace-login) button:focus-visible {outline:2px solid var(--link-text-color); outline-offset:3px;}
@media (max-width:480px) {
 .gradio-container .wrap:has(#workspace-login) > .panel {padding:24px 20px; border-radius:16px;}
 #workspace-login .login-title {font-size:26px;}
}
</style>
"""
LOGIN_MESSAGE = LOGIN_STYLE + '''<span id="workspace-login">
<span class="login-brand"><span class="login-mark" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M5 18V6l7 6 7-6v12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span><strong>AI Agent</strong><span class="login-sub">YOUR PERSONAL WORKSPACE</span></span></span>
<span class="login-eyebrow">A SPACE OF YOUR OWN</span>
<span class="login-title">Welcome back.</span>
<span class="login-lead">Sign in to your private workspace.<br>Ask, create, and take the next step.</span>
</span>'''
