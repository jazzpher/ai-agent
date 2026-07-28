"""
Configuration for the AI Agent
"""
import os

# NVIDIA NIM API Configuration
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Default model - pwede mo palitan
DEFAULT_MODEL = os.environ.get("AI_MODEL", "openai/gpt-oss-120b")

# Agent settings
MAX_ITERATIONS = 20  # Maximum tool calls per conversation turn
WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace")

# Ensure workspace exists
os.makedirs(WORKSPACE_DIR, exist_ok=True)
