"""Fail-closed launch settings. No network binding without a strong login."""
import hmac
import os


def launch_settings(env=None):
    env = os.environ if env is None else env
    hosted = env.get("RENDER", "").lower() == "true"
    host = env.get("AGENT_HOST", "0.0.0.0" if hosted else "127.0.0.1").strip()
    user = env.get("AGENT_USERNAME", "").strip()
    password = env.get("AGENT_PASSWORD", "")
    public = host not in {"127.0.0.1", "localhost", "::1"}
    if public or user or password:
        if not user or len(password) < 16 or not password.strip():
            raise ValueError("Set AGENT_USERNAME and AGENT_PASSWORD (at least 16 characters) before enabling network access.")
        def authenticate(given_user, given_password):
            # UTF-8 bytes allow non-ASCII passwords and usernames.
            user_ok = hmac.compare_digest(given_user.encode(), user.encode())
            pass_ok = hmac.compare_digest(given_password.encode(), password.encode())
            return user_ok and pass_ok
        auth = authenticate
    else:
        auth = None
    try:
        port = int(env.get("PORT", env.get("AGENT_PORT", "7860")))
    except ValueError:
        raise ValueError("PORT / AGENT_PORT must be an integer.") from None
    if not 1 <= port <= 65535:
        raise ValueError("PORT / AGENT_PORT must be between 1 and 65535.")
    return dict(server_name=host, server_port=port, auth=auth,
                share=False, inbrowser=not public, show_api=False,
                auth_message="Private AI agent. Sign in with your configured username and password.")
