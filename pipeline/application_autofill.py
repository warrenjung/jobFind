"""
Best-effort application autofill helpers.

The live autofill path opens a visible Playwright browser and fills obvious
fields from applicant_profile.json. It intentionally never submits forms.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional

import autofill_browser
from autofill_review import (
    COMMON_ANSWER_PROMPTS,
    COMMON_ANSWER_STORAGE_PROMPTS,
    LEGAL_PATTERNS,
    NOT_SPECIFIED,
    SEARCH_FIELD_PATTERNS,
    SELECT_PATTERNS,
    SENSITIVE_PATTERNS,
    TEXT_FIELD_PATTERNS,
    add_review_item,
    answer_for_prompt,
    build_review_item,
    clean_prompt_label,
    clean_text,
    common_custom_answer,
    custom_answers,
    dedupe_review_items,
    group_question_for_element,
    is_legal_prompt,
    is_resume_step,
    is_search_prompt,
    is_sensitive_prompt,
    load_profile,
    normalize,
    prepared_profile,
    resume_path_issue,
    resolve_resume_path,
    reveal_resume_upload_input,
    review_item_legacy_text,
    review_reason_text,
    select_answer_for_prompt,
    split_name,
    sync_legacy_review_strings,
    visible_label_for_element,
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

INDEED_LOGIN_URL = autofill_browser.INDEED_LOGIN_URL


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


def target_for_element(
    element: Any,
    kind: str,
    question: str = "",
    options: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Return enough metadata for the overlay to safely find the field again."""
    try:
        info = element.evaluate(
            """(el) => ({
              tag: (el.tagName || '').toLowerCase(),
              id: el.getAttribute('id') || '',
              name: el.getAttribute('name') || '',
              type: el.getAttribute('type') || '',
              value: el.getAttribute('value') || ''
            })"""
        )
    except Exception:
        info = {}
    target = {
        "kind": kind,
        "question": clean_text(question),
        "tag": clean_text(info.get("tag")),
        "id": clean_text(info.get("id")),
        "name": clean_text(info.get("name")),
        "type": clean_text(info.get("type")),
        "value": clean_text(info.get("value")),
    }
    cleaned_options = [clean_text(option) for option in options or [] if clean_text(option)]
    if cleaned_options:
        target["options"] = cleaned_options
    return {key: value for key, value in target.items() if value}


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
                add_review_item(
                    report,
                    question=prompt,
                    fallback="Unlabeled text field",
                    reason=reason,
                    kind="text",
                    raw_label=prompt,
                    target=target_for_element(field, "text", prompt),
                )
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
                add_review_item(
                    report,
                    question=prompt,
                    fallback="Dropdown",
                    reason=reason,
                    kind="select",
                    raw_label=prompt,
                    options=options,
                    target=target_for_element(select, "select", prompt, options=options),
                )
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
    targets: dict[str, dict[str, Any]] = {}
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
            prompt = (
                clean_prompt_label(info.get("prompt"))
                or group_question_for_element(radio)
                or clean_prompt_label(name)
            )
            prompts.setdefault(name, prompt)
            groups.setdefault(name, []).append((index, option))
            targets.setdefault(name, target_for_element(radio, "radio", prompt))
            targets[name]["options"] = [
                {"index": radio_index, "label": label, "value": clean_text(radios.nth(radio_index).get_attribute("value"))}
                for radio_index, label in groups[name]
            ]
        except Exception as exc:
            report["skipped"].append(f"Radio option {index + 1}: {exc}")

    for group_name, options_with_indexes in groups.items():
        prompt = prompts.get(group_name, group_name)
        options = [label for _, label in options_with_indexes]
        answer, reason = select_answer_for_prompt(prompt, profile, options)
        if not answer:
            add_review_item(
                report,
                question=prompt,
                fallback=group_name,
                reason=reason,
                kind="radio",
                raw_label=group_name,
                options=options,
                target=targets.get(group_name),
            )
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
    """Flag checkbox questions for review — once per group, by the question text.

    Multi-select questions render one checkbox per option ("3"/"4"/"5"); we report
    the group question, not each option, and only once.
    """
    checkboxes = page.locator("input[type='checkbox']")
    seen_groups: set = set()
    for index in range(checkboxes.count()):
        checkbox = checkboxes.nth(index)
        try:
            if not checkbox.is_visible() or not checkbox.is_enabled():
                continue
            # Prefer the group question; fall back to the checkbox's own label
            # (for standalone checkboxes like "I certify the info is accurate").
            prompt = group_question_for_element(checkbox) or visible_label_for_element(checkbox)
            key = (prompt or f"checkbox-{index}").lower()
            if key in seen_groups:
                continue  # another option of the same multi-select question
            seen_groups.add(key)
            if is_sensitive_prompt(prompt) or is_legal_prompt(prompt):
                add_review_item(
                    report,
                    question=prompt,
                    fallback="Checkbox",
                    reason="needs review: sensitive/legal checkbox",
                    kind="checkbox",
                    raw_label=prompt,
                    suggestable=False,
                    target=target_for_element(checkbox, "checkbox", prompt),
                )
                continue
            answer, reason = answer_for_prompt(prompt, profile)
            if answer:
                add_review_item(
                    report,
                    question=prompt,
                    fallback="Checkbox",
                    reason=f"{reason}; review before checking",
                    kind="checkbox",
                    raw_label=prompt,
                    target=target_for_element(checkbox, "checkbox", prompt),
                )
            else:
                add_review_item(
                    report,
                    question=prompt,
                    fallback="Checkbox",
                    reason=reason,
                    kind="checkbox",
                    raw_label=prompt,
                    target=target_for_element(checkbox, "checkbox", prompt),
                )
        except Exception as exc:
            report["skipped"].append(f"Checkbox {index + 1}: {exc}")


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
                add_review_item(
                    report,
                    question="Resume upload",
                    fallback="Resume upload",
                    reason=f"resume file not found at {raw_path} — add a real PDF or fix resume_path",
                    kind="resume",
                    raw_label="Resume step",
                    suggestable=False,
                )
            else:
                add_review_item(
                    report,
                    question="Resume upload",
                    fallback="Resume upload",
                    reason="set resume_path in your profile to auto-upload",
                    kind="resume",
                    raw_label="Resume step",
                    suggestable=False,
                )
            report["stopped_reason"] = "resume_needs_review"
        return 0

    for index in range(total):
        field = file_inputs.nth(index)
        try:
            prompt = visible_label_for_element(field) or "Resume upload"
            if resume_path is None:
                if raw_path and raw_path != NOT_SPECIFIED:
                    add_review_item(
                        report,
                        question=prompt,
                        fallback="Resume upload",
                        reason=f"resume file not found at {raw_path} — add a real PDF or fix resume_path",
                        kind="resume",
                        raw_label=prompt,
                        suggestable=False,
                    )
                else:
                    add_review_item(
                        report,
                        question=prompt,
                        fallback="Resume upload",
                        reason="set resume_path in your profile to auto-upload",
                        kind="resume",
                        raw_label=prompt,
                        suggestable=False,
                    )
                continue
            # set_input_files works even when the input is visually hidden.
            field.set_input_files(str(resume_path))
            report["filled"].append(f"{prompt} -> uploaded {resume_path.name}")
            report["resume_uploaded"] = True
            report["resume_filename"] = resume_path.name
            if select_uploaded_resume_option(page, resume_path.name, report):
                report["resume_selected"] = True
            count += 1
        except Exception as exc:
            add_review_item(
                report,
                question="Resume upload",
                fallback="Resume upload",
                reason=f"could not attach file ({exc})",
                kind="resume",
                raw_label=prompt if 'prompt' in locals() else "Resume upload",
                suggestable=False,
            )
            report["stopped_reason"] = "resume_needs_review"
    return count


def select_uploaded_resume_option(surface: Any, resume_filename: str, report: Optional[dict[str, Any]] = None) -> bool:
    """Select an uploaded resume card when the site requires choosing it before Continue."""
    filename = normalize(resume_filename)
    if not filename:
        return False
    deny = ("build indeed resume", "resume options", "delete resume", "remove resume")
    selectors = (
        "button, label, [role='button'], [tabindex]",
        "article, section, div",
    )
    try:
        for selector in selectors:
            controls = surface.locator(selector)
            limit = min(controls.count(), 80)
            for index in range(limit):
                control = controls.nth(index)
                try:
                    if not control.is_visible():
                        continue
                    text = normalize(control.inner_text(timeout=500) or control.get_attribute("aria-label") or "")
                    if not text or any(bad in text for bad in deny):
                        continue
                    looks_uploaded = filename in text or ("uploaded" in text and "resume" in text)
                    if not looks_uploaded:
                        continue
                    control.click(timeout=2500)
                    surface.wait_for_timeout(500)
                    if report is not None:
                        report.setdefault("filled", []).append("Uploaded resume option -> selected")
                    return True
                except Exception:
                    continue
    except Exception:
        return False
    return False


def inspect_buttons(page: Any, report: dict[str, list[str]]) -> None:
    """Record submit-like buttons that were intentionally left untouched."""
    for _, label, kind in iter_surface_button_controls(page):
        if kind == "submit":
            add_review_item(
                report,
                question=label or "Final submit step",
                fallback="Final submit step",
                reason="review and submit it yourself",
                kind="submit",
                raw_label=label,
                suggestable=False,
            )
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
        add_review_item(
            report,
            question="Final submit step",
            fallback="Final submit step",
            reason="review and submit it yourself",
            kind="submit",
            raw_label="Reached the final submit step",
            suggestable=False,
        )
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
    if report.get("resume_uploaded") and is_resume_step(page):
        filename = clean_text(report.get("resume_filename"))
        if filename and select_uploaded_resume_option(page, filename, report):
            try:
                element.click(timeout=5000)
            except Exception as exc:
                report["skipped"].append(f"Retry advance button '{label}': {exc}")
                return False
            retry_deadline = page.evaluate("() => Date.now()") + min(timeout_ms, 8_000)
            while page.evaluate("() => Date.now()") < retry_deadline:
                if step_fingerprint(page) != before:
                    report.setdefault("advanced_steps", []).append(label)
                    report["current_action"] = "Advancing"
                    add_stage(report, "advanced_step")
                    add_stage(report, "resume_option_selected")
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
    return autofill_browser.persistent_profile_path(data_dir, site)


def find_chrome_executable() -> Optional[str]:
    """Locate a real Google Chrome / Chromium binary, or None if not installed."""
    return autofill_browser.find_chrome_executable()


def free_debug_port(preferred: int = 9222) -> int:
    """Return an open localhost port, preferring 9222."""
    return autofill_browser.free_debug_port(preferred)


def cdp_endpoint(debug_port: int) -> str:
    """CDP HTTP endpoint for a given debug port."""
    return autofill_browser.cdp_endpoint(debug_port)


def chrome_debug_ready(debug_port: int) -> bool:
    """Whether Chrome's remote-debugging endpoint is responding."""
    return autofill_browser.chrome_debug_ready(debug_port)


def session_metadata_path(user_data_dir: Path) -> Path:
    """Return the ignored file that stores the managed Chrome session metadata."""
    return autofill_browser.session_metadata_path(user_data_dir)


def save_chrome_session(user_data_dir: Path, debug_port: int) -> dict[str, Any]:
    """Persist the managed Chrome debug port for reconnecting after server restart."""
    return autofill_browser.save_chrome_session(user_data_dir, debug_port)


def load_chrome_session(user_data_dir: Path) -> Optional[dict[str, Any]]:
    """Load persisted managed Chrome session metadata, if valid enough to use."""
    return autofill_browser.load_chrome_session(user_data_dir)


def chrome_process_uses_profile(debug_port: int, user_data_dir: Path) -> bool:
    """Whether a local Chrome process for debug_port uses this profile directory."""
    return autofill_browser.chrome_process_uses_profile(debug_port, user_data_dir)


def profile_lock_files_exist(user_data_dir: Path) -> bool:
    """Whether Chrome profile lock files are present for the managed profile."""
    return autofill_browser.profile_lock_files_exist(user_data_dir)


def recover_chrome_session(user_data_dir: Path, preferred_port: int = 9222) -> Optional[dict[str, Any]]:
    """Recover a live managed Chrome session when session.json is missing/stale."""
    return autofill_browser.recover_chrome_session(user_data_dir, preferred_port)


def launch_user_chrome(
    user_data_dir: Path,
    debug_port: int,
    start_url: Optional[str] = None,
    chrome_path: Optional[str] = None,
    ready_timeout_s: float = 20.0,
) -> Any:
    """Launch the user's real Chrome as a local process with remote debugging."""
    return autofill_browser.launch_user_chrome(
        user_data_dir,
        debug_port,
        start_url=start_url,
        chrome_path=chrome_path,
        ready_timeout_s=ready_timeout_s,
    )


def connect_user_chrome(pw: Any, debug_port: int) -> tuple[Any, Any]:
    """Attach Playwright to a running real Chrome over CDP."""
    return autofill_browser.connect_user_chrome(pw, debug_port)


def open_browser_context(pw: Any, headless: bool, user_data_dir: Optional[Path] = None) -> tuple[Any, Optional[Any]]:
    """Open a persistent or temporary context."""
    return autofill_browser.open_browser_context(pw, headless, user_data_dir)


def attach_browser_report(
    report: dict[str, Any],
    pw: Any,
    context: Any,
    browser: Optional[Any],
    page: Any,
    user_data_dir: Optional[Path] = None,
) -> None:
    """Attach live Playwright handles to a report for review/cleanup."""
    autofill_browser.attach_browser_report(report, pw, context, browser, page, user_data_dir)


def close_report_browser(report: dict[str, Any]) -> None:
    """Close Playwright handles attached to a report, ignoring stale handles."""
    autofill_browser.close_report_browser(report)


def start_playwright() -> Any:
    """Import and start Playwright with a helpful install message."""
    return autofill_browser.start_playwright()


def open_indeed_login(
    user_data_dir: Path,
    login_url: str = INDEED_LOGIN_URL,
) -> dict[str, Any]:
    """Launch the user's real Chrome at the Indeed login page for manual login."""
    return autofill_browser.open_indeed_login(user_data_dir, login_url=login_url)


def overlay_review_items(report: dict[str, Any], review_items: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """Return the review items that should be shown in the in-page helper."""
    items = review_items or report.get("current_step_review_items") or report.get("review_items") or []
    clean_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question = clean_text(item.get("question")) or clean_text(item.get("legacy_text"))
        if not question:
            continue
        options = [clean_text(option) for option in item.get("options", []) if clean_text(option)]
        generic_question = normalize(question) in {
            "yes no question on this step",
            "question on this step",
            "review this field on the page",
        }
        reason = clean_text(item.get("reason")) or "Needs review"
        if generic_question and options:
            reason = "Question needs review"
        out = {
            "question": question,
            "reason": reason,
            "kind": clean_text(item.get("kind")) or "question",
            "suggestable": bool(item.get("suggestable")),
            "options": options,
            "copy_question": not generic_question,
            "target": item.get("target") if isinstance(item.get("target"), dict) else {},
            "suggestion": clean_text(item.get("suggestion")),
            "suggestion_source": clean_text(item.get("suggestion_source")),
            "suggestion_error": clean_text(item.get("suggestion_error")),
        }
        out["can_accept"] = bool(out["target"] and out["suggestion"])
        out["can_edit"] = bool(out["target"] and out["suggestable"])
        clean_items.append(out)
    return clean_items


def should_show_review_overlay(report: dict[str, Any]) -> bool:
    """Whether an autofill stop is useful to surface directly on the Indeed page."""
    reason = clean_text(report.get("stopped_reason"))
    stages = set(report.get("stages") or [])
    return reason in {"needs_review", "resume_needs_review", "verification_required", "stopped_before_submit"} or bool(
        stages & {"needs_review", "verification_required", "stopped_before_submit"}
    )


def inject_review_overlay(
    page: Any,
    report: dict[str, Any],
    review_items: Optional[list[dict[str, Any]]] = None,
    job: Optional[dict[str, Any]] = None,
    app_base_url: str = "http://127.0.0.1:8000",
) -> bool:
    """Inject a static JobFind review helper into the visible application page."""
    if page is None or not should_show_review_overlay(report):
        return False
    items = overlay_review_items(report, review_items)
    if not items and report.get("stopped_reason") not in {"verification_required", "stopped_before_submit"}:
        return False
    payload = {
        "title": "JobFind review helper",
        "summary": clean_text(report.get("status_reason")) or "Review these fields, then return to JobFind and resume.",
        "safety": "Review everything yourself. JobFind will not submit applications.",
        "items": items,
        "job": job or {},
        "app_base_url": app_base_url.rstrip("/"),
    }
    try:
        page.evaluate(
            """(payload) => {
              const old = document.getElementById('jobfind-review-overlay');
              if (old) old.remove();
              const escape = (value) => String(value || '').replace(/[&<>"']/g, (ch) => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
              }[ch]));
              const panel = document.createElement('section');
              panel.id = 'jobfind-review-overlay';
              panel.setAttribute('aria-label', 'JobFind review helper');
              panel.innerHTML = `
                <style>
                  #jobfind-review-overlay {
                    position: fixed; z-index: 2147483647; right: 18px; top: 18px;
                    width: min(390px, calc(100vw - 36px)); max-height: calc(100vh - 36px);
                    overflow: auto; background: #fbfdfc; color: #14201f;
                    border: 1px solid #c9d8d5; border-radius: 8px;
                    box-shadow: 0 18px 50px rgba(15, 43, 38, 0.22);
                    font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                  }
                  #jobfind-review-overlay * { box-sizing: border-box; }
                  #jobfind-review-overlay header {
                    position: sticky; top: 0; background: #fbfdfc; padding: 14px 14px 10px;
                    border-bottom: 1px solid #d9e4e1; display: flex; gap: 10px; align-items: start;
                  }
                  #jobfind-review-overlay h2 { margin: 0; font-size: 16px; line-height: 1.2; }
                  #jobfind-review-overlay .jf-muted { color: #536461; margin: 4px 0 0; font-size: 12px; }
                  #jobfind-review-overlay .jf-body { padding: 12px 14px 14px; display: grid; gap: 10px; }
                  #jobfind-review-overlay .jf-card {
                    border: 1px solid #d5e0dd; border-radius: 8px; background: #ffffff; padding: 11px;
                    display: grid; gap: 8px;
                  }
                  #jobfind-review-overlay .jf-reason {
                    width: fit-content; border-radius: 999px; padding: 3px 8px; background: #e7f2ef;
                    color: #0c4b43; font-size: 11px; font-weight: 700; text-transform: uppercase;
                    letter-spacing: .02em;
                  }
                  #jobfind-review-overlay .jf-question { margin: 0; font-weight: 700; color: #152624; }
                  #jobfind-review-overlay .jf-options, #jobfind-review-overlay .jf-suggestion, #jobfind-review-overlay .jf-error {
                    margin: 0; color: #435552; font-size: 13px;
                  }
                  #jobfind-review-overlay .jf-suggestion {
                    border-left: 3px solid #18776b; padding: 7px 8px; background: #f2faf7; color: #183532;
                  }
                  #jobfind-review-overlay .jf-error { color: #8a4b15; }
                  #jobfind-review-overlay .jf-actions { display: flex; flex-wrap: wrap; gap: 7px; }
                  #jobfind-review-overlay .jf-editor { display: none; gap: 7px; }
                  #jobfind-review-overlay .jf-editor.jf-open { display: grid; }
                  #jobfind-review-overlay textarea {
                    width: 100%; min-height: 90px; resize: vertical; border: 1px solid #b8cbc7;
                    border-radius: 7px; padding: 8px; font: inherit; color: #14201f; background: #fff;
                  }
                  #jobfind-review-overlay .jf-status { margin: 0; color: #536461; font-size: 12px; }
                  #jobfind-review-overlay button {
                    appearance: none; border: 1px solid #b8cbc7; border-radius: 7px; background: #fff;
                    color: #0b4d45; padding: 7px 9px; font: inherit; font-weight: 700; cursor: pointer;
                  }
                  #jobfind-review-overlay button.jf-primary { background: #0b6f63; color: #fff; border-color: #0b6f63; }
                  #jobfind-review-overlay button:hover { background: #eef7f4; }
                  #jobfind-review-overlay button.jf-primary:hover { background: #095c52; }
                  #jobfind-review-overlay .jf-toggle { margin-left: auto; padding: 5px 8px; }
                  .jobfind-target-highlight {
                    outline: 3px solid #0b6f63 !important; outline-offset: 3px !important;
                    box-shadow: 0 0 0 6px rgba(11, 111, 99, 0.14) !important;
                  }
                  #jobfind-review-overlay.jf-collapsed { max-height: none; overflow: visible; }
                  #jobfind-review-overlay.jf-collapsed .jf-body, #jobfind-review-overlay.jf-collapsed .jf-summary { display: none; }
                  @media (max-width: 700px) {
                    #jobfind-review-overlay { left: 10px; right: 10px; top: 10px; width: auto; }
                  }
                </style>
                <header>
                  <div>
                    <h2>${escape(payload.title)}</h2>
                    <p class="jf-muted jf-summary">${escape(payload.summary)}</p>
                    <p class="jf-muted jf-summary">${escape(payload.safety)}</p>
                  </div>
                  <button class="jf-toggle" type="button" aria-expanded="true">Collapse</button>
                </header>
                <div class="jf-body">
                  ${payload.items.length ? payload.items.map((item, index) => `
                    <article class="jf-card">
                      <span class="jf-reason">${escape(item.reason || 'Needs review')}</span>
                    <p class="jf-question">${escape(item.question)}</p>
                    ${item.options && item.options.length ? `<p class="jf-options">Choices: ${escape(item.options.slice(0, 8).join(', '))}</p>` : ''}
                    ${item.suggestion_source ? `<p class="jf-options">${escape(item.suggestion_source === 'saved answer' ? 'Used saved answer' : `Suggestion source: ${item.suggestion_source}`)}</p>` : ''}
                    ${item.suggestion ? `<p class="jf-suggestion">${escape(item.suggestion)}</p>` : ''}
                    ${item.suggestion_error ? `<p class="jf-error">${escape(item.suggestion_error)}</p>` : ''}
                    <div class="jf-editor" data-editor="${index}">
                      <textarea>${escape(item.suggestion || '')}</textarea>
                      <div class="jf-actions">
                        <button class="jf-primary" type="button" data-action="accept-edit" data-index="${index}">Accept edit</button>
                        <button type="button" data-action="cancel-edit" data-index="${index}">Cancel</button>
                      </div>
                    </div>
                      <div class="jf-actions">
                        ${item.can_accept ? `<button class="jf-primary" type="button" data-action="accept" data-index="${index}">Accept</button>` : ''}
                        ${item.can_edit ? `<button type="button" data-action="edit" data-index="${index}">Edit</button>` : ''}
                        ${item.can_edit ? `<button type="button" data-action="review" data-index="${index}">Edit + Review</button>` : ''}
                        ${item.copy_question ? `<button type="button" data-copy="${index}" data-copy-kind="question">Copy question</button>` : ''}
                        ${item.suggestion && !item.can_accept ? `<button type="button" data-copy="${index}" data-copy-kind="suggestion">Copy suggestion</button>` : ''}
                      </div>
                    </article>
                  `).join('') : `
                    <article class="jf-card">
                      <span class="jf-reason">Manual step</span>
                      <p class="jf-question">${escape(payload.summary)}</p>
                    </article>
                  `}
                  <div class="jf-actions">
                    <button class="jf-primary" type="button" data-action="rerun">Rerun Autofill</button>
                  </div>
                  <p class="jf-status" id="jobfind-overlay-status">After you handle this in Indeed, return to JobFind or click Rerun Autofill.</p>
                </div>
              `;
              document.documentElement.appendChild(panel);
              const norm = (value) => String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
              const cssEscape = (value) => (typeof CSS !== 'undefined' && CSS.escape) ? CSS.escape(String(value)) : String(value).replace(/"/g, '\\"');
              const status = (message) => {
                const el = panel.querySelector('#jobfind-overlay-status');
                if (el) el.textContent = message;
              };
              const dispatchInput = (el) => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
              };
              const clearHighlights = () => {
                document.querySelectorAll('.jobfind-target-highlight').forEach((el) => el.classList.remove('jobfind-target-highlight'));
              };
              const findTargets = (item) => {
                const target = item.target || {};
                const byId = target.id ? document.getElementById(target.id) : null;
                const selector = target.name ? `[name="${cssEscape(target.name)}"]` : '';
                const byName = selector ? Array.from(document.querySelectorAll(selector)) : [];
                if (item.kind === 'radio') return byName.length ? byName : (byId ? [byId] : []);
                if (byId) return [byId];
                return byName;
              };
              const highlightTarget = (item) => {
                clearHighlights();
                const targets = findTargets(item);
                const first = targets.find((el) => el && el.isConnected);
                if (!first) return false;
                targets.forEach((el) => el.classList.add('jobfind-target-highlight'));
                first.scrollIntoView({ behavior: 'smooth', block: 'center' });
                try { first.focus({ preventScroll: true }); } catch (_) {}
                return true;
              };
              const chooseOption = (options, value) => {
                const wanted = norm(value);
                if (!wanted) return null;
                const startsYes = wanted === 'yes' || wanted.startsWith('yes ');
                const startsNo = wanted === 'no' || wanted.startsWith('no ');
                return options.find((option) => {
                  const label = norm(option.text || option.label || option.value);
                  const raw = norm(option.value);
                  if (startsYes && (label === 'yes' || raw === 'yes')) return true;
                  if (startsNo && (label === 'no' || raw === 'no')) return true;
                  return label && (wanted === label || wanted.includes(label) || label.includes(wanted));
                }) || null;
              };
              const fillTarget = (item, value) => {
                const targets = findTargets(item);
                if (!targets.length) return { ok: false, message: 'Could not find that field on the page.' };
                if (!value || !String(value).trim()) return { ok: false, message: 'Write an answer first.' };
                const kind = item.kind;
                if (kind === 'text') {
                  const el = targets[0];
                  if (!('value' in el)) return { ok: false, message: 'That field cannot be filled automatically.' };
                  el.value = value;
                  dispatchInput(el);
                  highlightTarget(item);
                  return { ok: true, message: 'Accepted into the field. Review it before continuing.' };
                }
                if (kind === 'select') {
                  const el = targets[0];
                  const option = chooseOption(Array.from(el.options || []), value);
                  if (!option) return { ok: false, message: 'Could not match that answer to a dropdown choice.' };
                  el.value = option.value;
                  dispatchInput(el);
                  highlightTarget(item);
                  return { ok: true, message: 'Selected the matching choice. Review it before continuing.' };
                }
                if (kind === 'radio') {
                  const option = chooseOption(targets.map((el) => ({
                    value: el.value,
                    text: el.closest('label')?.innerText || el.value,
                    element: el,
                  })), value);
                  if (!option || !option.element) return { ok: false, message: 'Could not safely match that answer to Yes/No.' };
                  option.element.checked = true;
                  dispatchInput(option.element);
                  highlightTarget(item);
                  return { ok: true, message: 'Selected the matching option. Review it before continuing.' };
                }
                if (kind === 'checkbox') {
                  const wanted = norm(value);
                  if (!(wanted === 'yes' || wanted.startsWith('yes ') || wanted === 'true' || wanted.startsWith('check'))) {
                    return { ok: false, message: 'Checkboxes need manual review unless the answer clearly says yes.' };
                  }
                  targets[0].checked = true;
                  dispatchInput(targets[0]);
                  highlightTarget(item);
                  return { ok: true, message: 'Checked the box. Review it before continuing.' };
                }
                return { ok: false, message: 'This item cannot be filled automatically.' };
              };
              const editorFor = (index) => panel.querySelector(`[data-editor="${index}"]`);
              const openEditor = (index, review = false) => {
                const item = payload.items[Number(index)] || {};
                const editor = editorFor(index);
                if (!editor) return;
                editor.classList.add('jf-open');
                const textarea = editor.querySelector('textarea');
                if (textarea && !textarea.value) textarea.value = item.suggestion || '';
                if (textarea) textarea.focus();
                if (review) {
                  const found = highlightTarget(item);
                  status(found ? 'Review the highlighted field, edit the answer, then Accept edit.' : 'Edit the answer here, then copy it manually if needed.');
                } else {
                  status('Edit the answer, then choose Accept edit.');
                }
              };
              const copyText = async (text, button) => {
                try {
                  await navigator.clipboard.writeText(text);
                  const old = button.textContent;
                  button.textContent = 'Copied';
                  setTimeout(() => { button.textContent = old; }, 1000);
                } catch (_) {
                  button.textContent = 'Copy failed';
                }
              };
              const saveAnswer = async (item, answer, source) => {
                try {
                  const response = await fetch(`${payload.app_base_url}/api/saved-answers`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      item,
                      question: item.question,
                      answer,
                      kind: item.kind,
                      source,
                      job: payload.job || {},
                    }),
                  });
                  if (response.ok) {
                    status('Accepted into the field. Saved for next time.');
                  } else {
                    const body = await response.json().catch(() => ({}));
                    status(body.error || 'Accepted into the field, but could not save the answer.');
                  }
                } catch (_) {
                  status('Accepted into the field, but could not reach JobFind to save it.');
                }
              };
              panel.addEventListener('click', (event) => {
                const toggle = event.target.closest('.jf-toggle');
                if (toggle) {
                  const collapsed = panel.classList.toggle('jf-collapsed');
                  toggle.textContent = collapsed ? 'Expand' : 'Collapse';
                  toggle.setAttribute('aria-expanded', String(!collapsed));
                  return;
                }
                const actionButton = event.target.closest('[data-action]');
                if (actionButton) {
                  const action = actionButton.dataset.action;
                  const index = actionButton.dataset.index;
                  const item = payload.items[Number(index)] || {};
                  if (action === 'accept') {
                    const result = fillTarget(item, item.suggestion || '');
                    status(result.message);
                    if (result.ok) saveAnswer(item, item.suggestion || '', 'accepted');
                    return;
                  }
                  if (action === 'edit') {
                    openEditor(index, false);
                    return;
                  }
                  if (action === 'review') {
                    openEditor(index, true);
                    return;
                  }
                  if (action === 'accept-edit') {
                    const editor = editorFor(index);
                    const textarea = editor ? editor.querySelector('textarea') : null;
                    const answer = textarea ? textarea.value : '';
                    const result = fillTarget(item, answer);
                    status(result.message);
                    if (result.ok) saveAnswer(item, answer, 'edited');
                    return;
                  }
                  if (action === 'cancel-edit') {
                    const editor = editorFor(index);
                    if (editor) editor.classList.remove('jf-open');
                    status('Edit canceled.');
                    return;
                  }
                  if (action === 'rerun') {
                    status('Restarting autofill in JobFind...');
                    fetch(`${payload.app_base_url}/api/applications/autofill/overlay-resume`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify(payload.job || {}),
                    }).then((response) => {
                      status(response.ok ? 'Autofill restarted in JobFind.' : 'Could not restart autofill from the overlay.');
                    }).catch(() => {
                      status('Could not reach JobFind. Use Resume Autofill in the local app.');
                    });
                    return;
                  }
                }
                const button = event.target.closest('[data-copy]');
                if (!button) return;
                const item = payload.items[Number(button.dataset.copy)] || {};
                const text = button.dataset.copyKind === 'suggestion' ? item.suggestion : item.question;
                copyText(text || '', button);
              });
            }""",
            payload,
        )
        report["review_overlay_injected"] = True
        add_stage(report, "review_overlay_injected")
        return True
    except Exception as exc:
        report.setdefault("skipped", []).append(f"Review overlay injection: {exc}")
        return False


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
        "review_items": [],
        "current_step_needs_review": [],
        "current_step_review_items": [],
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
            report["current_step_review_items"] = []
            report["current_step_needs_review"] = []
            review_start = len(report["review_items"])
            fill_current_step(target, profile, report)
            new_review = dedupe_review_items(report["review_items"][review_start:])
            if new_review:
                report["current_step_review_items"] = new_review
                sync_legacy_review_strings(report)
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
    report["review_items"] = dedupe_review_items(report["review_items"])
    report["current_step_review_items"] = dedupe_review_items(report.get("current_step_review_items", []))
    sync_legacy_review_strings(report)
    if report["filled"]:
        add_stage(report, "filled_fields")
    if report["review_items"]:
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
