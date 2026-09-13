from fastapi import APIRouter
from app.api.endpoints import auth, courses, exams, resources, questions, rubrics, answers, analytics

api_router = APIRouter()

api_router.include_router(auth.router, tags=["login"])
api_router.include_router(courses.router, prefix="/courses", tags=["courses"])
api_router.include_router(exams.router, prefix="/exams", tags=["exams"])
api_router.include_router(resources.router, prefix="/resources", tags=["resources"])
api_router.include_router(questions.router, prefix="/questions", tags=["questions"])
api_router.include_router(rubrics.router, prefix="/rubrics", tags=["rubrics"])
api_router.include_router(answers.router, prefix="/answers", tags=["answers"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])

@api_router.get("/health")
def health_check():
    return {"status": "ok"}
