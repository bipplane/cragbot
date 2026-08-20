"""
Configuration module for Discord bot.
Loads environment variables and defines bot settings.
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Shared backend environment is canonical. Legacy discord_bot/.env only fills
# variables absent from process environment and backend/.env.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / "backend" / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

# Discord Bot Configuration
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN environment variable is required")

# Backend API Configuration
BACKEND_API_URL = os.getenv("BACKEND_API_URL", "https://getcrag-api.vercel.app")
DISCORD_BACKEND_SHARED_SECRET = os.getenv("DISCORD_BACKEND_SHARED_SECRET", "")
RESEARCH_CAPTURE_ENABLED = len(DISCORD_BACKEND_SHARED_SECRET) >= 32

# Optional development/pilot guild. Guild-scoped commands update immediately,
# while global Discord command propagation may be cached by clients.
_discord_guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
try:
    DISCORD_GUILD_ID = int(_discord_guild_id) if _discord_guild_id else None
except ValueError as exc:
    raise ValueError("DISCORD_GUILD_ID must contain a numeric Discord guild ID") from exc

# User to Course Bot Mapping (PER-STUDENT setup)
# Each student gets their own course. Format: {"discord_user_id": "course_bot_id", ...}
# Get user IDs: Right-click user in Discord → Copy ID (enable Developer Mode)
USER_COURSE_MAPPING_STR = os.getenv("USER_COURSE_MAPPING", "{}")
try:
    USER_COURSE_MAPPING = json.loads(USER_COURSE_MAPPING_STR)
except json.JSONDecodeError:
    print("Warning: Invalid USER_COURSE_MAPPING JSON, using empty mapping")
    USER_COURSE_MAPPING = {}

# Bot Settings
SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT", "3600"))  # 1 hour in seconds
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))  # 30 seconds

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
