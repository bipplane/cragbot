# getCRAG-ed Discord bot

Separate discord.py worker providing course access, CRAG chat, learning modes, quizzes, LMS identity linking and research capture.

## Setup

1. Create Discord application/bot; enable Message Content Intent.
2. Invite with `bot` and `applications.commands` scopes plus Send Messages, Embed Links and Read Message History permissions.
3. Install `backend/requirements.txt` and `discord_bot/requirements.txt` in same environment.
4. Configure backend and bot environment.
5. Start backend, then run `python discord_bot/bot.py` from repository root.

Required values:

```ini
DISCORD_BOT_TOKEN=...
BACKEND_API_URL=http://localhost:8000
DISCORD_BACKEND_SHARED_SECRET=...
```

Optional: `DISCORD_GUILD_ID`, `USER_COURSE_MAPPING`, `SESSION_TIMEOUT`, `REQUEST_TIMEOUT`, `LOG_LEVEL`. Backend and worker must share exact `DISCORD_BACKEND_SHARED_SECRET`. `discord_bot/.env` is optional fallback; backend `.env` is also loaded.

## Student commands

| Command | Purpose |
|---|---|
| `/auth` | Register real name/student ID against tutor join code; may require approval |
| `/link` | Link Discord account to existing anonymous LMS identity using ten-minute one-use code |
| `/generate` | Generate ten-minute one-use LMS recovery code after linking |
| `/ask` | Start course-grounded conversation |
| reply to bot | Continue same reply-chain conversation |
| `/mode` | Select standard, guided or Socratic mode |
| `/quiz` | Start interactive practice quiz |
| `/help` | Show usage |

Legacy documentation referenced `/join`, `/list-courses` and `/select-course`; current `bot.py` does not register these commands. Use `/auth` for course registration.

## Conversation and research capture

Each `/ask` or fresh mention starts conversation. Replying to generated answer continues it. Bot records research conversation, sanitised interaction and canonical student/CRAG message through shared-secret backend endpoints. Delivery is best-effort: operational query can succeed when research capture fails, so monitoring/reconciliation remains required before study launch.

Course access resolution uses channel approval where applicable, then persistent backend user-course link, optional `USER_COURSE_MAPPING`, or active in-memory session. Sessions are lost on worker restart; canonical research conversation state can be rehydrated by backend RPCs where capture succeeded.

## Troubleshooting

- Commands absent: verify token, application scopes, worker logs and optional `DISCORD_GUILD_ID` sync.
- `401` from research endpoints: secrets differ or one side is missing.
- No course access: complete `/auth` and instructor approval, or configure fallback mapping.
- Backend errors: check `BACKEND_API_URL`, `/health`, worker timeout and logs.
