const form = document.querySelector("#search-form");
const button = document.querySelector("#run-button");
const setupCard = document.querySelector("#setup-card");
const setupList = document.querySelector("#setup-list");
const setupSummary = document.querySelector("#setup-summary");
const setupToggle = document.querySelector("#setup-toggle");
const setupCollapse = document.querySelector("#setup-collapse");
const statusText = document.querySelector("#status-text");
const statusDetail = document.querySelector("#status-detail");
const logs = document.querySelector("#logs");
const frame = document.querySelector("#results-frame");
const selectedJobEl = document.querySelector("#selected-job");
const notesEl = document.querySelector("#application-notes");
const profileList = document.querySelector("#profile-list");
const profileForm = document.querySelector("#profile-form");
const profileFields = document.querySelector("#profile-fields");
const profileMessage = document.querySelector("#profile-message");
const editProfileButton = document.querySelector("#edit-profile-button");
const cancelProfileButton = document.querySelector("#cancel-profile-button");
const resumeFileInput = document.querySelector("#resume-file");
const uploadResumeButton = document.querySelector("#upload-resume-button");
const resumeCurrent = document.querySelector("#resume-current");
const sectionCollapseButtons = Array.from(document.querySelectorAll(".section-collapse"));
const commonAnswersForm = document.querySelector("#common-answers-form");
const commonAnswerFieldsEl = document.querySelector("#common-answer-fields");
const commonAnswersMessage = document.querySelector("#common-answers-message");
const commonAnswerSuggestion = document.querySelector("#common-answer-suggestion");
const commonAnswerSuggestionText = document.querySelector("#common-answer-suggestion-text");
const useSuggestionButton = document.querySelector("#use-suggestion-button");
const dismissSuggestionButton = document.querySelector("#dismiss-suggestion-button");
const savedAnswersList = document.querySelector("#saved-answers-list");
const savedAnswersMessage = document.querySelector("#saved-answers-message");
const refreshSavedAnswersButton = document.querySelector("#refresh-saved-answers-button");
const applicationList = document.querySelector("#application-list");
const applicationButtons = Array.from(document.querySelectorAll("[data-application-status]"));
const indeedLoginButton = document.querySelector("#indeed-login-button");
const autofillButton = document.querySelector("#autofill-button");
const resumeAutofillButton = document.querySelector("#resume-autofill-button");
const autofillSummary = document.querySelector("#autofill-summary");
const autofillLog = document.querySelector("#autofill-log");
const reviewQuestionsEl = document.querySelector("#review-questions");
const reviewQuestionSuggestion = document.querySelector("#review-question-suggestion");
const reviewQuestionSuggestionText = document.querySelector("#review-question-suggestion-text");
const copyReviewSuggestionButton = document.querySelector("#copy-review-suggestion-button");
const dismissReviewSuggestionButton = document.querySelector("#dismiss-review-suggestion-button");
let pollTimer = null;
let autofillPollTimer = null;
let selectedJob = null;
let currentProfile = {};
let commonAnswerFields = [];
let commonAnswers = {};
let savedAnswers = [];
let editingSavedAnswerKey = "";
let suggestionTargetKey = "";
let setupStatus = null;
const setupPreferenceKey = "jobfind.setup.open";
const setupOrder = ["profile", "resume", "indeed_login", "results"];
const preservedApplicationStatuses = new Set(["Applied", "Skipped", "Needs follow-up"]);
const profileFieldDefs = [
  { key: "name", label: "Name" },
  { key: "first_name", label: "First name" },
  { key: "last_name", label: "Last name" },
  { key: "preferred_name", label: "Preferred name" },
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "city", label: "City" },
  { key: "state", label: "State" },
  { key: "school", label: "School" },
  { key: "graduation_year", label: "Graduation year" },
  { key: "education_level", label: "Education level" },
  { key: "availability", label: "Availability", multiline: true },
  { key: "available_start_date", label: "Available start date" },
  { key: "desired_hours", label: "Desired hours" },
  { key: "age_or_work_permit", label: "Age/work permit answer", multiline: true },
  { key: "work_eligibility", label: "Work eligibility" },
  { key: "resume_path", label: "Resume path" },
  { key: "short_intro", label: "Short intro", multiline: true }
];

async function fetchJson(url, options) {
  const response = await fetch(url, options || { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Request to ${url} failed (${response.status})`);
  }
  return response.json();
}

async function refreshStatus() {
  const data = await fetchJson("/api/status");
  statusText.textContent = data.status.toUpperCase();
  statusDetail.textContent = data.location ? `Location: ${data.location}` : "Ready to search.";
  logs.textContent = data.logs || "";
  logs.scrollTop = logs.scrollHeight;
  button.disabled = data.status === "running";
  if (data.status === "succeeded") {
    frame.src = `/jobs_clean.html?ts=${Date.now()}`;
    await loadSetupStatus();
  }
  if (data.status !== "running" && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function escapeText(value) {
  return String(value || "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}

function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(String(value || ""));
  return String(value || "").replace(/["\\]/g, "\\$&");
}

function profileValueText(value) {
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value || "");
}

function renderProfileForm(profile) {
  if (!profileFields) return;
  profileFields.innerHTML = profileFieldDefs.map(field => {
    const value = escapeText(profileValueText(profile[field.key]));
    const input = field.multiline
      ? `<textarea name="${field.key}">${value}</textarea>`
      : `<input name="${field.key}" value="${value}">`;
    return `<label>${escapeText(field.label)}${input}</label>`;
  }).join("");
}

function showCommonAnswersMessage(message, isError = false) {
  if (!commonAnswersMessage) return;
  commonAnswersMessage.textContent = message || "";
  commonAnswersMessage.style.color = isError ? "#a33b2f" : "var(--muted)";
}

function showSavedAnswersMessage(message, isError = false) {
  if (!savedAnswersMessage) return;
  savedAnswersMessage.textContent = message || "";
  savedAnswersMessage.style.color = isError ? "#a33b2f" : "var(--muted)";
}

function savedAnswerEnabled(row) {
  return row.autofill_enabled !== false;
}

function savedAnswerMeta(row) {
  const parts = [
    row.source ? `Source: ${row.source}` : "",
    row.kind ? `Type: ${row.kind}` : "",
    row.employer || row.job_title ? [row.job_title, row.employer].filter(Boolean).join(" · ") : "",
    row.updated_at ? `Updated: ${row.updated_at}` : ""
  ].filter(Boolean);
  return parts.join(" · ");
}

function renderSavedAnswers(payload) {
  if (!savedAnswersList) return;
  savedAnswers = Array.isArray(payload.answers) ? payload.answers : [];
  if (!savedAnswers.length) {
    savedAnswersList.innerHTML = '<article class="saved-answer-card empty">No saved answers yet. Use Accept or Accept edit in the Indeed helper to create one.</article>';
    showSavedAnswersMessage("Saved answers will appear here after you accept answers in the Indeed helper.");
    return;
  }
  savedAnswersList.innerHTML = savedAnswers.map(row => {
    const key = escapeText(row.key || "");
    const enabled = savedAnswerEnabled(row);
    const isEditing = row.key && row.key === editingSavedAnswerKey;
    if (isEditing) {
      return `
        <article class="saved-answer-card editing" data-saved-key="${key}">
          <strong>${escapeText(row.question || "Saved question")}</strong>
          <textarea data-saved-answer-edit="${key}">${escapeText(row.answer || "")}</textarea>
          <label class="saved-answer-toggle">
            <input type="checkbox" data-saved-enabled-edit="${key}" ${enabled ? "checked" : ""}>
            Use for autofill
          </label>
          <div class="saved-answer-actions">
            <button type="button" data-saved-save="${key}">Save</button>
            <button class="secondary-button" type="button" data-saved-cancel="${key}">Cancel</button>
          </div>
        </article>
      `;
    }
    return `
      <article class="saved-answer-card ${enabled ? "" : "disabled"}" data-saved-key="${key}">
        <div class="saved-answer-topline">
          <strong>${escapeText(row.question || "Saved question")}</strong>
          <span>${enabled ? "Autofill on" : "Autofill off"}</span>
        </div>
        <p>${escapeText(row.answer || "")}</p>
        <small>${escapeText(savedAnswerMeta(row) || "No metadata")}</small>
        <label class="saved-answer-toggle">
          <input type="checkbox" data-saved-toggle="${key}" ${enabled ? "checked" : ""}>
          Use for autofill
        </label>
        <div class="saved-answer-actions">
          <button class="secondary-button" type="button" data-saved-edit="${key}">Edit</button>
          <button class="secondary-button danger-button" type="button" data-saved-delete="${key}">Delete</button>
        </div>
      </article>
    `;
  }).join("");
  showSavedAnswersMessage(`${savedAnswers.length} saved answer${savedAnswers.length === 1 ? "" : "s"} available.`);
}

async function loadSavedAnswers() {
  if (!savedAnswersList) return;
  try {
    renderSavedAnswers(await fetchJson("/api/saved-answers"));
  } catch (error) {
    showSavedAnswersMessage("Could not load saved answers.", true);
  }
}

async function updateSavedAnswer(key, updates) {
  const response = await fetch("/api/saved-answers/update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, ...updates })
  });
  const result = await response.json().catch(() => ({ error: "Could not update saved answer." }));
  if (!response.ok) {
    showSavedAnswersMessage(result.error || "Could not update saved answer.", true);
    return false;
  }
  editingSavedAnswerKey = "";
  await loadSavedAnswers();
  showSavedAnswersMessage("Saved answer updated.");
  return true;
}

async function deleteSavedAnswer(key) {
  if (!window.confirm("Delete this saved answer?")) return;
  const response = await fetch("/api/saved-answers/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key })
  });
  const result = await response.json().catch(() => ({ error: "Could not delete saved answer." }));
  if (!response.ok) {
    showSavedAnswersMessage(result.error || "Could not delete saved answer.", true);
    return;
  }
  if (editingSavedAnswerKey === key) editingSavedAnswerKey = "";
  await loadSavedAnswers();
  showSavedAnswersMessage("Saved answer deleted.");
}

function renderCommonAnswers(payload) {
  if (!commonAnswerFieldsEl) return;
  commonAnswerFields = payload.fields || [];
  commonAnswers = payload.answers || {};
  commonAnswerFieldsEl.innerHTML = commonAnswerFields.map(field => {
    const value = escapeText(profileValueText(commonAnswers[field.key]));
    return `
      <article class="common-answer-field">
        <label for="common-answer-${field.key}">${escapeText(field.label)}</label>
        <textarea id="common-answer-${field.key}" name="${field.key}" data-common-key="${field.key}">${value}</textarea>
        <button class="secondary-button" type="button" data-ai-polish="${field.key}">Improve with AI</button>
      </article>
    `;
  }).join("");
  const providerMessage = payload.ai_message || "AI polish is optional.";
  showCommonAnswersMessage(`Used by autofill. ${providerMessage}`);
}

async function loadCommonAnswers() {
  const response = await fetch("/api/common-answers", { cache: "no-store" });
  if (!response.ok) {
    showCommonAnswersMessage("Could not load common answers.", true);
    return;
  }
  renderCommonAnswers(await response.json());
}

async function saveCommonAnswers(event) {
  event.preventDefault();
  const answers = Object.fromEntries(new FormData(commonAnswersForm).entries());
  const response = await fetch("/api/common-answers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers })
  });
  const result = await response.json().catch(() => ({ error: "Could not save common answers." }));
  if (!response.ok) {
    showCommonAnswersMessage(result.error || "Could not save common answers.", true);
    return;
  }
  commonAnswers = result.answers || {};
  await loadCommonAnswers();
  await loadProfile();
  showCommonAnswersMessage("Common answers saved.");
}

async function polishCommonAnswer(key) {
  const field = commonAnswersForm ? commonAnswersForm.querySelector(`[name="${key}"]`) : null;
  if (!field) return;
  const draft = field.value.trim();
  if (!draft) {
    showCommonAnswersMessage("Write an answer first, then ask AI to improve it.", true);
    field.focus();
    return;
  }
  showCommonAnswersMessage("Asking AI for a review-first suggestion...");
  const response = await fetch("/api/common-answers/improve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, draft, job: selectedJob || {} })
  });
  const result = await response.json().catch(() => ({ error: "Could not improve this answer." }));
  if (!response.ok) {
    showCommonAnswersMessage(result.error || "Could not improve this answer.", true);
    return;
  }
  suggestionTargetKey = key;
  if (commonAnswerSuggestionText) commonAnswerSuggestionText.textContent = result.suggestion || "";
  if (commonAnswerSuggestion) commonAnswerSuggestion.classList.remove("hidden");
  showCommonAnswersMessage("Review the suggestion below. It has not been saved.");
}

function useSuggestion() {
  if (!suggestionTargetKey || !commonAnswerSuggestionText || !commonAnswersForm) return;
  const field = commonAnswersForm.querySelector(`[name="${suggestionTargetKey}"]`);
  if (field) {
    field.value = commonAnswerSuggestionText.textContent || "";
    field.focus();
  }
  if (commonAnswerSuggestion) commonAnswerSuggestion.classList.add("hidden");
  showCommonAnswersMessage("Suggestion inserted. Save Common Answers when ready.");
}

function normalizeReviewItem(item) {
  if (item && typeof item === "object") {
    const question = String(item.question || item.raw_label || "").trim();
    const reason = String(item.reason || item.reason_detail || "").trim();
    return {
      question: question || "Question on the application — review it on the page.",
      reason: reason || "Needs review",
      kind: String(item.kind || "question"),
      suggestable: Boolean(item.suggestable),
      rawLabel: String(item.raw_label || ""),
      options: Array.isArray(item.options) ? item.options : [],
      suggestion: String(item.suggestion || ""),
      suggestionSource: String(item.suggestion_source || ""),
      suggestionError: String(item.suggestion_error || "")
    };
  }
  const text = String(item || "").replace(/\s*\([^)]*\)\s*$/, "").trim();
  return {
    question: text || "Question on the application — review it on the page.",
    reason: "Needs review",
    kind: "question",
    suggestable: false,
    rawLabel: String(item || ""),
    options: []
  };
}

function reviewOptionsSummary(item) {
  if (!item.options.length) return "";
  return item.options.slice(0, 6).join(", ");
}

function renderReviewQuestions(items) {
  if (!reviewQuestionsEl) return;
  const rows = (items || []).filter(Boolean).map(normalizeReviewItem);
  if (!rows.length) {
    reviewQuestionsEl.classList.add("hidden");
    reviewQuestionsEl.innerHTML = "";
    reviewQuestionsEl.dataset.questions = "[]";
    return;
  }
  reviewQuestionsEl.classList.remove("hidden");
  reviewQuestionsEl.innerHTML = rows.map((item, index) => {
    const options = reviewOptionsSummary(item);
    return `
      <article class="review-question">
        <strong>${escapeText(item.reason || "Needs review")}</strong>
        <span>${escapeText(item.question)}</span>
        ${options ? `<small>Choices: ${escapeText(options)}</small>` : ""}
        ${item.suggestion ? `<p class="inline-suggestion">${escapeText(item.suggestion)}</p>` : ""}
        ${item.suggestionError ? `<small class="inline-warning">${escapeText(item.suggestionError)}</small>` : ""}
        ${item.suggestion ? `<button class="secondary-button" type="button" data-copy="${escapeText(item.suggestion)}">Copy suggestion</button>` : ""}
        ${item.suggestable ? `<button class="secondary-button" type="button" data-question-suggest="${index}">Get AI suggestion</button>` : ""}
      </article>
    `;
  }).join("");
  reviewQuestionsEl.dataset.questions = JSON.stringify(rows);
}

async function suggestReviewAnswer(index) {
  if (!reviewQuestionsEl) return;
  const questions = JSON.parse(reviewQuestionsEl.dataset.questions || "[]");
  const item = normalizeReviewItem(questions[Number(index)] || {});
  if (!item.question || !item.suggestable) return;
  if (reviewQuestionSuggestionText) reviewQuestionSuggestionText.textContent = "Asking AI for a review-first suggestion...";
  if (reviewQuestionSuggestion) reviewQuestionSuggestion.classList.remove("hidden");
  const response = await fetch("/api/application-questions/suggest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: item.question, review_item: item, job: selectedJob || {} })
  });
  const result = await response.json().catch(() => ({ error: "Could not suggest an answer." }));
  if (!response.ok) {
    if (reviewQuestionSuggestionText) reviewQuestionSuggestionText.textContent = result.error || "Could not suggest an answer.";
    return;
  }
  if (reviewQuestionSuggestionText) reviewQuestionSuggestionText.textContent = result.suggestion || "";
}

function showProfileMessage(message, isError = false) {
  if (!profileMessage) return;
  profileMessage.textContent = message || "";
  profileMessage.style.color = isError ? "#a33b2f" : "var(--muted)";
}

function shouldShowSetup(status) {
  if (!status) return true;
  if (!status.all_ready) return true;
  return localStorage.getItem(setupPreferenceKey) === "open";
}

function renderSetup(status) {
  setupStatus = status;
  if (!setupCard || !setupList || !setupSummary || !setupToggle) return;
  const show = shouldShowSetup(status);
  setupCard.classList.toggle("hidden", !show);
  setupToggle.classList.toggle("hidden", show);
  if (setupCollapse) setupCollapse.hidden = !status.all_ready;
  setupSummary.textContent = status.all_ready
    ? "Everything looks ready. You can search and use Apply Assistant."
    : "Finish these local setup steps to get smoother searches and applications.";
  const checks = status.checks || {};
  setupList.innerHTML = setupOrder.map(key => {
    const check = checks[key] || {};
    const ready = Boolean(check.ready);
    return `
      <article class="setup-item ${ready ? "ready" : ""}">
        <span class="setup-badge">${ready ? "Ready" : "Needs setup"}</span>
        <strong>${escapeText(check.label || key)}</strong>
        <span class="setup-message">${escapeText(check.message || "")}</span>
        <button class="secondary-button setup-action" type="button" data-setup-action="${key}">
          ${escapeText(check.action || "Open")}
        </button>
      </article>
    `;
  }).join("");
}

async function loadSetupStatus() {
  const response = await fetch("/api/setup-status", { cache: "no-store" });
  if (!response.ok) return;
  renderSetup(await response.json());
}

async function openProfileEditor(focusName) {
  if (!Object.keys(currentProfile).length) await loadProfile();
  renderProfileForm(currentProfile);
  if (profileForm) profileForm.classList.remove("hidden");
  showProfileMessage("Editing local applicant_profile.json.");
  const panel = document.querySelector(".apply-panel");
  if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
  const target = focusName && profileForm ? profileForm.querySelector(`[name="${focusName}"]`) : null;
  if (target) target.focus();
}

function resumeFilenameFromPath(path) {
  const text = String(path || "").trim();
  if (!text) return "";
  return text.split(/[\\/]/).pop();
}

function renderResumeCurrent() {
  if (!resumeCurrent) return;
  const name = resumeFilenameFromPath(currentProfile.resume_path);
  resumeCurrent.textContent = name ? `Current resume: ${name}` : "No resume uploaded.";
}

async function uploadResume() {
  if (!resumeFileInput) return;
  const file = resumeFileInput.files && resumeFileInput.files[0];
  if (!file) {
    showProfileMessage("Choose a resume file to upload.", true);
    return;
  }
  if (uploadResumeButton) uploadResumeButton.disabled = true;
  try {
    const response = await fetch(
      `/api/applicant-profile/upload-resume?filename=${encodeURIComponent(file.name)}`,
      { method: "POST", body: file }
    );
    const result = await response.json().catch(() => ({ error: "Could not upload resume." }));
    if (!response.ok) {
      showProfileMessage(result.error || "Could not upload resume.", true);
      return;
    }
    if (resumeFileInput) resumeFileInput.value = "";
    await loadProfile();
    const warning = (result.warnings || [])[0];
    showProfileMessage(warning || `Uploaded ${result.resume_filename}.`, Boolean(warning));
  } catch (error) {
    showProfileMessage("Could not upload resume.", true);
  } finally {
    if (uploadResumeButton) uploadResumeButton.disabled = false;
  }
}

function applySectionCollapsed(section, button, collapsed) {
  section.classList.toggle("collapsed", collapsed);
  button.textContent = collapsed ? "Show" : "Hide";
  button.setAttribute("aria-expanded", String(!collapsed));
}

function setupCollapsibleSection(button) {
  const sectionId = button.dataset.section;
  const section = sectionId ? document.getElementById(sectionId) : null;
  if (!section) return;
  const storageKey = `jobfind.section.${sectionId}.collapsed`;
  applySectionCollapsed(section, button, localStorage.getItem(storageKey) === "1");
  button.addEventListener("click", () => {
    const next = !section.classList.contains("collapsed");
    localStorage.setItem(storageKey, next ? "1" : "0");
    applySectionCollapsed(section, button, next);
  });
}

async function loadProfile() {
  const profile = await fetchJson("/api/applicant-profile");
  currentProfile = profile;
  renderProfileForm(currentProfile);
  renderResumeCurrent();
  const rows = Object.entries(profile).filter(([, value]) => profileValueText(value).trim());
  if (!rows.length) {
    profileList.innerHTML = "<li>Add your info to applicant_profile.json.</li>";
    return;
  }
  profileList.innerHTML = rows.map(([key, value]) => `
    <li class="profile-item">
      <span><strong>${escapeText(key.replaceAll("_", " "))}</strong><br>${escapeText(profileValueText(value))}</span>
      <button class="copy-button" type="button" data-copy="${escapeText(profileValueText(value))}">Copy</button>
    </li>
  `).join("");
}

async function saveProfile(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(profileForm).entries());
  const response = await fetch("/api/applicant-profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const result = await response.json().catch(() => ({ error: "Could not save profile." }));
  if (!response.ok) {
    showProfileMessage(result.error || "Could not save profile.", true);
    return;
  }
  currentProfile = result.profile || {};
  await loadProfile();
  await loadSetupStatus();
  const warnings = result.warnings || [];
  showProfileMessage(warnings.length ? `Saved. ${warnings.join(" ")}` : "Profile saved.");
  if (profileForm) profileForm.classList.add("hidden");
}

async function loadApplications() {
  const payload = await fetchJson("/api/applications");
  const rows = payload.applications || [];
  applicationList.innerHTML = rows.length
    ? rows.slice(0, 8).map(row => `
        <li><strong>${escapeText(row.status)}</strong> · ${escapeText(row.title || "Untitled job")}<br>${escapeText(row.company || "")}</li>
      `).join("")
    : "<li>No applications tracked yet.</li>";
  syncResultsApplicationStatuses(rows);
  return rows;
}

function syncResultsApplicationStatuses(rows) {
  if (!frame || !frame.contentWindow) return;
  frame.contentWindow.postMessage({
    type: "jobfind:application-statuses",
    applications: Array.isArray(rows) ? rows : []
  }, "*");
}

async function broadcastApplicationStatuses() {
  try {
    const payload = await fetchJson("/api/applications");
    syncResultsApplicationStatuses(payload.applications || []);
  } catch (error) {
    // The result page should still work without application status badges.
  }
}

function renderSelectedJob(record) {
  selectedJob = record;
  applicationButtons.forEach(button => button.disabled = !selectedJob);
  if (autofillButton) autofillButton.disabled = !selectedJob;
  if (!selectedJob) {
    selectedJobEl.innerHTML = "<strong>No job selected</strong><p>Click Apply Assistant on an Indeed job card.</p>";
    return;
  }
  selectedJobEl.innerHTML = `
    <strong>${escapeText(selectedJob.title || "Untitled job")}</strong>
    <p>${escapeText(selectedJob.company || "Unknown employer")} · Score ${escapeText(selectedJob.score || "")}</p>
    <p>Status: ${escapeText(selectedJob.status || "Opened")}</p>
  `;
  if (notesEl && selectedJob.notes !== undefined) {
    notesEl.value = selectedJob.notes || "";
  }
}

async function refreshAutofillStatus() {
  const data = await fetchJson("/api/autofill/status");
  const report = data.report || {};
  const filled = report.filled_count || 0;
  const stages = report.stages || [];
  const stoppedReason = String(report.stopped_reason || "");
  const needsLogin = stages.includes("login_required") || stoppedReason === "login_required" || String(report.status_reason || "").toLowerCase().includes("login");
  const needsVerification = stages.includes("verification_required") || stoppedReason === "verification_required";
  if (autofillSummary) {
    if (data.status === "running") autofillSummary.textContent = report.current_action ? `Autofill: ${report.current_action}.` : "Autofill is running in a visible browser.";
    else if (needsVerification) autofillSummary.textContent = "Verification is required. Handle it manually in Chrome, then click Resume Autofill.";
    else if (needsLogin) autofillSummary.textContent = "Login or verification is required. Log in with Open Indeed Login, then click Resume Autofill.";
    else if (stoppedReason === "stopped_before_submit") autofillSummary.textContent = `Stopped before submit. Filled ${filled} fields. Review and submit manually.`;
    else if (stoppedReason === "open_login_first") autofillSummary.textContent = "Open Indeed Login first, log in, then run Autofill Application again.";
    else if (stoppedReason === "navigation_error") autofillSummary.textContent = "Chrome did not open the selected job. Close JobFind Chrome windows, click Open Indeed Login, then try again.";
    else if (stoppedReason === "resume_needs_review") autofillSummary.textContent = "Resume needs review. Fix resume_path or upload manually, then Resume Autofill.";
    else if (stoppedReason === "needs_review") autofillSummary.textContent = "Stopped for review. Check the highlighted fields, then Resume Autofill.";
    else if (data.status === "succeeded") autofillSummary.textContent = `Autofill finished. Filled ${filled} fields. Review before submitting.`;
    else if (data.status === "failed") autofillSummary.textContent = report.error || "Autofill failed.";
    else autofillSummary.textContent = selectedJob ? "Ready to autofill the selected job." : "Select a job to start autofill.";
  }
  if (autofillLog) {
    autofillLog.textContent = data.logs || "";
    autofillLog.scrollTop = autofillLog.scrollHeight;
  }
  renderReviewQuestions(report.current_step_review_items || report.current_step_needs_review || []);
  if (autofillButton) autofillButton.disabled = !selectedJob || data.status === "running";
  const canResume = needsLogin || needsVerification || ["needs_review", "resume_needs_review", "no_safe_advance"].includes(stoppedReason);
  if (resumeAutofillButton) resumeAutofillButton.disabled = !selectedJob || data.status === "running" || !canResume;
  if (data.status !== "running" && autofillPollTimer) {
    clearInterval(autofillPollTimer);
    autofillPollTimer = null;
    await loadApplications();
    await loadSavedAnswers();
  }
}

async function saveApplicationStatus(status) {
  if (!selectedJob) return;
  const response = await fetch("/api/applications/status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...selectedJob,
      status,
      notes: notesEl ? notesEl.value : ""
    })
  });
  const payload = await response.json();
  if (!response.ok) {
    renderSelectedJob({ ...selectedJob, status: payload.error || "Error" });
    return;
  }
  renderSelectedJob(payload.application);
  await loadApplications();
}

async function markSelectedApplicationStatus(status, options = {}) {
  if (!selectedJob) return null;
  if (options.preserveFinal && preservedApplicationStatuses.has(selectedJob.status || "")) {
    await loadApplications();
    return selectedJob;
  }
  const response = await fetch("/api/applications/status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...selectedJob,
      status,
      notes: notesEl ? notesEl.value : ""
    })
  });
  const payload = await response.json().catch(() => ({ error: "Could not update application status." }));
  if (!response.ok) {
    renderSelectedJob({ ...selectedJob, status: payload.error || "Error" });
    return null;
  }
  renderSelectedJob(payload.application);
  await loadApplications();
  return payload.application;
}

async function openApplyAssistant(job) {
  selectedJob = job;
  const initialStatus = preservedApplicationStatuses.has(job.status || "") ? job.status : "Opened";
  renderSelectedJob({ ...job, status: initialStatus });
  const response = await fetch("/api/applications/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(job)
  });
  const payload = await response.json();
  if (response.ok) {
    renderSelectedJob(payload.application);
    await loadApplications();
  }
  await refreshAutofillStatus();
}

async function startAutofill() {
  return startAutofillRequest(false);
}

async function resumeAutofill() {
  return startAutofillRequest(true);
}

async function startAutofillRequest(resume) {
  if (!selectedJob) return;
  const response = await fetch(resume ? "/api/applications/autofill/resume" : "/api/applications/autofill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(selectedJob)
  });
  const payload = await response.json();
  if (!response.ok) {
    if (autofillSummary) autofillSummary.textContent = payload.error || "Could not start autofill.";
    return;
  }
  await markSelectedApplicationStatus("Autofilled", { preserveFinal: true });
  await refreshAutofillStatus();
  autofillPollTimer = setInterval(refreshAutofillStatus, 1500);
}

async function openIndeedLogin() {
  const response = await fetch("/api/indeed-login/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({})
  });
  const payload = await response.json();
  if (!response.ok) {
    if (autofillSummary) autofillSummary.textContent = payload.error || "Could not open Indeed login.";
    return;
  }
  if (autofillSummary) autofillSummary.textContent = payload.status === "already_open"
    ? "Recovered existing JobFind Chrome session. Continue in that Chrome window."
    : "Indeed login browser opened. Log in there, then return here.";
  if (autofillLog) autofillLog.textContent = "Open Indeed Login started. This stores only browser session files under local ignored data/.";
  setTimeout(loadSetupStatus, 1000);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  const body = new URLSearchParams(new FormData(form));
  const response = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: "Could not start search." }));
    statusText.textContent = "ERROR";
    statusDetail.textContent = error.error || "Could not start search.";
    button.disabled = false;
    return;
  }
  await refreshStatus();
  pollTimer = setInterval(refreshStatus, 1500);
});

document.addEventListener("click", async (event) => {
  const copyButton = event.target.closest("[data-copy]");
  if (copyButton) {
    await navigator.clipboard.writeText(copyButton.dataset.copy || "");
    copyButton.textContent = "Copied";
    setTimeout(() => { copyButton.textContent = "Copy"; }, 1200);
  }
  const setupAction = event.target.closest("[data-setup-action]");
  if (setupAction) {
    const action = setupAction.dataset.setupAction;
    if (action === "profile") await openProfileEditor("name");
    if (action === "resume") await openProfileEditor("resume_path");
    if (action === "indeed_login") await openIndeedLogin();
    if (action === "results") {
      form.scrollIntoView({ behavior: "smooth", block: "center" });
      const locationInput = form.querySelector("[name=location]");
      if (locationInput) locationInput.focus();
    }
  }
      const polishButton = event.target.closest("[data-ai-polish]");
      if (polishButton) {
        await polishCommonAnswer(polishButton.dataset.aiPolish);
      }
      const questionSuggestButton = event.target.closest("[data-question-suggest]");
      if (questionSuggestButton) {
        await suggestReviewAnswer(questionSuggestButton.dataset.questionSuggest);
      }
      const savedEdit = event.target.closest("[data-saved-edit]");
      if (savedEdit) {
        editingSavedAnswerKey = savedEdit.dataset.savedEdit;
        renderSavedAnswers({ answers: savedAnswers });
      }
      const savedCancel = event.target.closest("[data-saved-cancel]");
      if (savedCancel) {
        editingSavedAnswerKey = "";
        renderSavedAnswers({ answers: savedAnswers });
      }
      const savedSave = event.target.closest("[data-saved-save]");
      if (savedSave) {
        const key = savedSave.dataset.savedSave;
        const answerField = savedAnswersList ? savedAnswersList.querySelector(`[data-saved-answer-edit="${cssEscape(key)}"]`) : null;
        const enabledField = savedAnswersList ? savedAnswersList.querySelector(`[data-saved-enabled-edit="${cssEscape(key)}"]`) : null;
        await updateSavedAnswer(key, {
          answer: answerField ? answerField.value : "",
          autofill_enabled: enabledField ? enabledField.checked : true
        });
      }
      const savedDelete = event.target.closest("[data-saved-delete]");
      if (savedDelete) {
        await deleteSavedAnswer(savedDelete.dataset.savedDelete);
      }
    });

if (savedAnswersList) {
  savedAnswersList.addEventListener("change", async (event) => {
    const toggle = event.target.closest("[data-saved-toggle]");
    if (!toggle) return;
    await updateSavedAnswer(toggle.dataset.savedToggle, { autofill_enabled: toggle.checked });
  });
}
if (refreshSavedAnswersButton) refreshSavedAnswersButton.addEventListener("click", loadSavedAnswers);

if (editProfileButton) {
  editProfileButton.addEventListener("click", () => {
    if (profileForm && profileForm.classList.contains("hidden")) {
      openProfileEditor("name");
    } else if (profileForm) {
      profileForm.classList.add("hidden");
      showProfileMessage("");
    }
  });
}
if (cancelProfileButton) {
  cancelProfileButton.addEventListener("click", () => {
    renderProfileForm(currentProfile);
    profileForm.classList.add("hidden");
    showProfileMessage("");
  });
}
if (profileForm) profileForm.addEventListener("submit", saveProfile);
if (uploadResumeButton) uploadResumeButton.addEventListener("click", uploadResume);
sectionCollapseButtons.forEach(setupCollapsibleSection);
if (commonAnswersForm) commonAnswersForm.addEventListener("submit", saveCommonAnswers);
if (useSuggestionButton) useSuggestionButton.addEventListener("click", useSuggestion);
    if (dismissSuggestionButton) {
      dismissSuggestionButton.addEventListener("click", () => {
        if (commonAnswerSuggestion) commonAnswerSuggestion.classList.add("hidden");
        showCommonAnswersMessage("Suggestion dismissed.");
      });
    }
    if (copyReviewSuggestionButton) {
      copyReviewSuggestionButton.addEventListener("click", async () => {
        await navigator.clipboard.writeText(reviewQuestionSuggestionText ? reviewQuestionSuggestionText.textContent || "" : "");
        copyReviewSuggestionButton.textContent = "Copied";
        setTimeout(() => { copyReviewSuggestionButton.textContent = "Copy suggestion"; }, 1200);
      });
    }
    if (dismissReviewSuggestionButton) {
      dismissReviewSuggestionButton.addEventListener("click", () => {
        if (reviewQuestionSuggestion) reviewQuestionSuggestion.classList.add("hidden");
      });
    }
if (setupToggle) {
  setupToggle.addEventListener("click", () => {
    localStorage.setItem(setupPreferenceKey, "open");
    if (setupStatus) renderSetup(setupStatus);
  });
}
if (setupCollapse) {
  setupCollapse.addEventListener("click", () => {
    localStorage.setItem(setupPreferenceKey, "collapsed");
    if (setupStatus) renderSetup(setupStatus);
  });
}

applicationButtons.forEach(button => {
  button.addEventListener("click", () => saveApplicationStatus(button.dataset.applicationStatus));
});
if (autofillButton) autofillButton.addEventListener("click", startAutofill);
if (resumeAutofillButton) resumeAutofillButton.addEventListener("click", resumeAutofill);
if (indeedLoginButton) indeedLoginButton.addEventListener("click", openIndeedLogin);

window.addEventListener("message", event => {
  if (!event.data) return;
  if (event.data.type === "jobfind:apply-assistant") {
    openApplyAssistant(event.data.job);
  }
  if (event.data.type === "jobfind:application-status-request") {
    broadcastApplicationStatuses();
  }
});

if (frame) frame.addEventListener("load", broadcastApplicationStatuses);

refreshStatus();
refreshAutofillStatus();
loadSetupStatus();
loadProfile();
loadCommonAnswers();
loadSavedAnswers();
loadApplications();

async function loadConfig() {
  try {
    const res = await fetch("/api/config", { cache: "no-store" });
    if (!res.ok) return;
    const cfg = await res.json();
    const loc = form && form.querySelector('[name="location"]');
    if (loc && !loc.value) loc.value = cfg.default_location || "";
    const radius = form && form.querySelector('[name="radius"]');
    if (radius && radius.options.length === 0 && Array.isArray(cfg.radius_choices)) {
      cfg.radius_choices.forEach(r => {
        const opt = document.createElement("option");
        opt.value = String(r);
        opt.textContent = `${r} miles`;
        if (String(r) === String(cfg.default_radius)) opt.selected = true;
        radius.appendChild(opt);
      });
    }
    const minScore = form && form.querySelector('[name="min_score"]');
    if (minScore && !minScore.value) minScore.value = (cfg.default_min_score ?? "");
  } catch (e) {
    console.warn("Could not load /api/config defaults:", e);
  }
}
loadConfig();
