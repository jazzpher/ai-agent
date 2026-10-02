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
- **Max 20 tool calls** per message (configurable)
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
