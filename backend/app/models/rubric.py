from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from app.db.base_class import Base

class Rubric(Base):
    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("question.id"), nullable=False)
    is_approved = Column(Boolean, default=False)
    
    question = relationship("Question", back_populates="rubric")
    criteria = relationship("RubricCriterion", back_populates="rubric")

class RubricCriterion(Base):
    id = Column(Integer, primary_key=True, index=True)
    rubric_id = Column(Integer, ForeignKey("rubric.id"), nullable=False)
    description = Column(Text, nullable=False)
    marks = Column(Float, nullable=False)
    order = Column(Integer, default=0)
    
    rubric = relationship("Rubric", back_populates="criteria")
