from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.services.live_refresh_service import LiveRefreshService
from app.web.routes.today_matches_routes import router as today_matches_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Start and stop background services tied to the application lifecycle.
    """
    live_refresh_service = LiveRefreshService(interval_seconds=60)
    app.state.live_refresh_service = live_refresh_service

    await live_refresh_service.start()

    try:
        yield
    finally:
        await live_refresh_service.stop()


app = FastAPI(
    title="Football Value Bets",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

app.include_router(today_matches_router)


@app.get("/", include_in_schema=False)
def root_redirect():
    """
    Redirect the root URL to the /matches/today page.
    """
    return RedirectResponse(url="/matches/today")
