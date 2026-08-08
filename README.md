# 🤖 AI Agent - Sandboxed Local AI Assistant

Isang AI agent na parang Arena.ai Agent Mode — may web interface, tools (bash, file ops, web search, pip install, Python execution), at **temporary sandbox** para protektahan ang laptop mo.

## 🆕 What's New

### 📦 Temporary Sandbox (Session-Only Packages)
- All code execution happens in a **temporary per-session sandbox**
- Packages installed via `pip_install` are available for the rest of the session only
- **Host machine is NEVER modified** — no permanent installs
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

1. I-paste ang **NVIDIA API key** sa Settings panel
2. Piliin ang **model** (default: `openai/gpt-oss-120b`)
3. Mag-type ng request — pwede Tagalog!

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
