# 🤖 AI Agent - Sandboxed Local AI Assistant

Isang AI agent na parang Arena.ai Agent Mode — may web interface, tools (bash, file ops, web search, pip install, Python execution), at **comprehensive safety guardrails** para protektahan ang laptop mo.

## 🛡️ Safety Features (BAGO!)

Ang agent ay naka-**sandbox** para iwas aksidente:

### Mga Proteksyon:
| Protection | Ano ang ginagawa |
|-----------|-----------------|
| 🔒 **File Sandbox** | File writes ONLY sa `workspace/` folder. Hindi pwede mag-write sa `C:\Windows`, `Program Files`, etc. |
| 🚫 **Command Blocklist** | Awtomatikong bina-block ang mga dangerous commands: `format`, `shutdown`, `del /s C:\Windows`, `reg delete`, `diskpart`, etc. |
| ⚠️ **Risky Command Warnings** | May warning kapag nag-run ng `del`, `rmdir`, `pip uninstall`, etc. |
| 🔐 **Credential Protection** | Hindi pwede mag-read ng `.ssh/id_rsa`, `.aws/credentials`, `.env`, `.git-credentials` |
| 🛤️ **Path Traversal Prevention** | Hindi pwede gumamit ng `../../` para umakyat sa system directories |
| 📦 **Package Validation** | Bina-block ang known malicious/typosquat Python packages |
| 🐍 **Python Code Analysis** | Flagged ang `eval()`, `exec()`, `os.system()` at iba pang risky patterns |

### Mga Blocked Operations:
```
❌ format C:                          (format drive)
❌ del /s C:\Windows\System32         (delete system)
❌ rmdir /s C:\Program Files          (delete programs)
❌ shutdown /s                        (shutdown PC)
❌ reg delete HKLM\SOFTWARE           (edit registry)
❌ taskkill /f /im svchost.exe        (kill critical process)
❌ diskpart                           (disk partitioner)
❌ Write to C:\Windows\...            (system directory)
❌ Read ~/.ssh/id_rsa                 (SSH keys)
```

## 📋 Requirements

- **Python 3.9+** ([python.org](https://www.python.org/downloads/))
- **NVIDIA API Key** ([build.nvidia.com](https://build.nvidia.com))
- **Windows 10/11**

## 🚀 Quick Start

### Option 1: Double-click (Pinakamadali)
1. I-copy ang `ai-agent` folder sa laptop mo
2. Double-click **`install.bat`** (first time lang)
3. Double-click **`start.bat`** para i-run!

### Option 2: Manual Setup
```bash
cd ai-agent
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Mabubuksan ang browser sa **http://127.0.0.1:7860**

### I-test ang Safety:
```bash
python test_safety.py
```

## 🎯 Paggamit

1. I-paste ang **NVIDIA API key** sa Settings panel
2. Piliin ang **model** (default: `openai/gpt-oss-120b`)
3. Mag-type ng request — pwede Tagalog!

### Example Prompts:
- "Gawa ka nga ng Python script na nag-calculate ng fibonacci"
- "I-install mo nga ang pandas tapos gawa ka ng sample analysis"
- "Gumawa ka ng simple web server gamit ang Flask"
- "Gawa ka ng TODO app gamit ang HTML, CSS, at JavaScript"
- **"Subukan mong i-delete ang C:\Windows (test ng safety!)"**

## 🛠️ Available Tools (Sandboxed)

| Tool | Description | Safety |
|------|-------------|--------|
| `run_bash` | Shell commands | 🛡️ Dangerous commands blocked |
| `read_file` | Read files | 🔒 Credentials protected |
| `write_file` | Create files | 🔒 Workspace only |
| `list_files` | List directory | 🔒 Workspace only |
| `web_search` | DuckDuckGo search | ✅ Safe |
| `pip_install` | Install packages | 🛡️ Malicious packages blocked |
| `run_python` | Execute Python | 🛡️ Risky patterns flagged |

## 📁 Project Structure

```
ai-agent/
├── app.py              # Web UI (Gradio)
├── agent.py            # Agent loop + LLM integration
├── tools.py            # Sandboxed tool implementations
├── safety.py           # 🛡️ Safety guardrails & sandbox
├── config.py           # Configuration
├── test_safety.py      # Safety test suite
├── requirements.txt    # Dependencies
├── start.bat           # One-click start
├── install.bat         # One-click install
├── README.md           # This file
└── workspace/          # Sandboxed working directory
```

## 🔧 Customization

### Palitan ang model:
I-edit ang `config.py` o gamitin ang Settings panel.

### Supported NVIDIA Models:
- `openai/gpt-oss-120b` - Powerful reasoning
- `meta/llama-3.1-70b-instruct` - Fast at capable
- `nvidia/llama-3.1-nemotron-70b-instruct` - NVIDIA's model
- `google/gemma-2-27b-it` - Lightweight

### Dagdag ng custom safety rules:
I-edit ang `safety.py`:
- `BLOCKED_COMMANDS` - mga commands na bawal talaga
- `RISKY_COMMANDS` - mga commands na may warning
- `PROTECTED_PATHS_WINDOWS` - mga directories na bawal galawin

## 🧪 Testing

I-run ang safety tests para ma-verify na gumagana ang guardrails:
```bash
python test_safety.py
```

## ⚠️ Important Notes

- Ang agent ay **hindi** pwedeng mag-bypass ng safety restrictions
- Lahat ng blocked operations ay naka-log
- Pwede mong i-customize ang safety rules sa `safety.py`
- Ang workspace folder lang ang pwedeng galawin ng agent
- **Max 20 tool calls** per message (configurable)

## 🐛 Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| `API Error: 401` | I-check ang API key |
| Command blocked | Normal! Safety feature yan. Check `safety.py` |
| Browser hindi bumukas | Manual: `http://127.0.0.1:7860` |
| Safety test failed | I-run `python test_safety.py` para i-diagnose |

---
Made with ❤️ at 🛡️ para sa safe na lokal na AI development!
