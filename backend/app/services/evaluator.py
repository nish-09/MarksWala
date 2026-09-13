import json
import chromadb
from app.core.config import settings
from app.models.question import Question
from app.models.rubric import Rubric
from app.services.llm_client import generate_json

chroma_client = chromadb.PersistentClient(path=settings.VECTOR_DB_PATH)


def evaluate_answer(answer_text: str, question: Question, rubric: Rubric, course_id: int) -> dict:
    # 1. Retrieve context
    collection = chroma_client.get_or_create_collection(name=f"course_{course_id}")
    results = collection.query(
        query_texts=[question.text],
        n_results=3
    )

    retrieved_context = ""
    if results and results["documents"] and results["documents"][0]:
        retrieved_context = "\n".join(results["documents"][0])

    # 2. Build evaluation prompt
    rubric_json = [{"id": c.id, "desc": c.description, "marks": c.marks} for c in rubric.criteria]

    if not answer_text or not answer_text.strip():
        return {
            "feedback": "No answer was found for this question.",
            "evaluation_confidence": 1.0,
            "criteria_evaluation": [{"criterion_id": c["id"], "score": 0.0, "evidence": "No answer provided."} for c in rubric_json],
        }

    prompt = f"""
    You are an expert university evaluator. Evaluate the student's answer against the rubric.

    Question: {question.text} (Total Marks: {question.marks})

    Course Material Reference (Use this as authoritative knowledge):
    {retrieved_context}

    Rubric:
    {json.dumps(rubric_json, indent=2)}

    Student's Answer (OCR Text):
    {answer_text}

    Evaluate criterion-by-criterion. Award partial marks if necessary. Do not award marks for
    content that is off-topic or unrelated to the question, even if it is factually correct.
    Return ONLY a raw JSON object with this EXACT format, no markdown blocks, no extra text:
    {{
      "feedback": "Specific, actionable feedback referencing what was missing or well done.",
      "evaluation_confidence": 0.85,
      "criteria_evaluation": [
        {{
          "criterion_id": 1,
          "score": 1.5,
          "evidence": "Student correctly defined the process."
        }}
      ]
    }}
    """

    data = generate_json(prompt, think=True)
    if not isinstance(data, dict):
        return {}

    # The LLM never controls the final mark directly: the backend recalculates
    # the total as the sum of criterion scores, each clamped to its own max,
    # and the whole total clamped to the question's max marks.
    criteria_by_id = {c.id: c for c in rubric.criteria}
    valid_criteria_evals = []
    total_score = 0.0

    for ce in data.get("criteria_evaluation", []):
        criterion = criteria_by_id.get(ce.get("criterion_id"))
        if not criterion:
            continue
        try:
            score = float(ce.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(score, criterion.marks))
        total_score += score
        valid_criteria_evals.append({
            "criterion_id": criterion.id,
            "score": score,
            "evidence": ce.get("evidence", ""),
        })

    total_score = max(0.0, min(total_score, question.marks))

    return {
        "total_score": round(total_score, 2),
        "feedback": data.get("feedback", ""),
        "evaluation_confidence": data.get("evaluation_confidence", 0.0),
        "criteria_evaluation": valid_criteria_evals,
    }
