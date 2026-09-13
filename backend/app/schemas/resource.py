from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class ResourceBase(BaseModel):
    filename: str
    resource_type: str

class ResourceCreate(ResourceBase):
    course_id: int
    file_path: str

class ResourceOut(ResourceBase):
    id: int
    course_id: int
    uploaded_at: datetime
    status: str
    chunk_count: Optional[int] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True
