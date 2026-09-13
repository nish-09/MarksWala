from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class ExamBase(BaseModel):
    title: str

class ExamCreate(ExamBase):
    course_id: int

class ExamOut(ExamBase):
    id: int
    course_id: int
    created_at: datetime

    class Config:
        from_attributes = True
