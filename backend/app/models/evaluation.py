from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, Boolean, DateTime
from sqlalchemy.orm import relationship
from app.db.base_class import Base
import datetime

class Student(Base):
    id = Column(Integer, primary_key=True, index=True)
    roll_number = Column(String(100), index=True)
    name = Column(String(255))

class AnswerSheet(Base):
    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("exam.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("student.id"), nullable=False)
    file_path = Column(String(500), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String(20), default="PROCESSING")  # PROCESSING, COMPLETED, FAILED
    error_message = Column(String(500), nullable=True)

    exam = relationship("Exam")
    student = relationship("Student")
    answers = relationship("Answer", back_populates="answer_sheet")

class Answer(Base):
    id = Column(Integer, primary_key=True, index=True)
    answer_sheet_id = Column(Integer, ForeignKey("answersheet.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("question.id"), nullable=False)
    ocr_text = Column(Text, nullable=True)
    corrected_text = Column(Text, nullable=True)
    page_image_path = Column(String(500), nullable=True)
    
    # Confidence metrics
    ocr_confidence = Column(Float, nullable=True)
    mapping_confidence = Column(Float, nullable=True)
    
    answer_sheet = relationship("AnswerSheet", back_populates="answers")
    question = relationship("Question")
    evaluation = relationship("Evaluation", uselist=False, back_populates="answer")

class Evaluation(Base):
    id = Column(Integer, primary_key=True, index=True)
    answer_id = Column(Integer, ForeignKey("answer.id"), nullable=False)
    total_score = Column(Float, nullable=False, default=0.0)
    ai_score = Column(Float, nullable=True)
    feedback = Column(Text, nullable=True)
    evaluation_confidence = Column(Float, nullable=True)
    is_reviewed = Column(Boolean, default=False)
    teacher_override = Column(Boolean, default=False)
    override_reason = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    
    answer = relationship("Answer", back_populates="evaluation")
    criterion_scores = relationship("CriterionScore", back_populates="evaluation")

class CriterionScore(Base):
    id = Column(Integer, primary_key=True, index=True)
    evaluation_id = Column(Integer, ForeignKey("evaluation.id"), nullable=False)
    criterion_id = Column(Integer, ForeignKey("rubriccriterion.id"), nullable=False)
    score = Column(Float, nullable=False)
    evidence = Column(Text, nullable=True)
    
    evaluation = relationship("Evaluation", back_populates="criterion_scores")
    criterion = relationship("RubricCriterion")
