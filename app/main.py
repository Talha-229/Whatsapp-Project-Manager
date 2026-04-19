import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.orchestrator_graph import compile_orchestrator
from app.config import get_settings
from app.db.checkpoint import init_checkpoint_pool, shutdown_checkpoint_pool
from app.jobs.reminders import setup_scheduler
from app.oauth.google import router as google_oauth_router
from app.webhooks.recall_router import router as recall_router
from app.whatsapp.meta_webhook import router as wa_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    s = get_settings()
    if (s.database_url or "").strip():
        cp = init_checkpoint_pool(s.database_url.strip())
        logger.info("Using Postgres checkpointer for LangGraph")
    else:
        logger.warning(
            "DATABASE_URL not set; using InMemorySaver (conversation state is lost on restart). "
            "Set DATABASE_URL to your Supabase Postgres connection string for persistence."
        )
        cp = InMemorySaver()
    compile_orchestrator(cp)

    scheduler = BackgroundScheduler()
    setup_scheduler(scheduler)
    scheduler.start()
    logger.info("APScheduler started")
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    shutdown_checkpoint_pool()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, lifespan=lifespan)
    app.include_router(wa_router)
    app.include_router(recall_router)
    app.include_router(google_oauth_router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
