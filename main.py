from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from admin.routes import router
from bot.service import bot_service


BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_service.start()
    yield
    bot_service.stop()


app = FastAPI(title="WhatsApp Bot Admin", lifespan=lifespan)
app.include_router(router)

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)

templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
    )


@app.get("/conversas", response_class=HTMLResponse)
def conversations(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="conversas.html",
    )


@app.get("/fila", response_class=HTMLResponse)
def queue_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="fila.html",
    )
