import os
import fitz
from app.services.llm_client import generate_json


def pdf_to_images(pdf_path: str, output_dir: str) -> list[str]:
    doc = fitz.open(pdf_path)
    image_paths = []

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    for i in range(len(doc)):
        page = doc.load_page(i)
        pix = page.get_pixmap()
        img_path = os.path.join(output_dir, f"page_{i}.png")
        pix.save(img_path)
        image_paths.append(img_path)

    return image_paths


def extract_text_and_map(image_path: str, questions: list[dict]) -> list[dict]:
    """
    Returns a list of mapped answer segments found on the page, using the Gemini
    vision model. A single page commonly contains answers to more than one
    question, so this always returns a list (one entry per question answered
    on this page), never a single merged blob:
    [
      {
         "question_number": "1(a)",
         "ocr_text": "Extracted text here...",
         "ocr_confidence": 0.85,
         "mapping_confidence": 0.6
      },
      ...
    ]

    ocr_confidence reflects how legible/certain the transcription itself is.
    mapping_confidence reflects how certain the model is that the transcribed
    text actually answers the question_number it picked (e.g. no explicit
    numbering on the page, ambiguous between two questions, etc.) - low
    mapping_confidence is what should route an answer to teacher review.
    """
    questions_context = "\n".join([f"Q{q['question_number']}: {q['text']}" for q in questions])

    prompt = f"""
    You are an expert OCR system for handwritten university exams.
    Read the handwriting in this image carefully.
    This single page may contain the student's answer to ONE question, PART of one
    question's answer (continued from/onto another page), or answers to SEVERAL
    questions if the student wrote compactly.

    Possible questions on this exam:
    {questions_context}

    Return a JSON list, one entry per distinct question answered on this page:
    [
      {{
          "question_number": "The question number this segment answers, e.g., '1(a)'",
          "ocr_text": "The exact transcribed handwritten text for just this segment",
          "ocr_confidence": 0.90,
          "mapping_confidence": 0.90
      }}
    ]

    ocr_confidence: how confident you are in the transcription accuracy (legibility, ambiguous characters/words).
    mapping_confidence: how confident you are that this text truly answers the question_number you chose
    (e.g. the student wrote an explicit "Q1(a)" label vs. you had to guess from context or handwriting position).
    If there is no visible question numbering and you had to infer it, mapping_confidence must be low (<0.5).
    If the page has no answer content at all, return an empty list [].
    Return ONLY the raw JSON list, no markdown, no extra text.
    """

    data = generate_json(prompt, image_path=image_path, think=True)
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict) and "question_number" in d]
    if isinstance(data, dict) and "question_number" in data:
        return [data]
    return []


def extract_student_info(image_path: str) -> dict:
    """Extract the student's name and roll/register number from the first page
    of a scanned answer sheet, using the Gemini vision model.

    Returns {"name": str | None, "roll_number": str | None}.
    """
    prompt = """
    You are reading the cover/first page of a university handwritten answer sheet.
    Find the student's name and roll number / register number / enrollment number if present.

    Return ONLY a raw JSON object with this EXACT format, no markdown, no extra text:
    {
        "name": "The student's full name, or null if not found",
        "roll_number": "The student's roll/register number, or null if not found"
    }
    """
    data = generate_json(prompt, image_path=image_path, think=False)
    if not isinstance(data, dict):
        return {"name": None, "roll_number": None}
    return {
        "name": data.get("name") or None,
        "roll_number": data.get("roll_number") or None,
    }
