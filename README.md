# 🤖 AI Agent - Sandboxed Local AI Assistant

Isang AI agent na parang Arena.ai Agent Mode — may web interface, tools (bash, file ops, web search, pip install, Python execution), at **temporary sandbox** para protektahan ang laptop mo.

## 🆕 What's New

### 📦 Temporary Sandbox (Session-Only Packages)
- All code execution happens in a **temporary per-session sandbox**
- Packages installed via `pip_install` are available for the rest of the session only
- **A venv isolates packages, not the host filesystem**. Use Docker for stronger isolation.
- Auto-cleanup when session ends
- Two modes: **venv** (default, no Docker needed) or **Docker** (optional, stronger isolation)

### 📄 File Viewing (view_file)
- View **any file** inline: docx, pdf, pptx, xlsx, images, csv
- Auto-detects file type and extracts content
- No need to write Python code to read documents

### 🔄 Per-Session Agents
- Each browser tab gets its own isolated agent session
- No more shared state between tabs
- Clean slate on "Clear" button

## 🛡️ Safety Features

Ang agent ay naka-**sandbox** para iwas aksidente:

### Mga Proteksyon:

| Protection | Ano ang ginagawa |
|---|---|
| 📦 **Temporary Sandbox** | All code runs in ephemeral venv/container — host untouched |
| 🔒 **File Sandbox** | File writes ONLY sa `workspace/` folder |
| 🚫 **Command Blocklist** | Awtomatikong bina-block ang mga dangerous commands |
| ⚠️ **Risky Command Warnings** | May warning kapag nag-run ng `del`, `rmdir`, etc. |
| 🔐 **Credential Protection** | Hindi pwede mag-read ng `.ssh/id_rsa`, `.aws/credentials`, etc. |
| 🛤️ **Path Traversal Prevention** | Hindi pwede gumamit ng `../../` para umakyat sa system directories |
| 📦 **Package Validation** | Bina-block ang known malicious/typosquat Python packages |
| 🐍 **Python Code Analysis** | Flagged ang `eval()`, `exec()`, `os.system()` at iba pang risky patterns |

## 📋 Requirements

- **Python 3.9+** ([python.org](https://www.python.org/downloads/))
- **NVIDIA API Key** ([build.nvidia.com](https://build.nvidia.com/))
- **Windows 10/11** (also works on Mac/Linux)
- **Docker Desktop** (optional — for stronger isolation)

## 🚀 Quick Start

### Option 1: Double-click (Pinakamadali)

1. I-copy ang `ai-agent` folder sa laptop mo
2. Double-click **`install.bat`** (first time lang)
3. Double-click **`start.bat`** para i-run!

### Option 2: Manual Setup

```
cd ai-agent
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Mabubuksan ang browser sa **[http://127.0.0.1:7860](http://127.0.0.1:7860/)**

### I-test ang Safety:

```
python test_safety.py
```

## 🎯 Paggamit

1. Buksan ang **🔑 Providers & API keys** sa Settings. Pumili ng preset (NVIDIA NIM, Groq, Gemini, OpenRouter, Custom), i-paste ang key, i-click ang **Test connection**, tapos **Save providers**
2. Hanggang 3 provider; ang nasa taas ang una. Kung rate-limited o down, lilipat sa susunod
3. Ang keys ay nasa `providers.json` lang (gitignored, hindi nilo-log, naka-mask sa UI). Gumagana pa rin ang `NVIDIA_API_KEY` sa `.env`
4. Mag-type ng request — pwede Tagalog!

### Example Prompts:

- "Gawa ka nga ng Python script na nag-calculate ng fibonacci"
- "I-install mo ang rich at gawa ka ng colored output" *(installs in sandbox, not on host)*
- "Basahin mo yung uploaded na docx file at i-summarize" *(uses view_file)*
- "Gumawa ka ng simple Flask web server"
- **"Subukan mong i-delete ang C:\\Windows (test ng safety!)"**

## 🛠️ Available Tools (Sandboxed)

| Tool | Description | Safety |
|---|---|---|
| `run_bash` | Shell commands | 🛡️ Dangerous commands blocked |
| `read_file` | Read text files | 🔒 Credentials protected |
| `view_file` | View any file (docx/pdf/pptx/images) | ✅ Auto-detect format |
| `write_file` | Create files | 🔒 Workspace only |
| `edit_file` | Surgical string replacement | 🔒 Workspace only |
| `list_files` | List directory | 🔒 Workspace only |
| `web_search` | DuckDuckGo search | ✅ Safe |
| `pip_install` | Install packages | 📦 Temporary (session-only) |
| `run_python` | Execute Python | 🛡️ Runs in sandbox |
| `download_file` | Download from URL | ✅ Safe |
| `image_search` | Search for images | ✅ Safe |
| `process_image` | Resize/crop/convert images | ✅ Safe |
| `remove_background` | Remove image background | ✅ Safe |

## 📦 Temporary Sandbox Explained

```
Session starts
  → Sandbox created (ephemeral venv or Docker container)
  → Core packages pre-installed (Pillow, python-docx, pandas, etc.)

User: "Install matplotlib"
  → pip_install("matplotlib")
  → Installed in sandbox only
  → Available for rest of session

Session ends (close browser / timeout / clear)
  → Sandbox destroyed
  → matplotlib is gone
  → Host machine untouched ✅
```

### Pre-installed Packages (Always Available):
- Pillow, requests, beautifulsoup4, pandas
- python-docx, python-pptx, openpyxl
- PyPDF2, pdfplumber

### Docker Mode (Optional — Stronger Isolation)
Set `AGENT_USE_DOCKER=1` in `.env` to use Docker containers instead of venvs.

## 📁 Project Structure

```
ai-agent/
├── app.py               # Web UI (Gradio)
├── agent.py             # Agent loop + LLM integration
├── tools.py             # Sandboxed tool implementations
├── safety.py            # 🛡️ Safety guardrails
├── sandbox_session.py   # 📦 Per-session temporary sandbox
├── sandbox_docker.py    # 🐳 Docker sandbox (optional)
├── config.py            # Configuration
├── test_safety.py       # Safety test suite
├── requirements.txt     # Dependencies
├── start.bat            # One-click start
├── install.bat          # One-click install
├── README.md            # This file
└── workspace/           # Sandboxed working directory
```

## 🔧 Customization

### Palitan ang model:

I-edit ang `config.py` o gamitin ang Settings panel.

### Dagdag ng custom safety rules:

I-edit ang `safety.py`:
- `BLOCKED_COMMANDS` - mga commands na bawal talaga
- `RISKY_COMMANDS` - mga commands na may warning
- `PROTECTED_PATHS_WINDOWS` - mga directories na bawal galawin

### Dagdag ng pre-installed packages:

I-edit ang `config.py`:
- `CORE_PACKAGES` - packages na laging available sa sandbox

## 🧪 Testing

I-run ang safety tests para ma-verify na gumagana ang guardrails:

```
python test_safety.py
```

## ⚠️ Important Notes

- Ang agent ay **hindi** pwedeng mag-bypass ng safety restrictions
- Lahat ng blocked operations ay naka-log
- Pwede mong i-customize ang safety rules sa `safety.py`
- Ang workspace folder lang ang pwedeng galawin ng agent
- **No step or time limit** per message. A turn ends when the model finishes, you press Stop, or the same tool call repeats 5 times in a row. Optional caps: `AGENT_MAX_ITERATIONS`, `AGENT_MAX_SECONDS`
- **Self-evaluation** is capped at 3 per turn to save tokens
- Installed packages are **temporary** — host machine is never modified

## 🐛 Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError` | The sandbox will auto-install it, or add to CORE_PACKAGES |
| `API Error: 401` | I-check ang API key |
| Command blocked | Normal! Safety feature yan. Check `safety.py` |
| Browser hindi bumukas | Manual: `http://127.0.0.1:7860` |
| Sandbox slow first time | Normal! Creating venv + installing packages (~15s) |
| Safety test failed | I-run `python test_safety.py` para i-diagnose |

---

Made with ❤️ at 🛡️ para sa safe na lokal na AI development!

## Private phone access on Render free (new)

This is a single-owner service, not a public agent or a multi-user app. The phone
opens the Render HTTPS address in Safari; the code and tools run on Render, not
on the iPhone. The model still runs at your chosen API provider. API free tiers
have their own limits and this does not make them unlimited.

### Deployment (not automatic)

1. Review this branch/PR. `render.yaml` points to `feat/render-phone-access` and
   disables automatic deploys. After merging, change the branch to `main` if you
   want to deploy the merged code. Do not deploy plain `main` before these changes.
2. In your Render account, create a Blueprint from this repository. Confirm the
   service says **Free**, with no paid disk or paid database. No payment method
   is needed for the free instance. If Render asks for payment or an upgrade,
   stop rather than accepting it.
3. Enter `AGENT_USERNAME` and a unique random `AGENT_PASSWORD` of at least 16
   characters in Render's environment settings. Never put them in git or chat.
   Non-loopback startup refuses to run without a strong login. `PORT` is supplied
   by Render; `AGENT_HOST=0.0.0.0` listens on its assigned port.
4. Open the service's HTTPS URL on your iPhone and sign in. Keep the password
   private; anyone with it can run tools and access service files/API keys.
5. Add provider settings using the password boxes under Settings, or use the
   environment option below. Test with your own key, then send a small message.

### Keep provider keys after sleep/restart

UI saves are stored in `providers.json`, **which is temporary on Render free**.
To keep provider settings across restarts, add `AGENT_PROVIDERS_JSON` in Render's
Environment dashboard. Its value is a JSON array with up to three providers:

```json
[{"preset":"Groq","base_url":"https://api.groq.com/openai/v1","model":"YOUR_MODEL","api_key":"YOUR_KEY","enabled":true}]
```

Enter the real key only in that dashboard, not in this file. Local UI saves
have priority while the service stays awake. After the local file is lost,
it uses the environment settings again. Update the environment value to make
changes durable. `NVIDIA_API_KEY` is also supported as the legacy env fallback.
Keys are not sent back into password inputs, but the app/tools run as the same
OS user and can access the process environment. This is **not secret isolation**.
Do not put unrelated account credentials into this service.

### Free-tier limits and safety

- Sleeps after 15 idle minutes; the next request can take about a minute to wake.
- Restart/redeploy/sleep deletes uploaded files, memory, local provider settings,
  generated outputs and installed packages. Chat sessions are not durable either.
- Free instances have 512 MB RAM / 0.1 CPU. Heavy image processing, pandas work or
  lots of package installs can exhaust resources. This build omits optional
  `rembg` and uses the existing simpler background-removal fallback.
- This deploy uses a Python venv, **not Docker isolation**. Bash/Python can access
  service files and keys. Login is an access gate, not a sandbox or approval system.
  Use it only yourself, and avoid sensitive files or untrusted task instructions.
- No uptime-pinging workaround is included. Render provides 750 free instance
  hours per workspace/month plus bandwidth/build allowances; exceeding limits
  without a payment method can suspend service/builds.
- For durable memory/files, add external storage in a separate reviewed change,
  or run on your own PC. A paid Render disk is a paid option, not part of this setup.
- Use HTTPS outside localhost. Gradio password login is not MFA and does not
  include our own rate-limiting layer. Change the env password and restart if it
  is exposed. Do not share the login.

For local-only use, `python app.py` keeps the existing `127.0.0.1:7860` defaults.
For LAN access set `AGENT_HOST`, `AGENT_USERNAME`, and `AGENT_PASSWORD`; put TLS
in front of it or use a private network. Do not enable Gradio public sharing.

Sources: <https://render.com/docs/free>, <https://render.com/docs/blueprint-spec>.

Tests: `python -m unittest test_server_settings test_provider_env`.

### NVIDIA default and retry behavior

The NVIDIA preset now defaults to `nvidia/nemotron-3-ultra-550b-a55b`.
`AI_MODEL` and models already saved in provider settings are not overwritten.
If an existing NVIDIA slot still uses the old model, edit that slot in Settings.
Other provider presets are unchanged.

Completion requests (chat, planning, review, and connection test) use a
30-second request timeout with SDK retries disabled. Connection errors,
timeouts, 429, and 500/502/503/504 responses get up to three attempts, with
1/2-second backoffs. Chat falls back to the next configured provider only after
those attempts are exhausted. Each model request times out after 30 seconds; there is no overall turn limit unless `AGENT_MAX_SECONDS` is set.
Read-only metrics and sandbox-status views no longer create sandboxes or install
packages. A new sandbox starts only when a tool needs it. This removes a slow,
silent pre-request setup even for a simple greeting. The completed chat history
is retained when the Send/Stop controls reset.

The UI keeps a waiting/retrying indicator visible during provider I/O and exits
on cancellation or the turn deadline. A blocked background HTTP read may still
finish within its request timeout; its result is discarded and its stream closed.
Tool execution and synchronous planning/review are still best-effort bounded,
not a guarantee that every kind of task stops exactly at 120 seconds.
Authentication and other non-transient errors are not retried. A partial stream
is never replayed automatically, avoiding duplicated text or tool calls.

Nemotron uses `chat_template_kwargs.enable_thinking=false` by default. The
**Enable Nemotron thinking (slower)** checkbox enables it for the current session.
Thinking mode also sets `force_nonempty_content=true` as NVIDIA documents for
reasoning with tool calls. Raw reasoning is never shown in chat. A reasoning-only stream keeps a status
visible; a completed stream without answer text or tool calls shows a useful
error instead of silently recording an empty answer. Other models keep their
existing reasoning controls.

Request start/ready/error/retry events go to stdout for hosting logs. They contain
only attempt numbers, timeouts, status codes, and error type names, never keys,
URLs, prompts, reasoning, model outputs, or raw exception text. Existing private
JSONL session logs remain separate. No API key is included in this change.

## Harness safety and smarts

- **Approval gate**: without Docker, risky commands and every `pip_install` pause for Approve/Deny in the UI (120s no answer = deny). `AGENT_APPROVAL=auto|on|off` (default auto), `AGENT_APPROVAL_TIMEOUT` seconds. Blocked commands stay blocked.
- **Answer check**: after tool use, the model checks its final answer against the tool results (max 2 fix rounds). `AGENT_VERIFY=off` disables it.
- **Vision flag**: each provider slot has a Vision checkbox. Images from `view_file` are sent only when it is on.
- **Models + benchmark**: "Fetch models" lists `/models`; "Benchmark (5 calls)" runs five small requests and shows pass/fail and time. Both only run when you click.

### Responsive workspace

The UI separates **Chat**, **Settings**, and **Session** into tabs. Chat keeps the
composer at the bottom and scrolls replies inside the conversation. Provider
slots are collapsed in Settings, and session usage/tools live in Session.
Approval requests remain above the conversation with explicit Approve/Deny buttons.

Local UI regression check (fake responses only, no provider calls):

```bash
pip install playwright
python checks/ui_browser_check.py --output /tmp/ui-screenshots
```

This optional check uses `/usr/bin/google-chrome` and covers 320px/390px phone
and 1280px desktop layouts. Playwright is not a production requirement.

## Wake page for the free Render server

Render's free service sleeps after about 15 minutes idle, and a sleeping app cannot serve its own loading page. `docs/index.html` is a small static page (no secrets, no build) meant for GitHub Pages (Settings > Pages > Deploy from branch > `main` / `/docs`). Open it instead of the app link: it polls the app's public `/favicon.ico` until the app answers, then redirects to the app. It can be added to the phone home screen. Local check: `python checks/wake_page_check.py` (needs playwright and Chrome).

### Render Docker isolation

The Dockerfile runs the app as a non-root user and enforces `AGENT_SANDBOX_MODE=bubblewrap` for tool execution. This is not Docker-in-Docker: each Bash/Python command starts in a fresh bubblewrap mount, PID, user and network namespace, with a clean environment, read-only interpreter/package paths, private `/tmp`, and only the workspace writable. App source, provider settings, other session environments and host processes are not mounted. Seccomp blocks namespace/mount/ptrace/keyring operations. Limits: 512 MiB address space per process, 60 CPU seconds per process, 256 processes per UID, 128 descriptors, 16 MiB per output file, 64 KiB captured output. A timeout kills the command's process group; PID-namespace teardown kills its remaining children.

Isolated Bash/Python commands do not ask for approval in `auto` approval mode. Package installs still ask: only the approved install gets network access and a writable session environment. If isolation cannot initialize, commands fail rather than silently run on the host. The workspace is shared within this single-user app and is ephemeral on Render Free. The app's own built-in fetch/file tools remain trusted code, outside the command sandbox. This reduces tool-child access, not whole-app or kernel exploit risk.

Render: use Settings > Build > Source > Edit, select this repository and `main`, choose Docker and deploy. Keep the instance on Free. No Docker daemon or privileged container is required. Rollback to the former Python runtime must restore its Python build/start commands and keeps approval prompts; do not describe venv mode as isolation.

Render Docker blocks bubblewrap mounts (`Failed to make / slave: Permission denied`). On that runtime the runner instead uses Landlock ABI 3+ with seccomp: read/execute access only to runtime paths; read/write access only to workspace, session install directory when approved, and a private temporary directory. File truncation is covered. Seccomp denies network sockets for ordinary commands, host signals/ptrace and namespace operations. It is access restriction, not a separate filesystem or PID view: directory names can remain visible, but forbidden file contents and writes are denied. Descendants cannot detach and are killed with the command group. No host fallback is allowed when neither kernel backend works.
