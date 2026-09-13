from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import Base
import datetime

class Exam(Base):
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), index=True, nullable=False)
    course_id = Column(Integer, ForeignKey("course.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    question_paper_status = Column(String(20), nullable=True)  # PROCESSING, COMPLETED, FAILED
    question_paper_error = Column(String(500), nullable=True)

    course = relationship("Course", back_populates="exams")
    questions = relationship("Question", back_populates="exam")
