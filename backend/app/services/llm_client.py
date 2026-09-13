import json
import time
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from app.core.config import settings

_client = genai.Client(api_key=settings.GEMINI_API_KEY) if settings.GEMINI_API_KEY else None


def _extract_json(text: str):
    """Gemini occasionally wraps JSON in prose or markdown code fences.
    Find the first bracket and try shrinking substrings until one parses."""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    for i, ch in enumerate(text):
        if ch in "{[":
            for j in range(len(text), i, -1):
                candidate = text[i:j]
                try:
                    return json.loads(candidate)
                except Exception:
                    continue
            break
    return None


def _call_gemini(prompt: str, image_path: str | None, think: bool, timeout: int) -> str:
    if _client is None:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to backend/.env or your environment."
        )

    model = settings.GEMINI_VISION_MODEL if image_path else settings.GEMINI_TEXT_MODEL

    contents: list = [prompt]
    if image_path:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        mime_type = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    config = types.GenerateContentConfig(
        temperature=0.2,
        thinking_config=types.ThinkingConfig(thinking_budget=-1 if think else 0),
        http_options=types.HttpOptions(timeout=timeout * 1000),
    )

    response = _client.models.generate_content(model=model, contents=contents, config=config)
    return response.text or ""


def _retry_delay_seconds(error) -> float | None:
    """Pull the server-suggested retry delay (e.g. '31s') out of a 429/503 error's details."""
    details = getattr(error, "details", None)
    if not isinstance(details, dict):
        return None
    for item in details.get("error", {}).get("details", []):
        delay = item.get("retryDelay")
        if delay and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                continue
    return None


def generate_json(prompt: str, image_path: str | None = None, think: bool = False, timeout: int = 180, max_attempts: int = 4):
    """Call the Gemini API and parse its response as JSON.

    Rate-limit (429) and transient server (5xx) errors are retried with backoff -
    honoring the server's suggested retry-after delay when it provides one, since
    the free tier's per-minute quota is easy to exceed under concurrent uploads.
    Returns the parsed dict/list, or None if every attempt failed.
    """
    last_raw = ""
    for attempt in range(max_attempts):
        try:
            response_text = _call_gemini(prompt, image_path, think, timeout)
        except (genai_errors.ClientError, genai_errors.ServerError) as e:
            is_retryable = getattr(e, "code", None) in (429, 500, 503)
            print(f"Gemini call failed (attempt {attempt + 1}/{max_attempts}): {e}")
            if not is_retryable or attempt == max_attempts - 1:
                break
            wait = _retry_delay_seconds(e) or min(2 ** attempt, 30)
            time.sleep(wait)
            continue
        except Exception as e:
            print(f"Gemini call failed (attempt {attempt + 1}/{max_attempts}): {e}")
            if attempt == max_attempts - 1:
                break
            time.sleep(min(2 ** attempt, 10))
            continue

        last_raw = response_text
        parsed = _extract_json(response_text)
        if parsed is not None:
            return parsed
        # Got a response but it wasn't valid JSON - retry without waiting, it's not a quota issue.

    print(f"Failed to get a usable Gemini response after {max_attempts} attempts. Last raw response: {last_raw[:1000]}")
    return None
