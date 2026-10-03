"""Presentation-only assets for the responsive Gradio workspace."""
HEADER = '''<header class="workspace-header">
<div class="brand"><span class="brand-mark" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M5 18V6l7 6 7-6v12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span><div><strong>AI Agent</strong><span>YOUR PERSONAL WORKSPACE</span></div></div>
<div class="header-note">Think clearly. Make things happen.</div></header>'''
EMPTY_CHAT = '''<div class="empty-chat"><span class="eyebrow">A LITTLE HELP, A LOT OF POSSIBILITY</span><h1>What are we working on?</h1><p>Ask a question, build something, or bring a file.<br>We will take it one step at a time.</p><div class="empty-tags"><span>Write &amp; explore</span><span>Code &amp; create</span><span>Read your files</span></div></div>'''
CSS = '''

:root { color-scheme: light; }
body { background: #f3f4ef; }
.gradio-container { max-width: 1180px !important; margin: auto !important; padding: 0 28px 20px !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important; }
.workspace-header { display:flex; align-items:center; justify-content:space-between; padding:24px 0 22px; }
.brand {display:flex; align-items:center; gap:12px; color:#183e38; }
.brand-mark { width:40px; height:40px; background:#e4ebe1; border-radius:12px; display:grid; place-items:center; }
.brand strong { display:block; font-size:20px; line-height:24px; font-weight:650; letter-spacing:-.5px; }
.brand span:not(.brand-mark) { display:block; margin-top:3px; font-size:9px; letter-spacing:1.5px; color:#6e7972; }
.header-note {color:#717b74; font-size:12px;}
#workspace-tabs > .tab-nav { border-bottom:1px solid #d9dfd6; gap:22px; padding:0 0 10px; margin-bottom:16px; }
#workspace-tabs > .tab-nav button {font-size:14px; color:#718078; border:0; padding:8px 0; border-radius:0; background:transparent; min-width:0;}
#workspace-tabs > .tab-nav button.selected {color:#183e38; font-weight:650; box-shadow:0 2px #183e38;}
#workspace-tabs > .tabitem {padding:0; border:0; background:transparent;}
#chat-workspace {height:calc(100dvh - 193px); min-height:500px; gap:0; background:white; border:1px solid #dce2d8; border-radius:18px; overflow:hidden; box-shadow:0 8px 28px #173e3805; }
#conversation-heading { padding:16px 22px; border-bottom:1px solid #edf0e9; }
#conversation-heading p {display:flex; justify-content:space-between; align-items:center; font-size:13px; color:#738176; margin:0; }
#conversation-heading strong {color:#2b473e; font-size:14px; font-weight:600;}
#agent-chat { flex:1; min-height:160px !important; height:0 !important; border:0; background:white; border-radius:0; }
#agent-chat .message {font-size:15px; line-height:1.65;}
#agent-chat .message.user {background:#eef3ec; color:#253f34; border-radius:16px 16px 4px 16px;}
#agent-chat .message.bot {background:#fafbf8; border:1px solid #edf0e9; border-radius:16px 16px 16px 4px;}
#agent-chat pre {max-width:100%; overflow-x:auto; white-space:pre;}
#agent-chat .prose {overflow-wrap:anywhere;}
.empty-chat {text-align:center; padding:30px 18px; color:#294739;}
.eyebrow {font-size:9px; font-weight:600; letter-spacing:1.6px; color:#819184;}
.empty-chat h1 {font-size:34px; letter-spacing:-1.2px; font-weight:500; line-height:1.2; margin:16px 0 12px; color:#254135;}
.empty-chat p {font-size:14px; line-height:1.75; color:#788579;}
.empty-tags {display:flex; justify-content:center; gap:8px; margin-top:25px; flex-wrap:wrap;}
.empty-tags span {font-size:11px; border:1px solid #e4e9df; padding:7px 11px; border-radius:20px; color:#6c7b6e;}
#composer { flex:none; position:sticky; bottom:0; background:#fff; padding:12px 18px; border-top:1px solid #e9eee5; gap:8px; }
#compose-row {gap:10px; align-items:stretch; flex-wrap:nowrap;}
#message-input {flex:1; min-width:0 !important;}
#message-input textarea {border:1px solid #dce3d5; border-radius:12px; padding:12px 14px; background:#fafbf8; font-size:15px; line-height:24px; max-height:130px;}
#send-button, #stop-button {flex:none; min-width:76px !important; width:76px; border-radius:12px; font-size:14px; min-height:48px;}
#send-button {background:#244e40; color:white; border:1px solid #244e40;}
#send-button:hover {background:#183b30;}
#attachment-row {align-items:center; flex-wrap:nowrap; gap:10px;}
#upload-button, #clear-button {flex:none; width:auto; min-width:0 !important; padding:6px 10px; background:transparent; border:0; box-shadow:none; font-size:12px; color:#647968; min-height:34px;}
#file-status {flex:1; min-width:0 !important; border:0; background:transparent; box-shadow:none;}
#file-status textarea {background:transparent; border:0; padding:0; font-size:11px; color:#8b968b; height:28px; min-height:28px;}
#examples {width:100%; border:0; background:#fafbf8; border-radius:10px; padding:6px 12px; box-shadow:none;}
#examples input {font-size:12px;}
#examples .wrap {border:0; background:transparent; box-shadow:none;}
#approval-banner {flex:none; max-height:24dvh; overflow-y:auto; background:#fff7e5; color:#614720; border-bottom:1px solid #e9d7ad; padding:14px 20px; }
#approval-actions {flex:none; margin:0; padding:8px 20px; background:#fff7e5; gap:10px;}
#approval-actions button {min-height:44px;}
#settings-panel, #session-panel {background:white; border:1px solid #dce2d8; border-radius:18px; padding:24px;}
#settings-panel h2, #session-panel h2 {font-size:24px; font-weight:500; letter-spacing:-.6px; color:#254135;}
#settings-panel .block, #session-panel .block {box-shadow:none;}
#provider-settings {border:1px solid #dce2d8; border-radius:12px;}
.provider-slot {border:1px solid #e3e8de !important; border-radius:10px !important; margin-bottom:8px;}
#render-notice {font-size:12px; background:#fff7e5; padding:12px 16px; border-radius:10px; margin-bottom:12px;}
button, input, textarea {font-family:inherit;}
button:focus-visible, input:focus-visible, textarea:focus-visible {outline:2px solid #3c7763 !important; outline-offset:3px;}
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
 #message-input textarea {font-size:16px !important; padding:10px;}
 #send-button, #stop-button {width:65px; min-width:65px !important;}
 #settings-panel, #session-panel {padding:18px 14px; border-radius:14px;}
 input, textarea {font-size:16px !important;}
 #approval-banner {padding:10px 14px;}
 #approval-actions {padding:8px 14px;}
}
@media (prefers-reduced-motion:reduce) { * {scroll-behavior:auto !important; transition:none !important;} }

.gradio-container {width:100% !important; background:#f3f4ef !important;}
.gradio-container > .main, .gradio-container .contain {width:100%;}
.gradio-container .app {padding:0 !important;}
#composer {flex:0 0 auto !important;}
#conversation-heading, #approval-banner, #approval-actions {flex:0 0 auto !important;}
#agent-chat {border:0 !important;}
#file-status {border:0 !important; overflow:hidden;}
#file-status textarea {font-size:11px !important; white-space:nowrap; overflow:hidden; resize:none;}
#message-input {border:0 !important;}
#examples {border:0 !important;}
#examples input::placeholder {color:#718078; opacity:1;}
#agent-chat .wrapper, #agent-chat .bubble-wrap {height:100%; min-height:0;}
#composer > .row {flex:0 0 auto !important;}

#approval-actions {flex-wrap:nowrap;}
#approval-actions button {min-width:0 !important; flex:1;}
#approval-actions button.primary {background:#244e40; color:white;}
#approval-banner pre {white-space:pre-wrap; overflow-wrap:anywhere;}
#message-input textarea {white-space:nowrap;}
#file-status textarea {text-overflow:ellipsis;}
#agent-chat .bubble-wrap {overflow-y:auto;}

#approval-banner pre, #approval-banner code {white-space:pre-wrap !important; overflow-wrap:anywhere !important; word-break:break-word;}
#agent-chat .wrapper {overflow:hidden; display:flex; min-height:0;}
#agent-chat .bubble-wrap {flex:1; min-height:0;}

'''
