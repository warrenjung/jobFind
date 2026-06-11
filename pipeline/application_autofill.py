"""
Best-effort application autofill helpers.

The live autofill path opens a visible Playwright browser and fills obvious
fields from applicant_profile.json. It intentionally never submits forms.
"""

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional


NOT_SPECIFIED = "Not specified"

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

APPLY_CONTROL_PATTERNS = (
    "apply now",
    "apply on company site",
    "apply with indeed",
    "apply externally",
    "easily apply",
    "start application",
    "continue to application",
)

# A bare "apply" is accepted too (Indeed's button is sometimes just "Apply"),
# but these are never apply entry points and must be excluded.
APPLY_DENYLIST = (
    "apply filter",
    "apply filters",
    "apply update",
    "apply remote",
    "apply changes",
    "applied",
    "apply saved search",
)

# Selectors for Indeed's apply control regardless of its surrounding markup.
# Restricted to clickable tags so container elements (e.g. the
# `#indeedApplyButtonContainer` iframe) are not mistaken for the button.
INDEED_APPLY_SELECTORS = (
    "#indeedApplyButton",
    "button[id*='indeedApply']",
    "a[id*='indeedApply']",
    "button[data-testid*='apply']",
    "a[data-testid*='apply']",
    "button:has-text('Apply now')",
    "button:has-text('Apply')",
    "a:has-text('Apply on company')",
    "a:has-text('Apply externally')",
)

# URLs that are Indeed's hosted application (multi-step SPA) surfaces.
APPLY_SURFACE_HOSTS = ("smartapply.indeed.com", "apply.indeed.com")

# Wizard navigation button classification. Only ADVANCE buttons are ever clicked;
# FINAL_SUBMIT and NEVER_CLICK are hard-stops so an application is never submitted
# or abandoned automatically.
FINAL_SUBMIT_PATTERNS = (
    "submit application",
    "submit your application",
    "submit",
    "send application",
)
ADVANCE_PATTERNS = ("continue", "next", "save and continue")
NEVER_CLICK_PATTERNS = ("save and close", "close", "cancel", "back")

# How many wizard steps to auto-advance through before giving up.
MAX_APPLICATION_STEPS = 8

# Options on the resume step. We choose "Upload a resume" and never the builder.
RESUME_UPLOAD_OPTION_PATTERNS = ("upload a resume", "upload resume", "upload your resume")

BLOCKED_PAGE_PATTERNS = (
    "blocked",
    "access denied",
    "cloudflare",
    "additional verification required",
    "ray id",
    "are you human",
    "checking your browser",
    "verify you are human",
    "unusual traffic",
)

VISIBLE_VERIFICATION_PATTERNS = (
    "additional verification required",
    "verify you are human",
    "are you human",
    "i m not a robot",
    "select all images",
    "checking your browser",
    "access denied",
    "unusual traffic",
    "captcha challenge",
    "cloudflare",
    "ray id",
)

LOGIN_PAGE_PATTERNS = (
    "sign in",
    "log in",
    "login",
    "create account",
    "email verification",
    "password",
)

INDEED_LOGIN_URL = "https://secure.indeed.com/auth"


def clean_text(value: Any) -> str:
    """Normalize browser/profile text for matching."""
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def normalize(value: Any) -> str:
    """Return a lowercase simplified string."""
    return re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower()).strip()


def load_profile(path: Path) -> dict[str, Any]:
    """Load an applicant profile JSON file."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def split_name(full_name: Any) -> tuple[str, str]:
    """Split a full name into first and last parts for fallback matching."""
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
    """Return normalized custom-answer prompts mapped to answer text."""
    answers = profile.get("custom_answers", {})
    if not isinstance(answers, dict):
        return {}
    result = {}
    for prompt, answer in answers.items():
        key = normalize(prompt)
        text = clean_text(answer)
        if key and text:
            result[key] = text
    return result


def common_custom_answer(profile: dict[str, Any], answer_key: str) -> Optional[str]:
    """Return a saved Common Answer value for one answer bucket."""
    answers = custom_answers(profile)
    for prompt in COMMON_ANSWER_STORAGE_PROMPTS.get(answer_key, ()):
        answer = answers.get(normalize(prompt))
        if answer:
            return answer
    return None


def is_sensitive_prompt(prompt: str) -> bool:
    """Whether a prompt should not be guessed from generic defaults."""
    text = normalize(prompt)
    return any(pattern in text for pattern in SENSITIVE_PATTERNS)


def is_legal_prompt(prompt: str) -> bool:
    """Whether a prompt needs explicit profile data rather than guessing."""
    text = normalize(prompt)
    return any(pattern in text for pattern in LEGAL_PATTERNS)


def is_search_prompt(prompt: str) -> bool:
    """Whether a field is part of a job-site search form, not an application."""
    text = normalize(prompt)
    if text in {"q", "l", "what", "where", "query", "search"}:
        return True
    return any(pattern in text for pattern in SEARCH_FIELD_PATTERNS)


def is_blocked_page(title: str, body_text: str = "") -> bool:
    """Whether the page looks like a bot block/interstitial."""
    text = normalize(f"{title} {body_text}")
    return any(pattern in text for pattern in BLOCKED_PAGE_PATTERNS)


def is_login_page(title: str, body_text: str = "") -> bool:
    """Whether the page is asking for login/account verification."""
    text = normalize(f"{title} {body_text}")
    return any(pattern in text for pattern in LOGIN_PAGE_PATTERNS)


def has_password_input(surface: Any) -> bool:
    """Whether a page/frame contains a visible password field."""
    try:
        fields = surface.locator("input[type='password']")
        for index in range(fields.count()):
            field = fields.nth(index)
            if field.is_visible() and field.is_enabled():
                return True
    except Exception:
        return False
    return False


def surface_title(surface: Any) -> str:
    """Return a title-like string for a page or frame."""
    try:
        if hasattr(surface, "title"):
            return surface.title()
    except Exception:
        pass
    try:
        return surface.evaluate("() => document.title || ''")
    except Exception:
        return ""


def verification_frame_urls(surface: Any) -> list[str]:
    """Return child frame URLs that indicate CAPTCHA/verification."""
    try:
        frames = list(surface.frames)
    except Exception:
        frames = [surface]
    matches = []
    for frame in frames:
        try:
            url = frame.url or ""
            title = surface_title(frame)
        except Exception:
            continue
        text = normalize(f"{url} {title}")
        if any(pattern in text for pattern in ("recaptcha", "hcaptcha", "captcha", "challenge", "cloudflare")):
            matches.append(url or title)
    return matches


def has_visible_verification_challenge(surface: Any) -> bool:
    """Whether the surface text looks like an actual visible verification challenge."""
    text = normalize(f"{surface_title(surface)} {safe_page_text(surface, max_chars=4000)}")
    return any(pattern in text for pattern in VISIBLE_VERIFICATION_PATTERNS)


def has_usable_application_controls(surface: Any) -> bool:
    """Whether a surface has normal controls that can be tried before stopping."""
    return is_resume_step(surface) or has_application_form(surface) or has_wizard_controls(surface)


def detect_hard_stop(surface: Any, report: dict[str, Any]) -> bool:
    """Stop on verification/login/security pages before filling or advancing."""
    frame_urls = verification_frame_urls(surface)
    if frame_urls:
        if has_usable_application_controls(surface) and not has_visible_verification_challenge(surface):
            report["background_verification_detected"] = frame_urls[:5]
            add_stage(report, "background_verification_detected")
            return False
        report["status_reason"] = "Verification required. Handle it manually, then Resume Autofill."
        report["stopped_reason"] = "verification_required"
        report["verification_frames"] = frame_urls[:5]
        add_stage(report, "verification_required")
        return True
    title = surface_title(surface)
    body = safe_page_text(surface)
    if is_blocked_page(title, body):
        report["status_reason"] = "Verification or bot-check page detected. Handle it manually, then resume autofill."
        report["stopped_reason"] = "verification_required"
        add_stage(report, "verification_required")
        return True
    if is_login_page(title, body) or has_password_input(surface):
        report["status_reason"] = "Login or account verification is required. Handle it manually, then resume autofill."
        report["stopped_reason"] = "login_required"
        add_stage(report, "login_required")
        return True
    return False


def add_stage(report: dict[str, Any], stage: str) -> None:
    """Append a report stage once."""
    stages = report.setdefault("stages", [])
    if stage not in stages:
        stages.append(stage)


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

    text = prompt_text
    if "transportation" in text:
        return "I have reliable transportation.", "safe default: transportation"
    if "weekend" in text and values.get("availability"):
        return values["availability"], "profile field: availability"
    if ("why" in text or "interested" in text) and values.get("short_intro"):
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


def visible_label_for_element(element: Any) -> str:
    """Collect nearby label/placeholder/name/id text for a form element."""
    return element.evaluate(
        """(el) => {
          const parts = [];
          const add = (value) => { if (value && String(value).trim()) parts.push(String(value).trim()); };
          add(el.getAttribute('aria-label'));
          add(el.getAttribute('placeholder'));
          add(el.getAttribute('name'));
          add(el.getAttribute('id'));
          let hasDirectLabel = false;
          if (el.id) {
            const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (label) {
              add(label.innerText);
              hasDirectLabel = true;
            }
          }
          const wrappingLabel = el.closest('label');
          if (wrappingLabel) {
            add(wrappingLabel.innerText);
            hasDirectLabel = true;
          }
          const fieldset = el.closest('fieldset');
          if (fieldset) add(fieldset.querySelector('legend')?.innerText);
          if (!hasDirectLabel) {
            const sibling = el.previousElementSibling;
            if (sibling?.tagName?.toLowerCase() === 'label') add(sibling.innerText);
            else add(sibling?.querySelector?.('label')?.innerText);
          }
          return parts.join(' ');
        }"""
    )


def is_submit_like(text: str) -> bool:
    """Detect buttons that may submit or advance an application."""
    value = normalize(text)
    risky = (
        "submit",
        "send application",
        "apply now",
        "finish",
        "continue",
        "next",
        "review application",
    )
    return any(token in value for token in risky)


def classify_action_button(text: str) -> str:
    """Classify a wizard button: 'submit', 'advance', 'never', or 'other'.

    Submit/never buttons are never clicked; only 'advance' moves to the next step.
    """
    value = normalize(text)
    if not value:
        return "other"
    if any(pattern in value for pattern in NEVER_CLICK_PATTERNS):
        return "never"
    if any(pattern in value for pattern in FINAL_SUBMIT_PATTERNS):
        return "submit"
    if any(pattern in value for pattern in ADVANCE_PATTERNS):
        return "advance"
    return "other"


def is_apply_control_text(text: str) -> bool:
    """Whether a control looks like a job-level apply entry point."""
    value = normalize(text)
    if not value or any(bad in value for bad in APPLY_DENYLIST):
        return False
    if any(pattern in value for pattern in APPLY_CONTROL_PATTERNS):
        return True
    # A short, standalone "Apply" button (Indeed sometimes labels it just "Apply").
    tokens = value.split()
    return "apply" in tokens and len(tokens) <= 4


def safe_page_text(page: Any, max_chars: int = 2000) -> str:
    """Read a bounded amount of page text for diagnostics."""
    try:
        return page.locator("body").inner_text(timeout=2000)[:max_chars]
    except Exception:
        return ""


def count_application_like_fields(page: Any) -> int:
    """Count visible non-search fields that look like application inputs."""
    count = 0
    fields = page.locator(
        "input:not([type]), input[type='text'], input[type='email'], "
        "input[type='tel'], textarea, select, input[type='radio'], input[type='checkbox'], input[type='file']"
    )
    for index in range(fields.count()):
        field = fields.nth(index)
        try:
            if not field.is_visible() or not field.is_enabled():
                continue
            prompt = visible_label_for_element(field)
            if is_search_prompt(prompt):
                continue
            count += 1
        except Exception:
            continue
    return count


def has_application_form(page: Any) -> bool:
    """Whether the current surface has enough non-search fields to try autofill."""
    return count_application_like_fields(page) > 0


def has_application_form_any(page: Any) -> bool:
    """True if the page or any of its child frames holds application fields.

    Indeed Apply renders its form inside an iframe, so a main-frame-only check
    misses it.
    """
    try:
        frames = page.frames
    except Exception:
        return has_application_form(page)
    for frame in frames:
        try:
            if has_application_form(frame):
                return True
        except Exception:
            continue
    return False


def page_surfaces(page: Any) -> list[Any]:
    """Return the page plus child frames when available."""
    try:
        return list(page.frames)
    except Exception:
        return [page]


def has_wizard_controls(surface: Any) -> bool:
    """Whether a page/frame looks like an application wizard step."""
    return any(kind in {"advance", "submit"} for _, _, kind in iter_surface_button_controls(surface))


def has_wizard_surface_any(page: Any) -> bool:
    """Whether any page/frame has resume UI or wizard navigation."""
    for surface in page_surfaces(page):
        if is_resume_step(surface) or has_wizard_controls(surface):
            return True
    return False


def find_application_surface(page: Any) -> Optional[Any]:
    """Return the most specific page/frame that looks like the current application step."""
    for surface in page_surfaces(page):
        try:
            if has_application_form(surface):
                return surface
        except Exception:
            continue
    for surface in page_surfaces(page):
        try:
            if is_resume_step(surface) or has_wizard_controls(surface):
                return surface
        except Exception:
            continue
    return None


def control_label(element: Any) -> str:
    """Collect visible text/value/ARIA text for a clickable control."""
    try:
        text = element.inner_text(timeout=1000)
    except Exception:
        text = ""
    parts = [
        text,
        element.get_attribute("value") or "",
        element.get_attribute("aria-label") or "",
        element.get_attribute("title") or "",
    ]
    return clean_text(" ".join(part for part in parts if part))


def collect_apply_controls(surface: Any) -> list[tuple[Any, str]]:
    """Return [(element, label)] of apply-like controls on one frame/page."""
    found: list[tuple[Any, str]] = []
    seen_labels: set[str] = set()

    def remember(element: Any, label: str) -> None:
        label = clean_text(label) or "Apply"
        key = label.lower()
        if key in seen_labels:
            return
        seen_labels.add(key)
        found.append((element, label))

    # Indeed-specific selectors first (most reliable for the Indeed Apply button).
    for selector in INDEED_APPLY_SELECTORS:
        try:
            locator = surface.locator(selector)
            for index in range(min(locator.count(), 5)):
                element = locator.nth(index)
                if not element.is_visible() or not element.is_enabled():
                    continue
                label = control_label(element)
                if not label:
                    continue  # skip container/icon-only matches with no label
                remember(element, label)
        except Exception:
            continue

    # Generic text scan across clickable controls.
    try:
        controls = surface.locator("a, button, input[type='button'], [role='button']")
        for index in range(controls.count()):
            element = controls.nth(index)
            try:
                if not element.is_visible() or not element.is_enabled():
                    continue
                label = control_label(element)
                if is_apply_control_text(label):
                    remember(element, label)
            except Exception:
                continue
    except Exception:
        pass
    return found


def find_and_click_apply_control(page: Any, report: dict[str, Any]) -> bool:
    """Find and click the job-level apply control across the page and its frames."""
    candidates: list[tuple[Any, str]] = []
    frames = page_surfaces(page)
    for surface in frames:
        candidates.extend(collect_apply_controls(surface))

    report["apply_candidates"] = [label for _, label in candidates][:10]
    try:
        report["frame_urls"] = [frame.url for frame in frames][:10]
    except Exception:
        pass

    for element, label in candidates:
        try:
            existing_pages = list(page.context.pages)
            try:
                # Indeed Apply usually opens its form in a popup/new tab.
                with page.context.expect_page(timeout=3000) as popup_info:
                    element.click(timeout=5000)
                new_page = popup_info.value
                new_page.wait_for_load_state("domcontentloaded", timeout=10_000)
                report["_page"] = new_page
            except Exception:
                # No popup — the click already happened; check for same-tab nav.
                page.wait_for_timeout(1000)
                new_pages = [p for p in page.context.pages if p not in existing_pages]
                if new_pages:
                    new_page = new_pages[-1]
                    new_page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    report["_page"] = new_page
            report["clicked_apply_control"] = label
            add_stage(report, "clicked_apply")
            return True
        except Exception as exc:
            report.setdefault("skipped", []).append(f"Apply control '{label}': {exc}")
    return False


def is_apply_surface_url(url: str) -> bool:
    """Whether a URL is one of Indeed's hosted application (SPA) surfaces."""
    lowered = (url or "").lower()
    return any(host in lowered for host in APPLY_SURFACE_HOSTS)


def wait_for_application_surface(page: Any, report: dict[str, Any], timeout_ms: int) -> Any:
    """Wait for a modal/new page/iframe application form after clicking apply.

    Indeed Apply (smartapply.indeed.com) is an async SPA whose fields render
    inside an iframe, so poll the page and all child frames for a while.
    """
    current_page = report.get("_page", page)
    surface = find_application_surface(current_page)
    if surface is not None:
        add_stage(report, "opened_application_form")
        return surface
    # Indeed Apply needs longer than a generic form to render its first step.
    cap_ms = max(min(timeout_ms, 20_000), 12_000)
    deadline = current_page.evaluate("() => Date.now()") + cap_ms
    while current_page.evaluate("() => Date.now()") < deadline:
        surface = find_application_surface(current_page)
        if surface is not None:
            add_stage(report, "opened_application_form")
            return surface
        current_page.wait_for_timeout(500)
    # Some Indeed Apply steps (e.g. the resume step) have only option cards and a
    # hidden file input. Treat any apply-surface URL as a form so the fill/resume
    # pipeline still runs there.
    try:
        if is_apply_surface_url(current_page.url):
            add_stage(report, "opened_application_form")
    except Exception:
        pass
    return current_page


def reach_application_form(page: Any, report: dict[str, Any], timeout_ms: int) -> Any:
    """Move from a job detail page to an actual application form when possible."""
    add_stage(report, "opened_job_page")
    if detect_hard_stop(page, report):
        return page
    if has_application_form_any(page) or has_wizard_surface_any(page):
        add_stage(report, "opened_application_form")
        return page

    if not find_and_click_apply_control(page, report):
        if not detect_hard_stop(page, report):
            report["status_reason"] = "Could not find a safe Apply button on the job page."
        return page

    target = wait_for_application_surface(report.get("_page", page), report, timeout_ms)
    if detect_hard_stop(target, report):
        return target
    if not has_application_form(target):
        report["status_reason"] = "Could not reach application form after clicking Apply."
    return target


def fill_text_controls(page: Any, profile: dict[str, Any], report: dict[str, list[str]]) -> int:
    """Fill visible text inputs and textareas."""
    count = 0
    fields = page.locator("input:not([type]), input[type='text'], input[type='email'], input[type='tel'], textarea")
    for index in range(fields.count()):
        field = fields.nth(index)
        try:
            if not field.is_visible() or not field.is_enabled():
                continue
            prompt = visible_label_for_element(field)
            if is_search_prompt(prompt):
                report["skipped"].append(f"{prompt or 'Search field'} (skipped search field)")
                continue
            answer, reason = answer_for_prompt(prompt, profile)
            if not answer:
                report["needs_review"].append(f"{prompt or 'Unlabeled text field'} ({reason})")
                continue
            field.fill(answer)
            report["filled"].append(f"{prompt or 'Text field'} -> {reason}")
            count += 1
        except Exception as exc:
            report["skipped"].append(f"Text field {index + 1}: {exc}")
    return count


def fill_select_controls(page: Any, profile: dict[str, Any], report: dict[str, list[str]]) -> int:
    """Fill visible select dropdowns when a safe option matches."""
    count = 0
    selects = page.locator("select")
    for index in range(selects.count()):
        select = selects.nth(index)
        try:
            if not select.is_visible() or not select.is_enabled():
                continue
            prompt = visible_label_for_element(select)
            if is_search_prompt(prompt):
                report["skipped"].append(f"{prompt or 'Search dropdown'} (skipped search field)")
                continue
            options = select.locator("option").all_text_contents()
            answer, reason = select_answer_for_prompt(prompt, profile, options)
            if not answer:
                report["needs_review"].append(f"{prompt or 'Dropdown'} ({reason})")
                continue
            select.select_option(label=answer)
            report["filled"].append(f"{prompt or 'Dropdown'} -> {reason}")
            count += 1
        except Exception as exc:
            report["skipped"].append(f"Dropdown {index + 1}: {exc}")
    return count


def fill_radio_controls(page: Any, profile: dict[str, Any], report: dict[str, list[str]]) -> int:
    """Fill radio groups when a safe option can be matched."""
    count = 0
    radios = page.locator("input[type='radio']")
    groups: dict[str, list[tuple[int, str]]] = {}
    prompts: dict[str, str] = {}
    for index in range(radios.count()):
        radio = radios.nth(index)
        try:
            if not radio.is_visible() or not radio.is_enabled():
                continue
            info = radio.evaluate(
                """(el) => {
                  const wrappingLabel = el.closest('label');
                  const fieldset = el.closest('fieldset');
                  return {
                    group: el.getAttribute('name') || el.getAttribute('id') || '',
                    option: wrappingLabel?.innerText || el.getAttribute('value') || '',
                    prompt: fieldset?.querySelector('legend')?.innerText || el.getAttribute('name') || ''
                  };
                }"""
            )
            name = clean_text(info.get("group")) or f"radio-{index}"
            option = clean_text(info.get("option")) or clean_text(radio.get_attribute("value")) or f"Option {index + 1}"
            prompt = clean_text(info.get("prompt")) or name
            prompts.setdefault(name, prompt)
            groups.setdefault(name, []).append((index, option))
        except Exception as exc:
            report["skipped"].append(f"Radio option {index + 1}: {exc}")

    for group_name, options_with_indexes in groups.items():
        prompt = prompts.get(group_name, group_name)
        options = [label for _, label in options_with_indexes]
        answer, reason = select_answer_for_prompt(prompt, profile, options)
        if not answer:
            report["needs_review"].append(f"{prompt or group_name} ({reason})")
            continue
        answer_norm = normalize(answer)
        for index, label in options_with_indexes:
            label_norm = normalize(label)
            if label_norm == answer_norm or answer_norm in label_norm or label_norm in answer_norm:
                try:
                    radios.nth(index).check()
                    report["filled"].append(f"{prompt or group_name} -> {reason}")
                    count += 1
                except Exception as exc:
                    report["skipped"].append(f"Radio group {group_name}: {exc}")
                break
    return count


def inspect_checkbox_controls(page: Any, profile: dict[str, Any], report: dict[str, list[str]]) -> None:
    """Detect checkboxes but leave them for review in v1."""
    checkboxes = page.locator("input[type='checkbox']")
    for index in range(checkboxes.count()):
        checkbox = checkboxes.nth(index)
        try:
            if not checkbox.is_visible() or not checkbox.is_enabled():
                continue
            prompt = visible_label_for_element(checkbox)
            if is_sensitive_prompt(prompt) or is_legal_prompt(prompt):
                report["needs_review"].append(f"{prompt or 'Checkbox'} (needs review: sensitive/legal checkbox)")
                continue
            answer, reason = answer_for_prompt(prompt, profile)
            if answer:
                report["needs_review"].append(f"{prompt or 'Checkbox'} ({reason}; review before checking)")
            else:
                report["needs_review"].append(f"{prompt or 'Checkbox'} ({reason})")
        except Exception as exc:
            report["skipped"].append(f"Checkbox {index + 1}: {exc}")


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
    text = normalize(safe_page_text(surface, max_chars=4000))
    return "resume" in text and ("upload" in text or "add" in text or "file" in text)


def reveal_resume_upload_input(surface: Any) -> None:
    """Click an 'Upload a resume' option (never the builder) to expose a file input."""
    try:
        controls = surface.locator("a, button, label, [role='button']")
        for index in range(controls.count()):
            control = controls.nth(index)
            try:
                if not control.is_visible() or not control.is_enabled():
                    continue
                label = normalize(control_label(control))
                if any(pattern in label for pattern in RESUME_UPLOAD_OPTION_PATTERNS):
                    control.click(timeout=3000)
                    surface.wait_for_timeout(800)
                    return
            except Exception:
                continue
    except Exception:
        return


def fill_file_inputs(page: Any, profile: dict[str, Any], report: dict[str, list[str]]) -> int:
    """Upload the resume into file inputs (hidden inputs included)."""
    resume_path = resolve_resume_path(profile)
    raw_path = clean_text(profile.get("resume_path"))

    resume_step = is_resume_step(page)
    file_inputs = page.locator("input[type='file']")
    if file_inputs.count() == 0 and resume_path is not None:
        # The resume input may only appear after choosing "Upload a resume".
        reveal_resume_upload_input(page)
        file_inputs = page.locator("input[type='file']")

    count = 0
    total = file_inputs.count()
    if total == 0:
        if resume_step:
            if raw_path and raw_path != NOT_SPECIFIED:
                report["needs_review"].append(
                    f"Resume step: resume file not found at {raw_path} — add a real PDF or fix resume_path"
                )
            else:
                report["needs_review"].append("Resume step: set resume_path in your profile to auto-upload")
            report["stopped_reason"] = "resume_needs_review"
        return 0

    for index in range(total):
        field = file_inputs.nth(index)
        try:
            prompt = visible_label_for_element(field) or "Resume upload"
            if resume_path is None:
                if raw_path and raw_path != NOT_SPECIFIED:
                    report["needs_review"].append(
                        f"{prompt}: resume file not found at {raw_path} — add a real PDF or fix resume_path"
                    )
                else:
                    report["needs_review"].append(f"{prompt}: set resume_path in your profile to auto-upload")
                continue
            # set_input_files works even when the input is visually hidden.
            field.set_input_files(str(resume_path))
            report["filled"].append(f"{prompt} -> uploaded {resume_path.name}")
            report["resume_uploaded"] = True
            count += 1
        except Exception as exc:
            report["needs_review"].append(f"Resume upload: could not attach file ({exc})")
            report["stopped_reason"] = "resume_needs_review"
    return count


def inspect_buttons(page: Any, report: dict[str, list[str]]) -> None:
    """Record submit-like buttons that were intentionally left untouched."""
    for _, label, kind in iter_surface_button_controls(page):
        if kind == "submit":
            report["needs_review"].append(f"Left final submit button untouched: {label}")
            report["status_reason"] = "Reached the final submit step — review and submit it yourself."
            report["stopped_reason"] = "stopped_before_submit"
            add_stage(report, "stopped_before_submit")
        elif kind == "never":
            report["skipped"].append(f"Left navigation/control button untouched: {label}")


def fill_current_step(target: Any, profile: dict[str, Any], report: dict[str, Any]) -> int:
    """Fill every safe control on the current wizard step. Returns fields filled."""
    filled_before = len(report["filled"])
    fill_text_controls(target, profile, report)
    fill_select_controls(target, profile, report)
    fill_radio_controls(target, profile, report)
    fill_file_inputs(target, profile, report)
    inspect_checkbox_controls(target, profile, report)
    inspect_buttons(target, report)
    return len(report["filled"]) - filled_before


def iter_surface_button_controls(surface: Any) -> list[tuple[Any, str, str]]:
    """Return [(element, label, kind)] for visible buttons on one page/frame."""
    results: list[tuple[Any, str, str]] = []
    try:
        buttons = surface.locator("button, input[type='submit'], input[type='button'], [role='button']")
        for index in range(buttons.count()):
            element = buttons.nth(index)
            try:
                if not element.is_visible() or not element.is_enabled():
                    continue
                label = control_label(element)
                results.append((element, label, classify_action_button(label)))
            except Exception:
                continue
    except Exception:
        pass
    return results


def iter_button_controls(page: Any) -> list[tuple[Any, str, str]]:
    """Return [(element, label, kind)] for visible buttons across page + frames."""
    results: list[tuple[Any, str, str]] = []
    for surface in page_surfaces(page):
        results.extend(iter_surface_button_controls(surface))
    return results


def surface_fingerprint(surface: Any) -> str:
    """A cheap signature of one page/frame step."""
    try:
        return surface.evaluate(
            """() => {
              const heading = document.querySelector('h1, h2')?.innerText || '';
              const progress = (document.body.innerText.match(/\\d{1,3}%/) || [''])[0];
              const active = document.querySelector('.active')?.id || '';
              const fields = document.querySelectorAll('input, select, textarea').length;
              return `${location.href}|${heading}|${progress}|${active}|${fields}`;
            }"""
        )
    except Exception:
        return ""


def step_fingerprint(page: Any) -> str:
    """A frame-aware signature of the current step, used to detect advancement."""
    return " || ".join(surface_fingerprint(surface) for surface in page_surfaces(page))


def try_advance(page: Any, report: dict[str, Any], timeout_ms: int = 8000) -> bool:
    """Click a Continue/Next button to reach the next step. Never clicks Submit.

    Returns True only if the step actually changed.
    """
    buttons = iter_button_controls(page)

    # Record submit/never buttons we are deliberately leaving alone.
    if any(kind == "submit" for _, _, kind in buttons):
        report["needs_review"].append("Reached the final submit step — review and submit it yourself.")
        report["status_reason"] = "Reached the final submit step — review and submit it yourself."
        report["stopped_reason"] = "stopped_before_submit"
        add_stage(report, "stopped_before_submit")
        return False

    advance = next(((el, label) for el, label, kind in buttons if kind == "advance"), None)
    if advance is None:
        report.setdefault("stopped_reason", "no_safe_advance")
        return False

    element, label = advance
    before = step_fingerprint(page)
    try:
        element.click(timeout=5000)
    except Exception as exc:
        report["skipped"].append(f"Advance button '{label}': {exc}")
        return False

    # Wait for the step to change.
    deadline = page.evaluate("() => Date.now()") + min(timeout_ms, 12_000)
    while page.evaluate("() => Date.now()") < deadline:
        if step_fingerprint(page) != before:
            report.setdefault("advanced_steps", []).append(label)
            report["current_action"] = "Advancing"
            add_stage(report, "advanced_step")
            return True
        page.wait_for_timeout(400)
    return False


def find_application_tab(context: Any, job_url: str) -> Optional[Any]:
    """Return the best already-open application tab, or None.

    SmartApply tabs are preferred over job-detail tabs, and blank tabs are
    ignored so stale about:blank pages do not steal focus.
    """
    job_key = ""
    match = re.search(r"jk=([0-9a-z]+)", job_url or "", re.IGNORECASE)
    if match:
        job_key = match.group(1).lower()
    try:
        pages = list(context.pages)
    except Exception:
        return None
    best_page = None
    best_score = -1
    for page in reversed(pages):
        try:
            url = page.url or ""
        except Exception:
            continue
        if url == "about:blank":
            continue
        if is_apply_surface_url(url):
            score = 100
        elif job_key and job_key in url.lower():
            score = 50
        else:
            continue
        if score > best_score:
            best_page = page
            best_score = score
    return best_page


def persistent_profile_path(data_dir: Path, site: str = "indeed") -> Path:
    """Return the local-only browser profile directory for a job site."""
    return data_dir / "browser_profiles" / site


def find_chrome_executable() -> Optional[str]:
    """Locate a real Google Chrome / Chromium binary, or None if not installed.

    A real browser profile lets the user log in manually and keep that session
    available for later review-first autofill runs.
    """
    system = platform.system()
    candidates: list[str] = []
    if system == "Darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Windows":
        candidates += [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
    else:  # Linux and others
        candidates += [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]

    for path in candidates:
        if path and Path(path).exists():
            return path
    # Fall back to anything on PATH.
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def free_debug_port(preferred: int = 9222) -> int:
    """Return an open localhost port, preferring 9222."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
            except OSError:
                continue
    # Last resort: let the OS choose.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def cdp_endpoint(debug_port: int) -> str:
    """CDP HTTP endpoint for a given debug port."""
    return f"http://127.0.0.1:{debug_port}"


def chrome_debug_ready(debug_port: int) -> bool:
    """Whether Chrome's remote-debugging endpoint is responding."""
    try:
        with urllib.request.urlopen(f"{cdp_endpoint(debug_port)}/json/version", timeout=1) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def session_metadata_path(user_data_dir: Path) -> Path:
    """Return the ignored file that stores the managed Chrome session metadata."""
    return user_data_dir / "session.json"


def save_chrome_session(user_data_dir: Path, debug_port: int) -> dict[str, Any]:
    """Persist the managed Chrome debug port for reconnecting after server restart."""
    user_data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "debug_port": debug_port,
        "profile_path": str(user_data_dir),
        "created_at": int(time.time()),
    }
    with session_metadata_path(user_data_dir).open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.write("\n")
    return payload


def load_chrome_session(user_data_dir: Path) -> Optional[dict[str, Any]]:
    """Load persisted managed Chrome session metadata, if valid enough to use."""
    path = session_metadata_path(user_data_dir)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        debug_port = int(payload.get("debug_port"))
    except (TypeError, ValueError):
        return None
    profile_path = clean_text(payload.get("profile_path"))
    if profile_path and Path(profile_path) != user_data_dir:
        return None
    payload["debug_port"] = debug_port
    return payload


def chrome_process_uses_profile(debug_port: int, user_data_dir: Path) -> bool:
    """Whether a local Chrome process for debug_port uses this profile directory."""
    profile = str(user_data_dir)
    port_arg = f"--remote-debugging-port={debug_port}"
    try:
        output = subprocess.check_output(["ps", "ax", "-o", "command="], text=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(port_arg in line and f"--user-data-dir={profile}" in line for line in output.splitlines())


def profile_lock_files_exist(user_data_dir: Path) -> bool:
    """Whether Chrome profile lock files are present for the managed profile."""
    return any((user_data_dir / name).exists() for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"))


def recover_chrome_session(user_data_dir: Path, preferred_port: int = 9222) -> Optional[dict[str, Any]]:
    """Recover a live managed Chrome session when session.json is missing/stale."""
    session = load_chrome_session(user_data_dir)
    if session and chrome_debug_ready(int(session["debug_port"])):
        return session
    if chrome_debug_ready(preferred_port) and chrome_process_uses_profile(preferred_port, user_data_dir):
        session = save_chrome_session(user_data_dir, preferred_port)
        session["recovered"] = True
        return session
    return None


def launch_user_chrome(
    user_data_dir: Path,
    debug_port: int,
    start_url: Optional[str] = None,
    chrome_path: Optional[str] = None,
    ready_timeout_s: float = 20.0,
) -> "subprocess.Popen":
    """Launch the user's real Chrome as a local process with remote debugging."""
    chrome = chrome_path or find_chrome_executable()
    if not chrome:
        raise RuntimeError(
            "Google Chrome was not found. Install Chrome to use the autofill "
            "login flow."
        )
    user_data_dir.mkdir(parents=True, exist_ok=True)
    args = [
        chrome,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-address=127.0.0.1",
        start_url or INDEED_LOGIN_URL,
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + ready_timeout_s
    while time.time() < deadline:
        if chrome_debug_ready(debug_port):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(
                "Chrome exited before the debugging port became ready. Close the JobFind "
                "Chrome windows and click Open Indeed Login again."
            )
        time.sleep(0.3)
    raise RuntimeError(
        "Timed out waiting for Chrome's remote-debugging port. Close the JobFind "
        "Chrome windows and click Open Indeed Login again."
    )


def connect_user_chrome(pw: Any, debug_port: int) -> tuple[Any, Any]:
    """Attach Playwright to a running real Chrome over CDP.

    Returns (browser, context). The context is the real Chrome's existing
    profile context, so it shares the user's logged-in Indeed session.
    """
    browser = pw.chromium.connect_over_cdp(cdp_endpoint(debug_port))
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, context


def open_browser_context(pw: Any, headless: bool, user_data_dir: Optional[Path] = None) -> tuple[Any, Optional[Any]]:
    """Open a persistent or temporary context (fallback path, not the CDP path)."""
    channel = "chrome" if find_chrome_executable() else None
    if user_data_dir:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        context = pw.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=headless,
            channel=channel,
        )
        return context, None

    browser = pw.chromium.launch(headless=headless, channel=channel)
    return browser.new_context(), browser


def attach_browser_report(
    report: dict[str, Any],
    pw: Any,
    context: Any,
    browser: Optional[Any],
    page: Any,
    user_data_dir: Optional[Path] = None,
) -> None:
    """Attach live Playwright handles to a report for review/cleanup."""
    report["_playwright"] = pw
    report["_browser"] = browser
    report["_context"] = context
    report["_page"] = page
    if user_data_dir:
        report["persistent_profile"] = str(user_data_dir)


def close_report_browser(report: dict[str, Any]) -> None:
    """Close Playwright handles attached to a report, ignoring stale handles.

    Note: `_cdp_browser` (a CDP connection to the user's real Chrome) is left
    untouched on purpose — only the Playwright connection is stopped so the
    user's logged-in Chrome and the filled tab stay open for review.
    """
    for key in ("_context", "_browser"):
        handle = report.get(key)
        if handle is None:
            continue
        try:
            handle.close()
        except Exception:
            pass
    playwright = report.get("_playwright")
    if playwright is not None:
        try:
            playwright.stop()
        except Exception:
            pass
    proc = report.get("_chrome_proc")
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass


def start_playwright() -> Any:
    """Import and start Playwright with a helpful install message."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Run: make install") from exc
    return sync_playwright().start()


def open_indeed_login(
    user_data_dir: Path,
    headless: bool = False,
    login_url: str = INDEED_LOGIN_URL,
    timeout_ms: int = 45_000,
) -> dict[str, Any]:
    """Launch the user's real Chrome at the Indeed login page for manual login."""
    report: dict[str, Any] = {
        "url": login_url,
        "stages": ["opened_login_page"],
        "filled": [],
        "skipped": [],
        "needs_review": [],
        "submitted": False,
        "status_reason": "Log into Indeed in the Chrome window, then return to JobFind and click Resume Autofill.",
    }
    debug_port = free_debug_port()
    proc = launch_user_chrome(user_data_dir, debug_port, start_url=login_url)
    save_chrome_session(user_data_dir, debug_port)
    report["_chrome_proc"] = proc
    report["_debug_port"] = debug_port
    report["_user_data_dir"] = str(user_data_dir)
    report["debug_port"] = debug_port
    return report


def autofill_application(
    url: str,
    profile: dict[str, Any],
    headless: bool = False,
    timeout_ms: int = 45_000,
    user_data_dir: Optional[Path] = None,
    cdp_port: Optional[int] = None,
) -> dict[str, Any]:
    """Fill what is safe in a real Chrome and leave the page open for review.

    Preferred path: attach to a real Chrome over CDP (`cdp_port`) — the same
    browser the user logged into. If no port is given, a fresh Chrome is launched
    against the persistent profile.
    """
    if not url.startswith(("http://", "https://", "file://")):
        raise ValueError("A valid application URL is required.")

    report: dict[str, Any] = {
        "url": url,
        "stages": [],
        "filled": [],
        "skipped": [],
        "needs_review": [],
        "submitted": False,
    }

    if cdp_port is None and not headless:
        report["error"] = "Open Indeed Login first, then run Autofill Application."
        report["status_reason"] = "Open Indeed Login first, then run Autofill Application."
        report["stopped_reason"] = "open_login_first"
        report["current_action"] = "Open Indeed Login first"
        add_stage(report, "open_login_first")
        return report

    pw = start_playwright()

    reused = False
    if cdp_port is not None:
        browser, context = connect_user_chrome(pw, cdp_port)
        report["_playwright"] = pw
        report["_cdp_browser"] = browser  # not "_browser": cleanup must not close it
        report["debug_port"] = cdp_port
        existing_tab = find_application_tab(context, url)
        if existing_tab is not None:
            # Reuse the tab that's already mid-application instead of opening a
            # new one — this is what keeps the wizard on the same page.
            page = existing_tab
            reused = True
        else:
            page = context.new_page()
        report["_page"] = page
    else:
        # Fallback when no real Chrome is installed: bundled/channel Chromium.
        context, browser = open_browser_context(pw, headless, user_data_dir)
        page = context.new_page()
        attach_browser_report(report, pw, context, browser, page, user_data_dir)

    if reused:
        add_stage(report, "reused_application_tab")
        try:
            page.bring_to_front()
        except Exception:
            pass
        try:
            report["active_tab_url"] = page.url
        except Exception:
            pass
        target = wait_for_application_surface(page, report, timeout_ms)
    else:
        report["opened_job_url"] = url
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            report["opened_title"] = page.title()
            report["active_tab_url"] = page.url
        except Exception as exc:
            report["navigation_error"] = str(exc)
            report["error"] = f"Could not navigate Chrome to the selected job: {exc}"
            report["status_reason"] = "Could not navigate Chrome to the selected job."
            report["stopped_reason"] = "navigation_error"
            add_stage(report, "navigation_error")
            report["filled_count"] = 0
            return report
        if page.url == "about:blank":
            report["navigation_error"] = "Chrome tab stayed on about:blank after navigation."
            report["error"] = "Chrome tab stayed on about:blank. Close the JobFind Chrome windows and click Open Indeed Login again."
            report["status_reason"] = report["error"]
            report["stopped_reason"] = "navigation_error"
            add_stage(report, "navigation_error")
            report["filled_count"] = 0
            return report
        target = reach_application_form(page, report, timeout_ms)

    if "opened_application_form" in report["stages"]:
        # Walk the multi-step wizard: fill the current step, click Continue, repeat.
        # try_advance never clicks Submit, so the run always stops for review.
        steps = 0
        while steps < MAX_APPLICATION_STEPS:
            if detect_hard_stop(target, report):
                break
            report["current_action"] = "Filling"
            review_start = len(report["needs_review"])
            fill_current_step(target, profile, report)
            new_review = report["needs_review"][review_start:]
            if new_review:
                report["current_step_needs_review"] = new_review
                report["stopped_reason"] = report.get("stopped_reason") or "needs_review"
                report["status_reason"] = report.get("status_reason") or "Stopped for review before advancing."
                if report.get("stopped_reason") == "stopped_before_submit":
                    report["current_action"] = "Stopped before submit"
                else:
                    report["current_action"] = "Stopped for review"
                add_stage(report, "stopped_for_review")
                break
            report["current_action"] = "Advancing"
            if not try_advance(report.get("_page", page), report):
                if report.get("stopped_reason") == "stopped_before_submit":
                    report["current_action"] = "Stopped before submit"
                else:
                    report["current_action"] = "Stopped for review"
                break
            steps += 1
            target = wait_for_application_surface(report.get("_page", page), report, timeout_ms)
        report["steps_completed"] = steps
        if steps >= MAX_APPLICATION_STEPS:
            report["stopped_reason"] = "max_steps_reached"
            report["status_reason"] = "Stopped after the maximum number of application steps."
            report["current_action"] = "Stopped for review"
    elif "status_reason" not in report:
        report["status_reason"] = "Could not reach application form."
        report["stopped_reason"] = "no_application_form"
    report["filled_count"] = len(report["filled"])
    if report["filled"]:
        add_stage(report, "filled_fields")
    if report["needs_review"]:
        add_stage(report, "needs_review")
    return report


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    """Strip live browser objects from an autofill report."""
    return {
        key: value
        for key, value in report.items()
        if not key.startswith("_")
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Open and autofill a job application.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    report = autofill_application(args.url, load_profile(args.profile), headless=args.headless)
    print(json.dumps(public_report(report), indent=2))
    if not args.headless:
        input("Review the browser page. Press Enter here when finished to close it.")
        close_report_browser(report)


if __name__ == "__main__":
    main()
