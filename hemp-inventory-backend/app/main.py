from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.database import init_db
from app.routers import auth_router, locations_router, inventory_router, par_router, alerts_router, ecommerce_router, loyalty_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Hemp Dispensary Inventory Manager", lifespan=lifespan)

# Disable CORS. Do not remove this for full-stack development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

app.include_router(auth_router.router)
app.include_router(locations_router.router)
app.include_router(inventory_router.router)
app.include_router(par_router.router)
app.include_router(alerts_router.router)
app.include_router(ecommerce_router.router)
app.include_router(loyalty_router.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
