import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()

class Settings(BaseSettings):
    PROJECT_NAME: str = "MarksWala"
    DATABASE_URI: str = os.getenv("DATABASE_URI", "sqlite:///./markswala.db")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "super-secret-key-for-dev")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7 # 7 days
    
    VECTOR_DB_PATH: str = os.getenv("VECTOR_DB_PATH", "./chroma_db")

    # Gemini API - no local model required. Get a key at https://aistudio.google.com/apikey
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_TEXT_MODEL: str = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
    GEMINI_VISION_MODEL: str = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
    
    class Config:
        case_sensitive = True

settings = Settings()
