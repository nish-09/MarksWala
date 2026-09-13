from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.api_v1 import api_router
from app.db.session import engine
from app.models import Base

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="MarksWala API",
    description="AI-Assisted Evaluation. Teacher Approved.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")

@app.get("/")
def root():
    return {"message": "Welcome to MarksWala API"}
