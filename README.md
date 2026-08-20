# getCRAG-ed

Course-grounded AI tutoring platform using Corrective Retrieval-Augmented Generation (CRAG). Instructors create course bots, upload material, review questions and analytics; students learn through web chat or Discord.

Status: active research prototype, audited against source on 19 August 2026.

## Current system

- FastAPI backend with modular routes and service wiring.
- Supabase PostgreSQL, pgvector and Storage.
- Jina 768-dimensional multimodal embeddings for text, PDF pages and queries.
- Gemini answer generation, extraction, quiz generation and grading.
- React student chat with persistent LMS identity, conversations, citations, learning modes and quizzes.
- React instructor dashboard with onboarding, content management, Q&A review, student/quiz analytics, Discord management, manual L1–L6 HCD coding and weekly HCD trends.
- Discord bot with course enrolment/approval, LMS identity linking/recovery, RAG chat, reply-chain conversations, learning modes, quizzes and research-event capture.

Research infrastructure exists but is not yet study-ready. See [research implementation status](docs/research/PROGRESSIVE_HCD_IMPLEMENTATION_PLAN.md) and [project roadmap](docs/PROJECT_ROADMAP.md).

Professor-facing evolution and design rationale: [midterm handover changelog](docs/MIDTERM_HANDOVER_CHANGELOG.md).

## Repository

| Path | Purpose |
|---|---|
| `backend/` | API, services, models, migrations, retrieval and AI integrations |
| `frontend/lms-chat/` | Student web interface |
| `frontend/lms-management/` | Instructor/researcher dashboard |
| `frontend/shared/` | Shared API client, components and quiz types |
| `discord_bot/` | Separately deployed Discord worker |
| `docs/architecture/` | Maintained system and flow diagrams |
| `docs/research/` | Research implementation status and gaps |
| `docs/deployment/` | Local and production deployment guide |

## Start locally

1. Configure `backend/.env` with `GEMINI_API_KEY`, `JINA_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`, `DISCORD_LINK_CODE_SECRET`, `DISCORD_BACKEND_SHARED_SECRET` and `RESEARCH_ADMIN_SHARED_SECRET`.
2. Apply database SQL through migration `025_unified_lms_research_capture.sql` in filename/order sequence. Duplicate numeric prefixes (`005`, `006`) are separate migrations.
3. Install backend and Discord dependencies: `pip install -r backend/requirements.txt` and `pip install -r discord_bot/requirements.txt`.
4. Install each frontend: run `npm install` inside `frontend/shared`, `frontend/lms-chat` and `frontend/lms-management`.
5. Start backend from `backend/`: `python -m uvicorn main:app --reload`.
6. Start frontends with `npm run dev`; start Discord worker with `python discord_bot/bot.py` from repository root.

Detailed instructions: [backend](backend/README.md), [student frontend](frontend/lms-chat/README.md), [management frontend](frontend/lms-management/README.md), [Discord](discord_bot/README.md), [deployment](docs/deployment/CS3103_Deployment_Strategy.md).

## Verification

```bash
cd backend
python -m pytest tests
cd ../frontend/lms-chat
npm run build
cd ../lms-management && npm run build
```

Swagger UI: `http://127.0.0.1:8000/docs`. API routes mount at root; do not add `/api`.
