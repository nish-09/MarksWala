from pydantic import BaseModel
from typing import Optional

class CourseBase(BaseModel):
    title: str
    description: Optional[str] = None

class CourseCreate(CourseBase):
    pass

class CourseOut(CourseBase):
    id: int
    teacher_id: int

    class Config:
        from_attributes = True
