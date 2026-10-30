"""All settings come from the .env file (loaded with python-dotenv)."""
import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

# Always load .env from the backend directory
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


DRY_RUN = _bool("DRY_RUN", "true")
DEMO_MODE = _bool("DEMO_MODE", "false")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "3.5"))

DB_BACKEND = os.getenv("DB_BACKEND", "supabase").strip().lower()
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SQLITE_PATH = str(Path(__file__).resolve().parent / "carecall.db")

ENABLED_LANGUAGES = [x.strip() for x in os.getenv("ENABLED_LANGUAGES", "en,hi,kn").split(",") if x.strip()]
VOICES = {
    "en": os.getenv("VOICE_EN", "Google.en-IN-Standard-A"),
    "hi": os.getenv("VOICE_HI", "Google.hi-IN-Standard-A"),
    "kn": os.getenv("VOICE_KN", "Google.kn-IN-Standard-A"),
}

CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()]


def get_public_url() -> str:
    """Public https address Twilio uses to reach us."""
    # 1. Check if tunnel.txt has an active tunnel
    tunnel_file = Path(__file__).resolve().parent / "tunnel.txt"
    if tunnel_file.exists():
        t = tunnel_file.read_text().strip()
        if t.startswith("https://"):
            return t.rstrip("/")

    # 2. Check if ngrok runs on this laptop
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=0.5) as r:
            data = json.load(r)
        for t in data.get("tunnels", []):
            if t.get("public_url", "").startswith("https://"):
                return t["public_url"].rstrip("/")
    except Exception:
        pass

    # 3. PUBLIC_URL from .env
    if PUBLIC_URL and PUBLIC_URL.startswith("https://") and "xxxx" not in PUBLIC_URL:
        return PUBLIC_URL
    return PUBLIC_URL
