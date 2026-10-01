from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from .config import Settings, settings
from .db import make_session_factory
from .ingest import router as ingest_router
from .web import router as web_router
from .workflows import router as workflows_router
from .mobile import router as mobile_router
from .operations import router as operations_router
from .invoices import router as invoices_router
from .marketing import router as marketing_router
from .preferences import router as preferences_router
from .food_porn import router as food_porn_router
from .bot_admin import router as bot_admin_router
from .meta_leads import router as meta_leads_router
from .growth_reports import router as growth_reports_router
from .config_releases import router as config_releases_router
from .bot_canvas import router as bot_canvas_router
from .finance_hub import router as finance_router
from .bot_databases import router as bot_databases_router
from .bot_read_api import router as bot_read_router


def create_app(config: Settings | None = None) -> FastAPI:
    config = config or settings()
    config.validate()
    engine, factory = make_session_factory(config.database_url)
    app = FastAPI(title='OmniFactory CRM', version='1.11.0', docs_url=None, redoc_url=None)
    app.state.settings = config
    app.state.engine = engine
    app.state.session_factory = factory
    root = Path(__file__).parent
    app.state.templates = Jinja2Templates(directory=root / 'templates')
    app.add_middleware(SessionMiddleware, secret_key=config.session_secret, https_only=config.cookie_secure, same_site='lax', max_age=28800)
    app.mount('/static', StaticFiles(directory=root / 'static'), name='static')
    app.include_router(ingest_router)
    app.include_router(bot_read_router)
    app.include_router(web_router)
    app.include_router(workflows_router)
    app.include_router(mobile_router)
    app.include_router(operations_router)
    app.include_router(invoices_router)
    app.include_router(marketing_router)
    app.include_router(meta_leads_router)
    app.include_router(growth_reports_router)
    app.include_router(food_porn_router)
    app.include_router(bot_admin_router)
    app.include_router(bot_canvas_router)
    app.include_router(finance_router)
    app.include_router(bot_databases_router)
    app.include_router(config_releases_router)
    app.include_router(preferences_router)

    @app.exception_handler(StarletteHTTPException)
    async def browser_authentication(request, exc):
        if (exc.status_code == 401 and exc.detail == 'Authentication required'
                and request.method == 'GET'
                and 'text/html' in request.headers.get('accept', '').lower()
                and not request.url.path.startswith('/api/')):
            return RedirectResponse('/login', status_code=303)
        return await http_exception_handler(request, exc)

    @app.get('/health/live')
    def live(): return {'status': 'up'}

    @app.get('/health/ready')
    def ready():
        from sqlalchemy import text
        try:
            with engine.connect() as connection: connection.execute(text('SELECT 1'))
            return {'status': 'ready'}
        except Exception:
            return JSONResponse({'status':'unavailable'}, status_code=503)

    return app


app = create_app()
