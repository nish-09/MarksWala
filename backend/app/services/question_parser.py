import fitz
from app.services.llm_client import generate_json


def parse_question_paper(file_path: str, exam_id: int) -> list[dict]:
    text = ""
    try:
        doc = fitz.open(file_path)
        for page in doc:
            text += page.get_text()
    except Exception as e:
        print(f"Error reading question paper PDF: {e}")
        return []

    prompt = f"""
    You are an expert exam parser. Read the following question paper text and extract all questions and subquestions.
    Return ONLY a raw JSON list with this EXACT format, no markdown blocks, no extra text:
    [
      {{
        "question_number": "1",
        "text": "What is an Operating System?",
        "marks": 5.0
      }},
      {{
        "question_number": "1(a)",
        "text": "Define Process.",
        "marks": 2.5
      }}
    ]

    Text:
    {text}
    """

    questions = generate_json(prompt, think=True)
    if not isinstance(questions, list):
        return []
    return questions
