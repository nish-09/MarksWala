from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import Base
import datetime

class Resource(Base):
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    resource_type = Column(String(50), nullable=False) # e.g. PDF, PPTX
    course_id = Column(Integer, ForeignKey("course.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String(20), default="PROCESSING")  # PROCESSING, COMPLETED, FAILED
    chunk_count = Column(Integer, nullable=True)
    error_message = Column(String(500), nullable=True)

    course = relationship("Course", back_populates="resources")
