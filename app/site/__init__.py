import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/site/templates")

_common = {
    "project_name": os.getenv("PROJECT_NAME", "Помни"),
    "bot_url": os.getenv("PUBLIC_BOT_URL", ""),
    "contact_email": os.getenv("CONTACT_EMAIL", ""),
    "inn_selfemp": os.getenv("INN_SELFEMP", ""),
    "full_name": (
        os.getenv("SELFEMP_FULLNAME")
        or os.getenv("LEGAL_OWNER_NAME")
        or os.getenv("OWNER_FULL_NAME")
        or ""
    ),
}

@router.get("/", response_class=HTMLResponse)
def homepage(request: Request):
    return templates.TemplateResponse("home.html", {"request": request, **_common})

@router.get("/requisites", response_class=HTMLResponse)
def requisites(request: Request):
    return templates.TemplateResponse("requisites.html", {"request": request, **_common})
