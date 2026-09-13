from sqlalchemy import Column, Integer, String, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.db.base_class import Base

class Course(Base):
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), index=True, nullable=False)
    description = Column(Text, nullable=True)
    teacher_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    
    teacher = relationship("User", backref="courses")
    exams = relationship("Exam", back_populates="course")
    resources = relationship("Resource", back_populates="course")
