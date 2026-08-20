# Discord quick start

Maintained setup and command reference: [`README.md`](README.md).

```bash
pip install -r backend/requirements.txt
pip install -r discord_bot/requirements.txt
python -m uvicorn main:app --app-dir backend --reload
python discord_bot/bot.py
```

Minimum environment: `DISCORD_BOT_TOKEN`, `BACKEND_API_URL`, matching `DISCORD_BACKEND_SHARED_SECRET`, plus backend API/Supabase credentials. Register through `/auth`; use `/link` only for LMS identity linkage.
