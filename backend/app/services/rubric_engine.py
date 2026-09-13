from app.services.llm_client import generate_json


def generate_rubric(question_text: str, marks: float) -> list[dict]:
    prompt = f"""
    You are an expert professor. Given a question and its total marks, generate a precise grading rubric.
    Break down the marks into logical criteria. The total marks of all criteria MUST exactly equal {marks}.

    Question: {question_text}
    Total Marks: {marks}

    Return ONLY a raw JSON list with this EXACT format, no markdown blocks, no extra text:
    [
      {{
        "description": "Definition of process",
        "marks": 1.5,
        "order": 1
      }},
      {{
        "description": "Explanation of states",
        "marks": 3.5,
        "order": 2
      }}
    ]
    """

    criteria = generate_json(prompt, think=False)
    if not isinstance(criteria, list):
        return []

    # The LLM is only a drafting aid - the backend is the source of truth for
    # whether a rubric is valid. If criteria marks don't sum to the question's
    # marks, rescale them proportionally so grading math always stays exact.
    total = sum(float(c.get("marks", 0)) for c in criteria)
    if total > 0 and abs(total - marks) > 0.01:
        scale = marks / total
        for c in criteria:
            c["marks"] = round(float(c.get("marks", 0)) * scale, 2)

    return criteria
