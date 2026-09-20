from fastapi import APIRouter


def register_routers(api: APIRouter) -> None:
    from app.api.routers import auth, courses, dashboard, exams, jobs, resources, results, reviews, rubrics, sheets

    api.include_router(auth.router)
    api.include_router(dashboard.router)
    api.include_router(courses.router)
    api.include_router(resources.router)
    api.include_router(exams.router)
    api.include_router(rubrics.router)
    api.include_router(sheets.router)
    api.include_router(reviews.router)
    api.include_router(results.router)
    api.include_router(jobs.router)
