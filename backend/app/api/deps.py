from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.core.config import settings
from app.models.user import User
from app.schemas.token import TokenData

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login/access-token")

def get_current_user(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.email == token_data.email).first()
    if user is None:
        raise credentials_exception
    return user


def get_owned_exam(exam_id: int, db: Session, current_user: User):
    """Return the Exam only if it belongs (via its Course) to current_user, else 404.

    Centralizes teacher-isolation checks for every endpoint keyed by exam_id so
    one teacher's exams/questions/rubrics/answers can never leak to another.
    """
    from app.models.exam import Exam
    from app.models.course import Course

    exam = (
        db.query(Exam)
        .join(Course, Exam.course_id == Course.id)
        .filter(Exam.id == exam_id, Course.teacher_id == current_user.id)
        .first()
    )
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    return exam


def get_owned_question(question_id: int, db: Session, current_user: User):
    from app.models.question import Question
    from app.models.exam import Exam
    from app.models.course import Course

    question = (
        db.query(Question)
        .join(Exam, Question.exam_id == Exam.id)
        .join(Course, Exam.course_id == Course.id)
        .filter(Question.id == question_id, Course.teacher_id == current_user.id)
        .first()
    )
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


def get_owned_rubric(rubric_id: int, db: Session, current_user: User):
    from app.models.rubric import Rubric
    from app.models.question import Question
    from app.models.exam import Exam
    from app.models.course import Course

    rubric = (
        db.query(Rubric)
        .join(Question, Rubric.question_id == Question.id)
        .join(Exam, Question.exam_id == Exam.id)
        .join(Course, Exam.course_id == Course.id)
        .filter(Rubric.id == rubric_id, Course.teacher_id == current_user.id)
        .first()
    )
    if not rubric:
        raise HTTPException(status_code=404, detail="Rubric not found")
    return rubric


def get_owned_answer(answer_id: int, db: Session, current_user: User):
    from app.models.evaluation import Answer, AnswerSheet
    from app.models.exam import Exam
    from app.models.course import Course

    answer = (
        db.query(Answer)
        .join(AnswerSheet, Answer.answer_sheet_id == AnswerSheet.id)
        .join(Exam, AnswerSheet.exam_id == Exam.id)
        .join(Course, Exam.course_id == Course.id)
        .filter(Answer.id == answer_id, Course.teacher_id == current_user.id)
        .first()
    )
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")
    return answer
