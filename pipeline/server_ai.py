"""AI/provider helpers for JobFind's local search server."""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

import requests


def ollama_base_url(default_url: str) -> str:
    """Return the configured Ollama base URL without a trailing slash."""
    return os.getenv("OLLAMA_BASE_URL", default_url).rstrip("/")


def ollama_model(default_model: str) -> str:
    """Return the configured Ollama model name."""
    return os.getenv("OLLAMA_MODEL", default_model)


def ollama_available(base_url: str, requests_mod: Any = requests, timeout: float = 0.7) -> bool:
    """Whether the local Ollama API looks reachable."""
    try:
        response = requests_mod.get(f"{base_url}/api/tags", timeout=timeout)
    except requests.RequestException:
        return False
    return response.status_code < 400


def ai_provider_status(
    openai_api_key: Optional[str],
    openai_model: str,
    ollama_is_available: bool,
    ollama_model_name: str,
    setup_message: str,
) -> dict[str, Any]:
    """Return the currently available AI polish provider."""
    if openai_api_key:
        return {
            "enabled": True,
            "provider": "openai",
            "model": openai_model,
            "message": f"AI polish available through OpenAI ({openai_model}).",
        }
    if ollama_is_available:
        return {
            "enabled": True,
            "provider": "ollama",
            "model": ollama_model_name,
            "message": f"AI polish available locally through Ollama ({ollama_model_name}).",
        }
    return {
        "enabled": False,
        "provider": "none",
        "model": ollama_model_name,
        "message": setup_message,
    }


def build_ai_polish_prompt(
    payload: dict[str, Any],
    profile: dict[str, Any],
    field_map: dict[str, dict[str, str]],
    clean_text: Callable[[Any, int], str],
) -> tuple[str, Optional[str]]:
    """Build a safe prompt for review-first answer improvement."""
    answer_key = clean_text(payload.get("key"), 80)
    field = field_map.get(answer_key)
    if not field:
        return "", "Unknown common answer field."
    draft = clean_text(payload.get("draft"), 3000)
    if not draft:
        return "", "Write an answer first, then ask AI to improve it."
    job = payload.get("job", {})
    if not isinstance(job, dict):
        job = {}
    title = clean_text(job.get("title"), 200) or "Not specified"
    company = clean_text(job.get("company"), 200) or "Not specified"
    description = clean_text(job.get("description") or job.get("reason"), 1400) or "Not specified"
    student_context = clean_text(profile.get("short_intro"), 700) or "High school student seeking entry-level work."
    prompt = f"""Improve this student job-application answer.

Question type: {field['label']}
Likely application question: {field['prompt']}
Current answer: {draft}

Selected job:
Title: {title}
Employer: {company}
Description/context: {description}

Student context: {student_context}

Rules:
- Return only one improved answer, 1-3 sentences.
- Keep it honest, natural, specific, and appropriate for a high school student.
- Do not invent experience, credentials, availability, transportation, legal eligibility, age, or work authorization.
- Do not answer sensitive/legal/demographic questions.
- Keep the student's meaning, but make wording clearer and more matched to the job."""
    return prompt, None


def build_question_suggestion_prompt(
    payload: dict[str, Any],
    profile: dict[str, Any],
    common_answers: dict[str, str],
    field_map: dict[str, dict[str, str]],
    clean_text: Callable[[Any, int], str],
    is_sensitive_prompt: Callable[[str], bool],
    is_legal_prompt: Callable[[str], bool],
) -> tuple[str, Optional[str]]:
    """Build a safe prompt for a review-field answer suggestion."""
    review_item = payload.get("review_item")
    if not isinstance(review_item, dict):
        review_item = {}
    question = clean_text(review_item.get("question") or payload.get("question"), 1000)
    if not question:
        return "", "Question text is required."
    review_kind = clean_text(review_item.get("kind"), 80).lower()
    suggestable = review_item.get("suggestable")
    if suggestable is False or review_kind in {"resume", "verification", "login", "submit", "blocked"}:
        return "", "This question needs your review and should not be answered by AI."
    if is_sensitive_prompt(question) or is_legal_prompt(question):
        return "", "This question needs your review and should not be answered by AI."
    job = payload.get("job", {})
    if not isinstance(job, dict):
        job = {}
    title = clean_text(job.get("title"), 200) or "Not specified"
    company = clean_text(job.get("company"), 200) or "Not specified"
    description = clean_text(job.get("description") or job.get("reason"), 1400) or "Not specified"
    student_context = clean_text(profile.get("short_intro"), 700) or "High school student seeking entry-level work."
    common_lines = [
        f"- {meta['label']}: {common_answers.get(key)}"
        for key, meta in field_map.items()
        if common_answers.get(key)
    ]
    common_context = "\n".join(common_lines) or "- No saved common answers."
    prompt = f"""Draft a review-first answer for a student job application question.

Application question: {question}

Selected job:
Title: {title}
Employer: {company}
Description/context: {description}

Student profile/context: {student_context}
Saved common answers:
{common_context}

Rules:
- Return only one suggested answer, 1-3 sentences.
- Keep it honest, natural, and appropriate for a high school student.
- Do not invent work experience, credentials, availability, transportation, legal eligibility, age, or work authorization.
- If the question asks about service/customer/food/retail experience and the profile does not show formal experience, say that honestly and emphasize reliability, willingness to learn, school/team experience, and customer-friendly skills.
- Do not answer sensitive/legal/demographic questions.
- This is a suggestion for user review, not an automatic form submission."""
    return prompt, None


def extract_openai_text(payload: dict[str, Any]) -> str:
    """Extract text from a Responses API payload."""
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def extract_ollama_text(payload: dict[str, Any]) -> str:
    """Extract text from an Ollama generate response."""
    text = payload.get("response")
    return text.strip() if isinstance(text, str) else ""


def call_openai_polish(
    prompt: str,
    api_key: Optional[str],
    model: str,
    responses_url: str,
    requests_mod: Any = requests,
) -> tuple[dict[str, str], int]:
    """Call OpenAI for one review-first suggestion."""
    if not api_key:
        return {"error": "OPENAI_API_KEY is not set."}, 503
    try:
        response = requests_mod.post(
            responses_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "instructions": "You improve short job application answers for a student. Return only the revised answer.",
                "input": prompt,
                "max_output_tokens": 220,
                "temperature": 0.2,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        return {"error": f"Could not reach OpenAI: {exc}"}, 502
    if response.status_code >= 400:
        return {"error": f"OpenAI request failed with status {response.status_code}."}, 502
    try:
        response_payload = response.json()
    except ValueError:
        return {"error": "OpenAI returned a non-JSON response."}, 502
    suggestion = extract_openai_text(response_payload)
    if not suggestion:
        return {"error": "OpenAI did not return a suggestion."}, 502
    return {"suggestion": suggestion, "model": model, "provider": "openai"}, 200


def call_ollama_polish(
    prompt: str,
    base_url: str,
    model: str,
    setup_message: str,
    requests_mod: Any = requests,
) -> tuple[dict[str, str], int]:
    """Call local Ollama for one review-first suggestion."""
    try:
        response = requests_mod.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "system": "You improve short job application answers for a student. Return only the revised answer.",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=45,
        )
    except requests.RequestException as exc:
        return {"error": f"Could not reach Ollama: {exc}. {setup_message}"}, 502
    if response.status_code >= 400:
        return {
            "error": (
                f"Ollama request failed with status {response.status_code}. "
                f"Make sure `{model}` is installed with `ollama pull {model}`."
            )
        }, 502
    try:
        response_payload = response.json()
    except ValueError:
        return {"error": "Ollama returned a non-JSON response."}, 502
    suggestion = extract_ollama_text(response_payload)
    if not suggestion:
        return {"error": "Ollama did not return a suggestion."}, 502
    return {"suggestion": suggestion, "model": model, "provider": "ollama"}, 200
