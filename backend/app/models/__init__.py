"""Import every model so Base.metadata is complete (Alembic and tests rely on this)."""
from app.models.answers import Answer, AnswerMapping, AnswerPage, AnswerSheet, AnswerSheetBatch, OcrResult
from app.models.evaluation import (
    ConfidenceFlag,
    Evaluation,
    EvaluationCriterion,
    EvaluationSource,
    ExamAnalytics,
    StudentResult,
    TeacherOverride,
    TeacherReview,
    TopicResult,
)
from app.models.exams import Exam, Question, Rubric, RubricCriterion, RubricVersion, Subquestion
from app.models.identity import Course, CourseMember, Student, User, UserSession
from app.models.resources import Resource, ResourceChunk, ResourceFile
from app.models.system import AuditLog, ProcessingJob

__all__ = [
    "Answer", "AnswerMapping", "AnswerPage", "AnswerSheet", "AnswerSheetBatch", "AuditLog", "ConfidenceFlag",
    "Course", "CourseMember", "Evaluation", "EvaluationCriterion", "EvaluationSource", "Exam", "ExamAnalytics",
    "OcrResult", "ProcessingJob", "Question", "Resource", "ResourceChunk", "ResourceFile", "Rubric",
    "RubricCriterion", "RubricVersion", "Student", "StudentResult", "Subquestion", "TeacherOverride",
    "TeacherReview", "TopicResult", "User", "UserSession",
]
