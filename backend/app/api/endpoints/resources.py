import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
from app.db.session import get_db
from app.api import deps
from app.models.user import User
from app.models.resource import Resource
from app.models.course import Course
from app.schemas.resource import ResourceOut
from app.services.ingestion import process_resource

router = APIRouter()

UPLOAD_DIR = "uploads"

@router.post("/", response_model=ResourceOut)
async def upload_resource(
    course_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    course = db.query(Course).filter(Course.id == course_id, Course.teacher_id == current_user.id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
        
    ext = file.filename.split(".")[-1] if "." in file.filename else "pdf"
    unique_filename = f"{uuid.uuid4()}.{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(file_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
        
    resource = Resource(
        filename=file.filename,
        file_path=file_path,
        resource_type=ext.upper(),
        course_id=course_id
    )
    db.add(resource)
    db.commit()
    db.refresh(resource)
    
    background_tasks.add_task(process_resource, resource.id)
    
    return resource

@router.get("/", response_model=List[ResourceOut])
def read_resources(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    course = db.query(Course).filter(Course.id == course_id, Course.teacher_id == current_user.id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
        
    resources = db.query(Resource).filter(Resource.course_id == course_id).all()
    return resources
