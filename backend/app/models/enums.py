"""Domain enumerations (stored as VARCHAR + CHECK constraints)."""
import enum


class UserRole(str, enum.Enum):
    TEACHER = "TEACHER"
    ADMIN = "ADMIN"


class CourseRole(str, enum.Enum):
    OWNER = "OWNER"
    INSTRUCTOR = "INSTRUCTOR"
    VIEWER = "VIEWER"


class ResourceStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PaperStatus(str, enum.Enum):
    NONE = "NONE"
    PROCESSING = "PROCESSING"
    PARSED = "PARSED"
    FAILED = "FAILED"


class RubricVersionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


class RubricSource(str, enum.Enum):
    AI = "AI"
    TEACHER = "TEACHER"


class SheetStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"  # pages extracted, OCR'd and answers mapped
    EVALUATING = "EVALUATING"
    EVALUATED = "EVALUATED"
    FAILED = "FAILED"


class BatchStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class MappingMethod(str, enum.Enum):
    AI = "AI"
    RULE = "RULE"
    TEACHER = "TEACHER"


class EvaluationStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TextSource(str, enum.Enum):
    ORIGINAL = "ORIGINAL"
    CORRECTED = "CORRECTED"


class FlagKind(str, enum.Enum):
    LOW_OCR = "LOW_OCR"
    LOW_MAPPING = "LOW_MAPPING"
    LOW_RETRIEVAL = "LOW_RETRIEVAL"
    LOW_RUBRIC = "LOW_RUBRIC"
    LOW_EVALUATION = "LOW_EVALUATION"
    LOW_OVERALL = "LOW_OVERALL"
    MISSING_ANSWER = "MISSING_ANSWER"
    UNREADABLE = "UNREADABLE"
    STUDENT_UNIDENTIFIED = "STUDENT_UNIDENTIFIED"
    INVALID_AI_OUTPUT = "INVALID_AI_OUTPUT"
    EVALUATION_FAILED = "EVALUATION_FAILED"


class FlagSeverity(str, enum.Enum):
    RECOMMENDED = "RECOMMENDED"
    MANDATORY = "MANDATORY"


class ReviewStatus(str, enum.Enum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"


class ReviewDecision(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    OVERRIDDEN = "OVERRIDDEN"


class ResultStatus(str, enum.Enum):
    PROVISIONAL = "PROVISIONAL"  # some reviews / evaluations still outstanding
    FINAL = "FINAL"


class JobKind(str, enum.Enum):
    PROCESS_RESOURCE = "PROCESS_RESOURCE"
    PARSE_QUESTION_PAPER = "PARSE_QUESTION_PAPER"
    GENERATE_RUBRIC = "GENERATE_RUBRIC"
    PROCESS_ANSWER_SHEET = "PROCESS_ANSWER_SHEET"
    EVALUATE_ANSWER_SHEET = "EVALUATE_ANSWER_SHEET"
    GENERATE_FEEDBACK = "GENERATE_FEEDBACK"


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


ACTIVE_JOB_STATUSES = (JobStatus.QUEUED.value, JobStatus.PROCESSING.value)
