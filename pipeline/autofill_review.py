"""Prompt matching and review-item helpers for application autofill."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional


from utils import NOT_SPECIFIED

LEGAL_PATTERNS = (
    "authorized to work",
    "legally authorized",
    "work authorization",
    "eligible to work",
    "require sponsorship",
    "visa sponsorship",
    "at least 16",
    "at least 18",
    "work permit",
)

GENERIC_REVIEW_QUESTIONS = {
    "yes no question on this step",
    "question on this step",
    "review this field on the page",
    "unlabeled text field",
}

TEXT_FIELD_PATTERNS = (
    ("first_name", ("first name", "given name")),
    ("last_name", ("last name", "surname", "family name")),
    ("preferred_name", ("preferred name", "nickname")),
    ("name", ("full name", "your name", "name")),
    ("email", ("email", "e-mail")),
    ("phone", ("phone", "mobile", "telephone", "cell")),
    ("city", ("city",)),
    ("state", ("state", "province")),
    ("school", ("school", "high school")),
    ("graduation_year", ("graduation year", "grad year", "year of graduation")),
    ("available_start_date", ("start date", "available start", "when can you start")),
    ("desired_hours", ("desired hours", "hours per week", "how many hours")),
    ("availability", ("availability", "available", "schedule")),
    ("work_eligibility", LEGAL_PATTERNS),
    ("education_level", ("education", "highest level", "grade level")),
    ("short_intro", ("cover letter", "introduction", "tell us about yourself", "why are you interested")),
)

COMMON_ANSWER_PROMPTS = {
    "ideal_job": (
        "ideal job",
        "looking for in a job",
        "what are you looking for",
        "best job for you",
    ),
    "availability": (
        "availability",
        "available",
        "schedule",
        "when can you work",
        "what days can you work",
        "weekend",
        "hours per week",
    ),
    "transportation": (
        "transportation",
        "reliable transportation",
        "get to work",
        "commute",
    ),
    "why_this_company": (
        "why do you want to work here",
        "why are you interested",
        "why this company",
        "why this job",
        "interested in this job",
        "interested in this company",
    ),
    "customer_service": (
        "customer service",
        "service experience",
        "guest experience",
        "food service",
        "retail service",
        "serving customers",
        "help customers",
        "working with customers",
        "difficult customer",
    ),
    "teamwork": (
        "teamwork",
        "worked on a team",
        "team member",
        "with a team",
        "coworkers",
    ),
    "extra_notes": (
        "anything else",
        "additional information",
        "anything we should know",
        "tell us more",
    ),
}

COMMON_ANSWER_STORAGE_PROMPTS = {
    "ideal_job": ("What does your ideal job look like?",),
    "availability": (
        "When are you available to work?",
        "Are you available weekends?",
        "How many hours per week can you work?",
    ),
    "transportation": ("Do you have reliable transportation?",),
    "why_this_company": (
        "Why do you want to work here?",
        "Why are you interested in this job?",
        "Why are you interested in this company?",
    ),
    "customer_service": ("Tell us about your customer service experience.",),
    "teamwork": ("Tell us about a time you worked on a team.",),
    "extra_notes": ("Anything else an employer should know?",),
}

SELECT_PATTERNS = (
    ("state", ("state", "province")),
    ("education_level", ("education", "highest level", "grade level")),
    ("graduation_year", ("graduation year", "grad year")),
)

SEARCH_FIELD_PATTERNS = (
    "job title keywords company",
    "title keywords company",
    "city state zip",
    "city state zip code",
    "where city state",
    "what job title",
    "text input what",
    "text input where",
    "search jobs",
    "search job",
    "keyword search",
)

SENSITIVE_PATTERNS = (
    "social security",
    "ssn",
    "password",
    "passcode",
    "captcha",
    "verification code",
    "one-time code",
    "otp",
    "criminal",
    "felony",
    "background check",
    "disability",
    "veteran",
    "race",
    "ethnicity",
    "gender",
    "hispanic",
    "date of birth",
    "birth date",
    "birthdate",
)

RESUME_UPLOAD_OPTION_PATTERNS = ("upload a resume", "upload resume", "upload your resume")

_HASH_TOKEN_RE = re.compile(r"\bq_[0-9a-f]{12,}\b", re.IGNORECASE)
_REACT_ID_RE = re.compile(r":r[0-9a-z]+:", re.IGNORECASE)
_FRAMEWORK_TOKEN_RE = re.compile(
    r"\b[a-z]+(?:-[a-z]+)*-(?:question|select|input|textarea)(?:-input)?(?:-\d+)?\b",
    re.IGNORECASE,
)
def clean_text(value: Any) -> str:
    """Normalize browser/profile text for matching."""
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def normalize(value: Any) -> str:
    """Return a lowercase simplified string."""
    return re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower()).strip()


def load_profile(path: Path) -> dict[str, Any]:
    """Load a JSON applicant profile."""
    import json

    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        profile = json.load(file)
    return profile if isinstance(profile, dict) else {}


def split_name(full_name: Any) -> tuple[str, str]:
    """Split a full name into best-effort first/last parts."""
    parts = clean_text(full_name).split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def prepared_profile(profile: dict[str, Any]) -> dict[str, str]:
    """Return profile values with first/last-name fallbacks populated."""
    output: dict[str, str] = {}
    for key, value in profile.items():
        if isinstance(value, (str, int, float)):
            text = clean_text(value)
            if text:
                output[key] = text

    first, last = split_name(output.get("name", ""))
    output.setdefault("first_name", first)
    output.setdefault("last_name", last)
    return output


def custom_answers(profile: dict[str, Any]) -> dict[str, str]:
    """Return lowercased custom question prompts -> answers."""
    raw = profile.get("custom_answers")
    if not isinstance(raw, dict):
        return {}
    result = {}
    for prompt, answer in raw.items():
        prompt_text = normalize(prompt)
        answer_text = clean_text(answer)
        if prompt_text and answer_text != NOT_SPECIFIED:
            result[prompt_text] = answer_text
    return result


def common_custom_answer(profile: dict[str, Any], answer_key: str) -> Optional[str]:
    """Return a stored reusable answer for a common-answer bucket."""
    answers = custom_answers(profile)
    prompts = COMMON_ANSWER_STORAGE_PROMPTS.get(answer_key, ())
    for prompt in prompts:
        answer = answers.get(normalize(prompt))
        if answer:
            return answer
    return None


def saved_answers(profile: dict[str, Any]) -> dict[str, str]:
    """Return normalized question prompt -> remembered answer."""
    raw = profile.get("_saved_answers")
    if not isinstance(raw, dict):
        raw = profile.get("saved_answers")
    if not isinstance(raw, dict):
        return {}
    result = {}
    for prompt, answer in raw.items():
        prompt_text = normalize(prompt)
        answer_text = clean_text(answer)
        if prompt_text and answer_text != NOT_SPECIFIED:
            result[prompt_text] = answer_text
    return result


def is_sensitive_prompt(prompt: str) -> bool:
    """Whether a prompt should never be auto-filled."""
    text = normalize(prompt)
    return any(pattern in text for pattern in SENSITIVE_PATTERNS)


def is_legal_prompt(prompt: str) -> bool:
    """Whether a prompt is about work authorization / age / permits."""
    text = normalize(prompt)
    return any(pattern in text for pattern in LEGAL_PATTERNS)


def is_generic_review_prompt(prompt: str) -> bool:
    """Whether a prompt is a fallback label, not a real question."""
    return normalize(prompt) in GENERIC_REVIEW_QUESTIONS


def is_safe_saved_answer_prompt(prompt: str) -> bool:
    """Whether a prompt can be safely remembered for exact-match reuse."""
    text = normalize(prompt)
    return bool(text) and not is_generic_review_prompt(text) and not is_sensitive_prompt(text) and not is_legal_prompt(text)


def is_search_prompt(prompt: str) -> bool:
    """Whether a field is a site search box, not an application field."""
    text = normalize(prompt)
    if text in {"q", "l", "what", "where", "query", "search"}:
        return True
    return any(pattern in text for pattern in SEARCH_FIELD_PATTERNS)


def match_profile_key(prompt: str, patterns: tuple[tuple[str, tuple[str, ...]], ...]) -> Optional[str]:
    """Find the profile key that best matches a label/name/placeholder."""
    text = normalize(prompt)
    if not text:
        return None
    for key, phrases in patterns:
        if any(phrase in text for phrase in phrases):
            return key
    return None


def answer_for_prompt(prompt: str, profile: dict[str, Any]) -> tuple[Optional[str], str]:
    """Return a profile/custom answer for a field prompt, or why it was skipped."""
    prompt_text = normalize(prompt)
    values = prepared_profile(profile)

    if is_sensitive_prompt(prompt):
        return None, "needs review: sensitive question"

    if is_legal_prompt(prompt):
        legal_answer = values.get("work_eligibility") or values.get("age_or_work_permit")
        if legal_answer:
            return legal_answer, "explicit profile legal/work eligibility answer"
        return None, "needs review: legal/work eligibility question"

    remembered_answer = saved_answers(profile).get(prompt_text)
    if remembered_answer:
        return remembered_answer, "saved answer"

    for custom_prompt, answer in custom_answers(profile).items():
        if custom_prompt and custom_prompt in prompt_text:
            return answer, "custom answer"

    for answer_key, patterns in COMMON_ANSWER_PROMPTS.items():
        if any(pattern in prompt_text for pattern in patterns):
            answer = common_custom_answer(profile, answer_key)
            if answer:
                return answer, f"common answer: {answer_key}"

    key = match_profile_key(prompt, TEXT_FIELD_PATTERNS)
    if key and values.get(key):
        return values[key], f"profile field: {key}"

    if "transportation" in prompt_text:
        return "I have reliable transportation.", "safe default: transportation"
    if "weekend" in prompt_text and values.get("availability"):
        return values["availability"], "profile field: availability"
    if ("why" in prompt_text or "interested" in prompt_text) and values.get("short_intro"):
        return values["short_intro"], "profile field: short_intro"

    return None, "needs review: no confident answer"


def select_answer_for_prompt(prompt: str, profile: dict[str, Any], options: list[str]) -> tuple[Optional[str], str]:
    """Choose a select/radio option when it safely matches the desired answer."""
    values = prepared_profile(profile)
    key = match_profile_key(prompt, SELECT_PATTERNS)
    desired = values.get(key or "") if key else None
    if not desired:
        desired, reason = answer_for_prompt(prompt, profile)
        if not desired:
            return None, reason

    desired_norm = normalize(desired)
    for option in options:
        option_norm = normalize(option)
        if option_norm and (option_norm == desired_norm or desired_norm in option_norm or option_norm in desired_norm):
            return option, f"matched option from {key or 'answer'}"

    lowered_options = {normalize(option): option for option in options}
    if (desired_norm in ("yes", "y", "true") or desired_norm.startswith("yes ")) and "yes" in lowered_options:
        return lowered_options["yes"], "matched yes option"
    if (desired_norm in ("no", "n", "false") or desired_norm.startswith("no ")) and "no" in lowered_options:
        return lowered_options["no"], "matched no option"

    return None, "needs review: no matching option"


def clean_prompt_label(text: Any) -> str:
    """Strip machine ids/framework noise from a form label for readable display."""
    value = clean_text(text)
    if value == NOT_SPECIFIED:
        return ""
    value = _HASH_TOKEN_RE.sub(" ", value)
    value = _REACT_ID_RE.sub(" ", value)
    value = _FRAMEWORK_TOKEN_RE.sub(" ", value)
    value = re.sub(r"[-_]{2,}", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^[\s\-–—:]+", "", value)
    value = re.sub(r"^(?:-?\d+\s+){1,3}", "", value)
    value = re.sub(r"^[\s\-–—:]+", "", value)
    value = re.sub(r"\s*\*\s*$", "", value).strip()
    return value


def is_machine_label(text: Any) -> bool:
    """Whether a label is only a framework/hash control id."""
    value = clean_text(text)
    if not value:
        return False
    if clean_prompt_label(value):
        return False
    return bool(_HASH_TOKEN_RE.search(value) or _REACT_ID_RE.search(value) or _FRAMEWORK_TOKEN_RE.search(value))


def has_yes_no_options(options: Optional[list[str]]) -> bool:
    """Whether a radio/select option set is basically yes/no."""
    normalized = {normalize(option) for option in options or [] if normalize(option)}
    return bool(normalized) and normalized.issubset({"yes", "no", "y", "n", "true", "false"})


def fallback_review_question(kind: str, fallback: Any, options: Optional[list[str]] = None) -> str:
    """Return a readable fallback question when the DOM only exposes ids."""
    cleaned = clean_prompt_label(fallback) or clean_text(fallback)
    if cleaned and not is_machine_label(cleaned):
        return cleaned
    if normalize(kind) in {"radio", "select"} and has_yes_no_options(options):
        return "Yes/No question on this step"
    return "Review this field on the page"


def visible_label_for_element(element: Any) -> str:
    """Return the best human-readable label for a form element."""
    raw = element.evaluate(
        """(el) => {
          const txt = (node) => (node && node.innerText ? String(node.innerText).trim() : '');
          const aria = el.getAttribute('aria-label');
          if (aria && aria.trim()) return aria.trim();
          if (el.id) {
            const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (label && txt(label)) return txt(label);
          }
          const wrappingLabel = el.closest('label');
          if (wrappingLabel && txt(wrappingLabel)) return txt(wrappingLabel);
          const fieldset = el.closest('fieldset');
          const legend = fieldset && fieldset.querySelector('legend');
          if (legend && txt(legend)) return txt(legend);
          const labelledby = el.getAttribute('aria-labelledby');
          if (labelledby) {
            const parts = labelledby.split(/\\s+/).map(id => {
              const n = document.getElementById(id); return n ? txt(n) : '';
            }).filter(Boolean);
            if (parts.length) return parts.join(' ');
          }
          const sibling = el.previousElementSibling;
          if (sibling && sibling.tagName && sibling.tagName.toLowerCase() === 'label' && txt(sibling)) {
            return txt(sibling);
          }
          const siblingLabel = sibling && sibling.querySelector ? sibling.querySelector('label') : null;
          if (siblingLabel && txt(siblingLabel)) return txt(siblingLabel);
          const placeholder = el.getAttribute('placeholder');
          if (placeholder && placeholder.trim()) return placeholder.trim();
          return el.getAttribute('name') || el.getAttribute('id') || '';
        }"""
    )
    return clean_prompt_label(raw)


def review_reason_text(reason: str) -> str:
    """Convert an internal skip reason into short user-facing copy."""
    value = clean_text(reason)
    if value == NOT_SPECIFIED or not value:
        return "Needs review"
    value = re.sub(r"^needs review:\s*", "", value, flags=re.IGNORECASE).strip()
    aliases = {
        "sensitive question": "Sensitive question",
        "legal/work eligibility question": "Legal/work eligibility question",
        "no confident answer": "No confident answer",
        "no matching option": "No matching option",
        "sensitive/legal checkbox": "Sensitive/legal checkbox",
    }
    lowered = value.lower()
    if lowered in aliases:
        return aliases[lowered]
    return value[:1].upper() + value[1:] if value else "Needs review"


def review_item_legacy_text(item: dict[str, Any]) -> str:
    """Return the legacy string form kept for compatibility and logs."""
    question = clean_text(item.get("question"))
    reason_detail = clean_text(item.get("reason_detail") or item.get("reason"))
    if question and question != NOT_SPECIFIED:
        return f"{question} ({reason_detail})" if reason_detail and reason_detail != NOT_SPECIFIED else question
    return reason_detail if reason_detail and reason_detail != NOT_SPECIFIED else "Needs review"


def is_suggestable_review_item(item: dict[str, Any]) -> bool:
    """Whether it is safe to ask AI for a wording suggestion for this item."""
    kind = normalize(item.get("kind"))
    question = normalize(item.get("question"))
    reason = normalize(item.get("reason_detail") or item.get("reason"))
    if kind in {"resume", "verification", "login", "submit", "blocked"}:
        return False
    if is_generic_review_prompt(question):
        return False
    if is_sensitive_prompt(question) or is_legal_prompt(question):
        return False
    blocked_terms = ("captcha", "verification", "password", "resume", "submit", "legal", "sensitive")
    return not any(term in reason for term in blocked_terms)


def build_review_item(
    *,
    question: Any,
    fallback: str,
    reason: str,
    kind: str,
    raw_label: Any = "",
    options: Optional[list[str]] = None,
    suggestable: Optional[bool] = None,
    target: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build one structured review item for the autofill report."""
    cleaned_options = [clean_text(option) for option in options or [] if clean_text(option) != NOT_SPECIFIED]
    cleaned_question = (
        clean_prompt_label(question)
        or clean_prompt_label(raw_label)
        or fallback_review_question(kind, fallback, cleaned_options)
    )
    item = {
        "question": cleaned_question,
        "reason": review_reason_text(reason),
        "reason_detail": clean_text(reason),
        "kind": kind,
        "raw_label": clean_text(raw_label) or clean_text(question) or cleaned_question,
        "suggestable": False,
    }
    if cleaned_options:
        item["options"] = cleaned_options
    if isinstance(target, dict) and target:
        item["target"] = target
    item["suggestable"] = is_suggestable_review_item(item) if suggestable is None else bool(suggestable)
    item["legacy_text"] = review_item_legacy_text(item)
    return item


def add_review_item(
    report: dict[str, Any],
    *,
    question: Any,
    fallback: str,
    reason: str,
    kind: str,
    raw_label: Any = "",
    options: Optional[list[str]] = None,
    suggestable: Optional[bool] = None,
    target: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Append one structured review item to the report."""
    item = build_review_item(
        question=question,
        fallback=fallback,
        reason=reason,
        kind=kind,
        raw_label=raw_label,
        options=options,
        suggestable=suggestable,
        target=target,
    )
    report.setdefault("review_items", []).append(item)
    return item


def group_question_for_element(element: Any) -> str:
    """Return the group-level question for a checkbox/radio control."""
    raw = element.evaluate(
        """(el) => {
          const txt = (n) => (n && n.innerText ? String(n.innerText).trim() : '');
          const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
          const labelText = (node) => clean(txt(node));
          const optionTexts = new Set(
            Array.from(document.querySelectorAll(`input[name="${CSS.escape(el.name || '')}"]`))
              .map(input => labelText(input.closest('label')) || clean(input.value))
              .filter(Boolean)
          );
          const useful = (value) => {
            const text = clean(value);
            if (!text) return '';
            if (/^q_[0-9a-f]{12,}$/i.test(text)) return '';
            if (/^:?r[0-9a-z]+:?$/i.test(text)) return '';
            if (optionTexts.has(text)) return '';
            return text;
          };
          const fromContainer = (c) => {
            if (!c) return '';
            const aria = c.getAttribute && c.getAttribute('aria-label');
            if (useful(aria)) return useful(aria);
            const lb = c.getAttribute && c.getAttribute('aria-labelledby');
            if (lb) {
              const parts = lb.split(/\\s+/).map(id => {
                const x = document.getElementById(id); return x ? txt(x) : '';
              }).filter(Boolean);
              if (useful(parts.join(' '))) return useful(parts.join(' '));
            }
            const legend = c.querySelector && c.querySelector('legend');
            if (legend && useful(txt(legend))) return useful(txt(legend));
            return '';
          };
          const group = el.closest('fieldset, [role="group"], [role="radiogroup"]');
          const grouped = fromContainer(group);
          if (grouped) return grouped;
          if (String(el.type || '').toLowerCase() === 'checkbox') return '';
          const labelledby = el.getAttribute('aria-labelledby');
          if (labelledby) {
            const parts = labelledby.split(/\\s+/).map(id => {
              const x = document.getElementById(id); return x ? txt(x) : '';
            }).filter(Boolean);
            if (useful(parts.join(' '))) return useful(parts.join(' '));
          }
          let node = el.closest('label') || el;
          for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
            const heading = node.querySelector && node.querySelector('legend, h1, h2, h3, h4, [data-testid*="question"], [id*="question"], [class*="question"]');
            if (heading && useful(txt(heading))) return useful(txt(heading));
            let prev = node.previousElementSibling;
            for (let hops = 0; prev && hops < 4; hops += 1, prev = prev.previousElementSibling) {
              if (useful(txt(prev))) return useful(txt(prev));
            }
          }
          return '';
        }"""
    )
    return clean_prompt_label(raw)


def dedupe_review_items(items: list) -> list:
    """Collapse review items that map to the same question."""
    seen: set = set()
    out: list = []
    for item in items:
        if isinstance(item, dict):
            base = clean_text(item.get("question")) or clean_text(item.get("raw_label")) or review_item_legacy_text(item)
        else:
            base = re.sub(r"\s*\([^)]*\)\s*$", "", str(item)).strip()
        key = (clean_prompt_label(base) or str(item)).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def sync_legacy_review_strings(report: dict[str, Any]) -> None:
    """Keep legacy string fields in sync with structured review items."""
    report["needs_review"] = [review_item_legacy_text(item) for item in report.get("review_items", [])]
    report["current_step_needs_review"] = [
        review_item_legacy_text(item) for item in report.get("current_step_review_items", [])
    ]


def resolve_resume_path(profile: dict[str, Any]) -> Optional[Path]:
    """Return an existing resume file path from the profile, or None."""
    raw = clean_text(profile.get("resume_path"))
    if raw == NOT_SPECIFIED or not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None


def resume_path_issue(profile: dict[str, Any]) -> Optional[str]:
    """Return a user-facing resume_path problem, if one exists."""
    raw = clean_text(profile.get("resume_path"))
    if not raw or raw == NOT_SPECIFIED:
        return "Resume path is not set in applicant_profile.json."
    if not Path(raw).expanduser().is_file():
        return f"Resume file missing: {raw}"
    return None


def is_resume_step(surface: Any) -> bool:
    """Whether the current step appears to be asking for a resume."""
    text = normalize(surface.locator("body").inner_text(timeout=2000)[:4000] if hasattr(surface, "locator") else "")
    return "resume" in text and ("upload" in text or "add" in text or "file" in text)


def reveal_resume_upload_input(surface: Any) -> None:
    """Click an upload-resume option (never the builder) to expose a file input."""
    try:
        controls = surface.locator("a, button, label, [role='button']")
        for index in range(controls.count()):
            control = controls.nth(index)
            try:
                if not control.is_visible() or not control.is_enabled():
                    continue
                label = normalize(control.inner_text(timeout=1000) or control.get_attribute("aria-label") or "")
                if any(pattern in label for pattern in RESUME_UPLOAD_OPTION_PATTERNS):
                    control.click(timeout=3000)
                    surface.wait_for_timeout(800)
                    return
            except Exception:
                continue
    except Exception:
        return
