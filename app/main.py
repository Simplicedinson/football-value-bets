from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.web.routes.today_matches_routes import router as today_matches_router


app = FastAPI(title="Football Value Bets")

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

app.include_router(today_matches_router)


@app.get("/", include_in_schema=False)
def root_redirect():
    """
    Redirect the root URL to the /matches/today page.
    """
    return RedirectResponse(url="/matches/today")