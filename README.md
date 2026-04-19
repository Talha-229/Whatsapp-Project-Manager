# WhatsApp Project Manager

FastAPI backend that powers a **WhatsApp Business (Cloud API)** assistant for scheduling, HR policies, tasks, projects, and meeting notes (Recall.ai).

Repository: [github.com/Talha-229/Whatsapp-Project-Manager](https://github.com/Talha-229/Whatsapp-Project-Manager)

## Features

- **WhatsApp assistant** — LangGraph ReAct agent with conversation memory (Postgres checkpointer when `DATABASE_URL` is set).
- **Google Calendar** — OAuth, create/preview meetings, list events, Contacts-based attendee resolution.
- **Meeting reminders** — WhatsApp pings using your **current** lead time (minutes before start); works for bot-scheduled meetings and primary-calendar events (with deduplication).
- **Recall.ai** — Notetaker bot on Meet links; transcript + Whisper fallback on video; **post-meeting summary** (decisions, action items, owners, deadlines) saved to Supabase and sent on WhatsApp.
- **Company policies** — RAG-style search over policies stored in Supabase.
- **Tasks** — Create, list (today / week / overdue / open), update due date/title/notes, complete; scoped by WhatsApp user.
- **Projects** — Create and list personal projects (scoped by WhatsApp user).
- **Inbound deduplication** — WhatsApp message IDs deduped to avoid duplicate replies when Meta retries webhooks.
- **Jobs** — APScheduler: meeting reminders, Google Calendar reminders, overdue task nudges.

## Stack

- Python 3.11+ (3.12 OK)
- FastAPI, Uvicorn
- Supabase (REST + Postgres for LangGraph checkpoints)
- OpenAI (chat + Whisper)
- Google Calendar / People API
- Recall.ai (optional)
- Meta WhatsApp Cloud API

## Prerequisites

- [Supabase](https://supabase.com) project (URL + service role key + Postgres URI for LangGraph)
- [Meta for Developers](https://developers.facebook.com/) — WhatsApp app, permanent access token, phone number ID, app secret, verify token
- [OpenAI API key](https://platform.openai.com/)
- Public HTTPS URL (e.g. [ngrok](https://ngrok.com)) for webhooks and OAuth redirect
- Optional: Google Cloud OAuth client (Calendar + Contacts scopes), Recall.ai API key

## Quick start

```bash
git clone https://github.com/Talha-229/Whatsapp-Project-Manager.git
cd Whatsapp-Project-Manager

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

1. Copy environment file and edit:

   ```bash
   copy .env.example .env   # Windows
   # cp .env.example .env   # Unix
   ```

2. Apply SQL migrations in Supabase (SQL editor or CLI): run files in `supabase/migrations/` in order (`001` … `007`).

3. Start the API:

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload
   ```

4. Point **Meta WhatsApp** webhook to `https://<your-public-host>/webhooks/whatsapp` and **Recall** (if used) to `https://<your-public-host>/webhooks/recall`.

## Deploy on Render

1. Ensure the repo root contains `render.yaml` (Blueprint) and `runtime.txt` (Python 3.12).
2. In [Render](https://render.com), create a **Blueprint**, connect `Talha-229/Whatsapp-Project-Manager`, branch `main`, path `render.yaml`.
3. After deploy, open the web service **Environment** and fill every variable (same as local `.env`). Set `PUBLIC_BASE_URL` to your service URL, e.g. `https://whatsapp-bot.onrender.com` (no trailing slash). Set `GOOGLE_REDIRECT_URI` to `{PUBLIC_BASE_URL}/oauth/google/callback`.
4. Update **Meta** WhatsApp and **Recall** webhook URLs to use `PUBLIC_BASE_URL`, and add the Google OAuth redirect URI in Google Cloud Console.

## Configuration (environment variables)

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | Signs Google OAuth state (Calendar linking); use a long random string |
| `APP_NAME` | API title (default `WhatsApp Agent API`) |
| `DEBUG` | `true` / `false` |
| `LANGGRAPH_MAX_CLARIFY_TURNS` | Max clarification turns before the agent answers (default `2`) |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key (server-side only) |
| `DATABASE_URL` | Postgres URI for LangGraph checkpoints (Supabase DB) |
| `META_WA_VERIFY_TOKEN` | Webhook verification token |
| `META_WA_ACCESS_TOKEN` | WhatsApp Cloud API token |
| `META_WA_PHONE_NUMBER_ID` | Phone number ID |
| `META_WA_APP_SECRET` | App secret (signature verification) |
| `PUBLIC_BASE_URL` | Public base URL of this API (no trailing slash) |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_MODEL` | Chat model (default `gpt-4o-mini`) |
| `WHISPER_MODEL` | Whisper model for voice (default `whisper-1`) |
| `DEFAULT_TZ` | Default IANA timezone (e.g. `Asia/Karachi`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth client |
| `GOOGLE_REDIRECT_URI` | Must match OAuth client; e.g. `{PUBLIC_BASE_URL}/oauth/google/callback` |
| `GOOGLE_TOKEN_ENCRYPTION_KEY` | Fernet key (base64) for storing refresh tokens |
| `RECALL_API_KEY` | Optional Recall.ai |
| `RECALL_REGION` | e.g. `ap-northeast-1` |
| `RECALL_WEBHOOK_SECRET` | Svix secret from Recall dashboard |

See `.env.example` for a template.

## Project layout

```
app/
  agents/          # LangGraph orchestrator, tools (calendar, tasks, projects, policies)
  jobs/            # Reminder cron jobs
  oauth/           # Google OAuth routes
  services/        # Calendar, Recall, meeting scheduler, etc.
  webhooks/        # Recall Svix webhook
  whatsapp/        # Meta webhook, media/transcription
supabase/migrations/
```

## Security notes

- Do **not** commit `.env` or service role keys.
- Use HTTPS for all webhooks and OAuth redirects in production.

## License

Use and modify as needed for your deployment.
