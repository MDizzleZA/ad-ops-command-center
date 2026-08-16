from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import MEDIA_DIR, WEB_DIR
from app.routers import (briefs, clients, cloner, creatives, dashboard, grader, organic, pipeline,
                         publish, scans, spy, sync)
from app.services import scheduler
from app.services.apify import ApifyError
from app.services.gemini import GeminiError

app = FastAPI(title='Ad Ops Command Center')

for router_module in (clients, dashboard, organic, sync, creatives, briefs, cloner, scans, spy,
                      grader, pipeline, publish):
    app.include_router(router_module.router)


@app.exception_handler(ApifyError)
@app.exception_handler(GeminiError)
def service_error_handler(request, exc):
    """Missing tokens / refused generations are user-fixable: surface the message, not a 500."""
    return JSONResponse(status_code=400, content={'detail': str(exc)})

app.mount('/media', StaticFiles(directory=MEDIA_DIR), name='media')
app.mount('/web', StaticFiles(directory=WEB_DIR), name='web')


@app.middleware('http')
async def no_cache_app_code(request, call_next):
    """Local dev server: force revalidation of JS/CSS so edits show up on plain reload."""
    response = await call_next(request)
    if request.url.path.startswith('/web') or request.url.path == '/':
        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.get('/')
def index():
    return FileResponse(WEB_DIR / 'index.html')


@app.on_event('startup')
def startup():
    db.migrate()
    scheduler.start()


@app.on_event('shutdown')
def shutdown():
    scheduler.stop()
