"""
Configuration for the AI Agent
"""
import os
from dotenv import load_dotenv

# Load .env file if present (so users can put NVIDIA_API_KEY there instead of UI)
load_dotenv()

# NVIDIA NIM API Configuration
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

# Default model - pwede mo palitan
DEFAULT_MODEL = os.environ.get("AI_MODEL", "openai/gpt-oss-120b")

# Agent settings
MAX_ITERATIONS = 20                    # Maximum tool calls per conversation turn
MAX_TOTAL_SECONDS = 600                # Wall-clock budget per chat_stream call
MAX_CONTEXT_MESSAGES = 50              # Conversation trim threshold
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TEMPERATURE = 0.3

# Workspace
WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)

# File handling
MAX_FILE_READ_BYTES = 50_000           # Truncate file reads beyond this
MAX_TOOL_OUTPUT_CHARS = 8000           # Truncate tool outputs before sending back to LLM

# Pricing (per 1M tokens) - rough estimates for display purposes
# Update if you use different models. Keys are substrings of model names.
MODEL_PRICING = {
    "gpt-oss-120b":   {"input": 0.0,  "output": 0.0},   # free tier
    "llama-3.1-70b":  {"input": 0.59, "output": 0.79},
    "nemotron-70b":   {"input": 0.0,  "output": 0.0},   # free tier
    "gemma-2-27b":    {"input": 0.0,  "output": 0.0},   # free tier
    "default":        {"input": 1.00, "output": 3.00},
}

# Models that support reasoning_effort parameter on NVIDIA NIM
REASONING_CAPABLE_MODELS = (
    "gpt-oss-120b",
    "o1",
    "o3",
    "claude-3.7",
    "deepseek-r1",
)

# Memory file lives in the workspace so the agent can read/edit it
MEMORY_FILE = os.path.join(WORKSPACE_DIR, "MEMORY.md")

# Per-session log file (JSONL - one record per line)
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agent_logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ============================================================
# SANDBOX CONFIGURATION
# ============================================================

# Sandbox directory for ephemeral venvs
SANDBOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sandboxes")
os.makedirs(SANDBOX_DIR, exist_ok=True)

# Context management directory (for offloaded tool outputs)
CONTEXT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".context")
os.makedirs(CONTEXT_DIR, exist_ok=True)

# Facts file (L1 atomic facts extracted from conversations)
FACTS_FILE = os.path.join(WORKSPACE_DIR, ".facts")

# Max chars for tool result summaries injected into context
MAX_SUMMARY_CHARS = 200

# Sandbox mode: "auto" (try Docker, fall back to venv), "docker", "venv"
SANDBOX_MODE = os.environ.get("AGENT_SANDBOX_MODE", "auto")

# Session timeout in seconds (default: 1 hour)
SANDBOX_TIMEOUT = int(os.environ.get("AGENT_SANDBOX_TIMEOUT", "3600"))

# Core packages pre-installed in every sandbox session
# These are the packages the agent needs most frequently
CORE_PACKAGES = [
    # Image processing (for image tools)
    "Pillow",
    # Document handling (for view_file)
    "python-docx",
    "python-pptx",
    "openpyxl",
    "PyPDF2",
    "pdfplumber",
    # Web & data
    "requests",
    "beautifulsoup4",
    "pandas",
]
