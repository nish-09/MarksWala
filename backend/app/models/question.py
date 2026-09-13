from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.db.base_class import Base

class Question(Base):
    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("exam.id"), nullable=False)
    question_number = Column(String(50), nullable=False)
    text = Column(Text, nullable=False)
    marks = Column(Float, nullable=False)
    
    exam = relationship("Exam", back_populates="questions")
    rubric = relationship("Rubric", uselist=False, back_populates="question")
