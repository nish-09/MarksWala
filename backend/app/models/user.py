from sqlalchemy import Column, Integer, String, Boolean
from app.db.base_class import Base

class User(Base):
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), index=True)
    is_active = Column(Boolean(), default=True)
    is_superuser = Column(Boolean(), default=False)
