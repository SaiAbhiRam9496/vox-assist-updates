from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from backend.routes import api, user
from backend.database.connection import connect_to_mongo, close_mongo_connection, create_database_indexes
import backend.database.connection
from backend.utils.rate_limit import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import uvicorn
import os
import logging

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Fast DB connection
    await connect_to_mongo()
    
    # Offload slow index creation to background
    import asyncio
    asyncio.create_task(create_database_indexes())
    
    yield
    
    # Shutdown
    await close_mongo_connection()

app = FastAPI(
    title="VoxAssist Backend Core",
    description="The underlying compute and orchestration API serving the VoxAssist AI generator platform.",
    version="2.0.0",
    lifespan=lifespan,
    contact={
        "name": "VoxAssist Developer Support",
        "email": "support@voxassist.com",
    }
)

# Rate Limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Global Error Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.error(f"Global exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": str(exc), "message": "Internal Server Error"}
    )

# Helper to load env for CORS
# For now, allowing all for development
origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "https://vox-assist.vercel.app",
    "https://vox-assist-pearl.vercel.app",
    "https://vox-assist-frontend.onrender.com"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.staticfiles import StaticFiles

app.include_router(api.router, prefix="/api/v1")
app.include_router(user.router, prefix="/api/v1")

# Mount static files
# Mount static files (for 3D models, etc.)
os.makedirs("backend/static/models", exist_ok=True)
app.mount("/static", StaticFiles(directory="backend/static"), name="static")

# Lifespan handles startup/shutdown now

@app.get("/")
def read_root():
    return {"message": "Welcome to VOX-ASSIST API"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "vox-assist-backend"}

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
# Force reload trigger 30
