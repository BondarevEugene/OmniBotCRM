"""Per-session appearance and language controls."""
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .security import csrf

router = APIRouter()


@router.post('/preferences')
def preferences(request: Request, token: str = Form(), theme: str = Form('light'), locale: str = Form('ru')):
    csrf(request, token)
    request.session['theme'] = theme if theme in ('light', 'dark') else 'light'
    request.session['locale'] = locale if locale in ('ru', 'en') else 'ru'
    referer = urlsplit(request.headers.get('referer', ''))
    destination = referer.path if referer.netloc == request.url.netloc and referer.path.startswith('/') else '/'
    return RedirectResponse(destination, status_code=303)
