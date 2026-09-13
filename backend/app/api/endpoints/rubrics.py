from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.db.session import get_db
from app.api import deps
from app.models.user import User
from app.models.question import Question
from app.models.rubric import Rubric, RubricCriterion
from pydantic import BaseModel

router = APIRouter()

class CriterionOut(BaseModel):
    id: int
    description: str
    marks: float
    order: int
    
    class Config:
        from_attributes = True

class RubricOut(BaseModel):
    id: int
    question_id: int
    is_approved: bool
    criteria: List[CriterionOut]
    
    class Config:
        from_attributes = True

@router.get("/question/{question_id}", response_model=RubricOut)
def get_rubric_by_question(
    question_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    deps.get_owned_question(question_id, db, current_user)
    rubric = db.query(Rubric).filter(Rubric.question_id == question_id).first()
    if not rubric:
        # Create an empty rubric if it doesn't exist (e.g. if AI generation failed)
        rubric = Rubric(question_id=question_id, is_approved=False)
        db.add(rubric)
        db.commit()
        db.refresh(rubric)
    return rubric

class CriterionUpdate(BaseModel):
    description: str
    marks: float
    order: int

class RubricUpdate(BaseModel):
    is_approved: bool
    criteria: List[CriterionUpdate]

@router.put("/{rubric_id}", response_model=RubricOut)
def update_rubric(
    rubric_id: int, 
    rubric_in: RubricUpdate, 
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    rubric = deps.get_owned_rubric(rubric_id, db, current_user)

    question = db.query(Question).filter(Question.id == rubric.question_id).first()
    total_criteria_marks = round(sum(c.marks for c in rubric_in.criteria), 2)
    if question and abs(total_criteria_marks - question.marks) > 0.01:
        raise HTTPException(
            status_code=400,
            detail=f"Rubric criteria must sum to the question's marks ({question.marks}), got {total_criteria_marks}.",
        )
    if any(c.marks < 0 for c in rubric_in.criteria):
        raise HTTPException(status_code=400, detail="Criterion marks cannot be negative.")

    rubric.is_approved = rubric_in.is_approved

    # Delete old criteria and insert new
    db.query(RubricCriterion).filter(RubricCriterion.rubric_id == rubric_id).delete()
    
    for crit in rubric_in.criteria:
        rc = RubricCriterion(
            rubric_id=rubric.id,
            description=crit.description,
            marks=crit.marks,
            order=crit.order
        )
        db.add(rc)
        
    db.commit()
    db.refresh(rubric)
    return rubric
