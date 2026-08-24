import os
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.v1.routers import main_router
from src.core.config import STATIC_DIR, UPLOAD_DIR, settings
from src.core.init_db import create_first_superuser
from src.marketplaces.service import (
    close_marketplace_services,
    configure_marketplace_runtime,
    start_marketplace_services,
)


load_dotenv()
os.makedirs(UPLOAD_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f'Приложение запущено! Дата: {datetime.now()}')
    configure_marketplace_runtime('api')
    try:
        await start_marketplace_services()
        await create_first_superuser()
        yield
    finally:
        await close_marketplace_services()
        print(f'Приложение остановлено! Дата: {datetime.now()}')


app = FastAPI(
    title=settings.title,
    description=settings.description,
    lifespan=lifespan
)
app.router.include_router(main_router)
app.mount(STATIC_DIR, StaticFiles(directory=UPLOAD_DIR), name='media')
