const apiStatus = document.querySelector("#apiStatus");
const googleLoginButton = document.querySelector("#googleLoginButton");
const logoutButton = document.querySelector("#logoutButton");
const themeToggleButton = document.querySelector("#themeToggleButton");
const editForm = document.querySelector("#editForm");
const startAnalysisButton = document.querySelector("#startAnalysisButton");
const editResult = null;
const editProgress = document.querySelector("#editProgress");
const editProgressLabel = document.querySelector("#editProgressLabel");
const editProgressPercent = document.querySelector("#editProgressPercent");
const editProgressBar = document.querySelector("#editProgressBar");
const editProgressMessage = document.querySelector("#editProgressMessage");
const progressDock = document.querySelector("#progressDock");
const footerActions = document.querySelector("#footerActions");
const subtitleOffsetSeconds = document.querySelector("#subtitleOffsetSeconds");
const analysisPhase = document.querySelector("#analysisPhase");
const renderPhase = document.querySelector("#renderPhase");
const backToAnalysisButton = document.querySelector("#backToAnalysisButton");
const backToReviewButton = document.querySelector("#backToReviewButton");
const restartAnalysisButton = document.querySelector("#restartAnalysisButton");
const subtitleEditor = document.querySelector("#subtitleEditor");
const subtitleTableBody = document.querySelector("#subtitleTableBody");
const saveSubtitleButton = document.querySelector("#saveSubtitleButton");
const downloadRenderedButton = document.querySelector("#downloadRenderedButton");
const subtitleEditorMessage = document.querySelector("#subtitleEditorMessage");
const segmentReviewer = document.querySelector("#segmentReviewer");
const sourcePreview = document.querySelector("#sourcePreview");
const previewRangeLabel = document.querySelector("#previewRangeLabel");
const renderedPreviewCard = document.querySelector("#renderedPreviewCard");
const renderedPreview = document.querySelector("#renderedPreview");
const renderedRevisionLabel = document.querySelector("#renderedRevisionLabel");
const selectedSegmentCount = document.querySelector("#selectedSegmentCount");
const selectedDuration = document.querySelector("#selectedDuration");
const targetDurationFeedback = document.querySelector("#targetDurationFeedback");
const selectRecommendedButton = document.querySelector("#selectRecommendedButton");
const selectAllSegmentsButton = document.querySelector("#selectAllSegmentsButton");
const clearSegmentsButton = document.querySelector("#clearSegmentsButton");
const chapterCount = document.querySelector("#chapterCount");
const chapterList = document.querySelector("#chapterList");
const segmentList = chapterList;
const segmentFeedback = document.querySelector("#segmentFeedback");
const segmentReviewMessage = document.querySelector("#segmentReviewMessage");
const renderSelectedButton = document.querySelector("#renderSelectedButton");

let supabaseClient = null;
let currentSession = null;
let currentEditJobId = null;
let currentSegmentReview = null;
let currentPreviewEnd = null;
let editPollGeneration = 0;
let analysisFingerprint = null;
let isEditJobRunning = false;
let isProgressDocked = false;
const phaseActionGroups = {
  analysis: analysisPhase?.querySelector(".phase-actions"),
  review: segmentReviewer?.querySelector(".phase-actions"),
  render: renderPhase?.querySelector(".phase-actions"),
};

function showPhase(name, { resetProgress = false } = {}) {
  document.querySelectorAll("video").forEach((video) => video.pause());
  currentPreviewEnd = null;
  const phases = { analysis: analysisPhase, review: segmentReviewer, render: renderPhase };
  Object.entries(phases).forEach(([phase, element]) => {
    if (element) element.hidden = phase !== name;
  });
  const actions = phaseActionGroups[name];
  if (footerActions && actions) footerActions.replaceChildren(actions);
  if (resetProgress) {
    renderEditProgress({ progress: 0, status: "idle", phase: name, message: "작업을 준비하는 중입니다." });
    if (editProgressLabel) editProgressLabel.textContent = "AI 편집 진행률";
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
  requestAnimationFrame(updateProgressDock);
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const dark = theme === "dark";
  themeToggleButton?.setAttribute("aria-label", dark ? "라이트 모드로 전환" : "다크 모드로 전환");
  themeToggleButton?.setAttribute("title", dark ? "라이트 모드로 전환" : "다크 모드로 전환");
  if (themeToggleButton) themeToggleButton.firstElementChild.textContent = dark ? "☀️" : "🌙";
}

try {
  applyTheme(localStorage.getItem("theme") || "light");
} catch {
  applyTheme("light");
}

themeToggleButton?.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("theme", next); } catch { /* Storage may be blocked by browser privacy settings. */ }
});

function analysisSettingsFingerprint() {
  const form = new FormData(editForm);
  return JSON.stringify({
    vod_url: form.get("vod_url"), genre: form.get("genre"), llm_provider: form.get("llm_provider"),
    chat_delay_seconds: form.get("chat_delay_seconds"),
    clean_subtitles: form.get("clean_subtitles") === "on",
    target_duration_seconds: form.get("target_duration_seconds"),
    subtitle_offset_seconds: form.get("subtitle_offset_seconds"),
    subtitle_font_name: form.get("subtitle_font_name"),
    subtitle_font_size: form.get("subtitle_font_size"), render_mode: form.get("render_mode"),
  });
}

function invalidatePriorAnalysis() {
  if (!analysisFingerprint || analysisSettingsFingerprint() === analysisFingerprint) return;
  currentEditJobId = null;
  currentSegmentReview = null;
  subtitleEditor.hidden = true;
  renderedPreviewCard.hidden = true;
  setMessage(editResult, "분석 설정이 변경되었습니다. 다음 단계로 진행하려면 AI 분석을 다시 실행하세요.", "warning");
}

function bindRangeNumber(rangeId, numberId) {
  const range = document.querySelector(rangeId);
  const number = document.querySelector(numberId);
  if (!range || !number) return;
  const sync = (source, target) => { target.value = source.value; invalidatePriorAnalysis(); };
  range.addEventListener("input", () => sync(range, number));
  number.addEventListener("input", () => sync(number, range));
}

bindRangeNumber("#chatDelaySecondsRange", "#chatDelaySeconds");
bindRangeNumber("#subtitleOffsetSecondsRange", "#subtitleOffsetSeconds");
bindRangeNumber("#subtitleFontSizeRange", "#subtitleFontSize");
bindRangeNumber("#targetDurationSecondsRange", "#targetDurationSeconds");
editForm?.addEventListener("change", invalidatePriorAnalysis);

startAnalysisButton?.addEventListener("click", () => {
  if (!editForm?.reportValidity()) return;
  startAnalysis();
});
if (startAnalysisButton) startAnalysisButton.dataset.analysisBound = "true";

function resizeSegmentFeedback() {
  if (!segmentFeedback) return;
  segmentFeedback.style.height = "auto";
  segmentFeedback.style.height = `${segmentFeedback.scrollHeight}px`;
}

segmentFeedback?.addEventListener("input", resizeSegmentFeedback);
resizeSegmentFeedback();

subtitleOffsetSeconds?.addEventListener("input", () => { subtitleOffsetSeconds.dataset.manual = "true"; });

function setStatus(text, state = "idle") {
  apiStatus.textContent = text;
  apiStatus.dataset.state = state;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setMessage(target, text, type = "success") {
  const logger = type === "error" ? console.error : type === "warning" ? console.warn : console.info;
  logger(`[AI 영상 편집] ${text}`);
}

function renderJson(target, value) {
  console.info("[AI 영상 편집 결과]", value);
}

function formatSeconds(value) {
  const total = Math.max(0, Math.round(Number(value) || 0));
  const hours = String(Math.floor(total / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

async function loadConfig() {
  const response = await fetch("/api/config");
  const config = await response.json();
  if (!response.ok) throw new Error(config.detail || "Supabase 설정이 없습니다.");
  supabaseClient = window.supabase.createClient(config.supabase_url, config.supabase_anon_key);
  supabaseClient.auth.onAuthStateChange((_event, session) => updateAuthState(session));
  const { data } = await supabaseClient.auth.getSession();
  updateAuthState(data.session);
}

function updateAuthState(session) {
  currentSession = session;
  const loggedIn = Boolean(session?.user);
  googleLoginButton.hidden = loggedIn;
  logoutButton.hidden = !loggedIn;
  logoutButton.disabled = isEditJobRunning;
  setStatus(loggedIn ? `${session.user.email} 로그인됨` : "로그인 필요", loggedIn ? "success" : "idle");
}

googleLoginButton.addEventListener("click", async () => {
  if (!supabaseClient) return;
  const { error } = await supabaseClient.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo: window.location.origin },
  });
  if (error) setMessage(editResult, error.message, "error");
});

function renderEditProgress(data) {
  if (!editProgress) return;
  const progress = Math.max(0, Math.min(100, Number(data.progress) || 0));
  if (editProgressLabel) {
    editProgressLabel.textContent = data.phase === "render" ? "선택 구간 렌더링" : "AI 분석 진행률";
  }
  editProgressPercent.textContent = `${progress}%`;
  editProgressBar.style.setProperty("--progress", `${progress}%`);
  editProgressBar.setAttribute("aria-valuenow", String(progress));
  editProgressMessage.textContent = data.message || "처리 중입니다.";
  editProgress.dataset.state = data.status || "running";
}

function updateProgressDock() {
  if (!editProgress || !progressDock) return;
  const remaining = document.documentElement.scrollHeight - (window.scrollY + window.innerHeight);
  if (isProgressDocked ? remaining > 28 : remaining <= 2) {
    isProgressDocked = !isProgressDocked;
    editProgress.classList.toggle("docked", isProgressDocked);
  }
}

function setEditJobRunning(running) {
  isEditJobRunning = running;
  logoutButton.disabled = running;
}

function setAnalysisFormBusy(busy) {
  editForm?.querySelectorAll("input, select, textarea").forEach((element) => {
    element.disabled = busy;
  });
  startAnalysisButton.disabled = busy;
}

window.addEventListener("scroll", updateProgressDock, { passive: true });
window.addEventListener("resize", updateProgressDock);
updateProgressDock();

async function pollEditJob(jobId, generation = editPollGeneration) {
  while (true) {
    const response = await fetch(`/api/youtube/edit/status/${encodeURIComponent(jobId)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "AI 편집 상태를 확인할 수 없습니다.");
    if (generation !== editPollGeneration) throw new Error("새 작업이 시작되어 이전 상태 확인을 중단했습니다.");
    renderEditProgress(data);
    if (data.status === "completed" || data.status === "awaiting_selection") {
      return { ...(data.result || {}), job_status: data.status };
    }
    if (data.status === "failed") throw new Error(data.error || data.message || "AI 영상 편집에 실패했습니다.");
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

function selectedSegmentIds() {
  return [...new Set(
    [...segmentList.querySelectorAll(".segment-checkbox:checked")]
      .map((input) => input.value),
  )];
}

function unionDuration(items) {
  const intervals = items
    .map((item) => [Number(item.start), Number(item.end)])
    .filter(([start, end]) => Number.isFinite(start) && Number.isFinite(end) && end > start)
    .sort((a, b) => a[0] - b[0]);
  let total = 0;
  let current = null;
  intervals.forEach(([start, end]) => {
    if (!current || start > current[1]) {
      if (current) total += current[1] - current[0];
      current = [start, end];
    } else {
      current[1] = Math.max(current[1], end);
    }
  });
  if (current) total += current[1] - current[0];
  return total;
}

function selectedUnionDuration() {
  if (!currentSegmentReview) return 0;
  const selected = new Set(selectedSegmentIds());
  return unionDuration(
    currentSegmentReview.segments.filter((item) => selected.has(item.segment_id)),
  );
}

function updateSelectionSummary({ markDirty = true } = {}) {
  if (!currentSegmentReview) return;
  const ids = selectedSegmentIds();
  const duration = selectedUnionDuration();
  selectedSegmentCount.textContent = String(ids.length);
  selectedDuration.textContent = formatSeconds(duration);
  const target = Number(currentSegmentReview.target_seconds) || 0;
  const delta = Math.round(duration - target);
  targetDurationFeedback.textContent = target
    ? delta === 0
      ? "목표 길이와 같습니다."
      : delta > 0
        ? `목표보다 약 ${formatSeconds(delta)} 깁니다.`
        : `목표보다 약 ${formatSeconds(Math.abs(delta))} 짧습니다.`
    : "";
  renderSelectedButton.disabled = ids.length === 0;
  segmentList.querySelectorAll(".segment-card").forEach((card) => {
    const input = card.querySelector(".segment-checkbox");
    card.dataset.selected = String(Boolean(input?.checked));
  });
  syncChapterSelection();
  if (markDirty) segmentReviewMessage.textContent = "선택 변경사항은 아직 결과 영상에 반영되지 않았습니다.";
}

function renderSegmentCard(item, recommended) {
    const id = escapeHtml(item.segment_id);
    const chapterId = escapeHtml(item.chapter_id || "");
    const duration = Math.max(0, Number(item.end) - Number(item.start));
    const score = Math.round(Number(item.final_score) || 0);
    const chatDensity = Number(item.chat_density) || 0;
    const recommendedBadge = recommended.has(item.segment_id)
      ? '<span class="recommended-pill">AI 추천</span>'
      : "";
    return `
      <article class="segment-card" data-selected="${Boolean(item.selected)}">
        <input class="segment-checkbox" type="checkbox" value="${id}" data-chapter-id="${chapterId}" aria-label="${formatSeconds(item.start)} 구간 선택" ${item.selected ? "checked" : ""}>
        <span class="segment-copy">
          <span class="segment-meta">
            <span class="segment-time">${formatSeconds(item.start)}–${formatSeconds(item.end)}</span>
            <span class="muted">${formatSeconds(duration)}</span>
            <span class="score-pill">LLM ${score}</span>
            <span class="chat-density-pill">채팅 ${chatDensity.toFixed(1)}/분</span>
            ${recommendedBadge}
          </span>
          <span class="segment-text">${escapeHtml(item.text || "자막 없음")}</span>
        </span>
        <button type="button" class="preview-segment-button" data-preview-id="${id}">미리보기</button>
      </article>`;
}

function syncChapterSelection() {
  if (!currentSegmentReview) return;
  chapterList.querySelectorAll(".chapter-card").forEach((card) => {
    const chapterId = card.dataset.chapterId;
    const chapter = currentSegmentReview.chapters.find((item) => item.chapter_id === chapterId);
    const ids = new Set(chapter?.segment_ids || []);
    const inputs = [...segmentList.querySelectorAll(".segment-checkbox")]
      .filter((input) => ids.has(input.value));
    const selectedCount = inputs.filter((input) => input.checked).length;
    const selectedIds = new Set(
      inputs.filter((input) => input.checked).map((input) => input.value),
    );
    const selectedChapterDuration = unionDuration(
      currentSegmentReview.segments.filter((item) => selectedIds.has(item.segment_id)),
    );
    const checkbox = card.querySelector(".chapter-checkbox");
    checkbox.checked = inputs.length > 0 && selectedCount === inputs.length;
    checkbox.indeterminate = selectedCount > 0 && selectedCount < inputs.length;
    card.dataset.selection = selectedCount === 0
      ? "none"
      : selectedCount === inputs.length
        ? "all"
        : "partial";
    card.closest(".chapter-block")?.setAttribute("data-selection", card.dataset.selection);
    const metric = card.querySelector("[data-chapter-selected]");
    if (metric) {
      metric.textContent = `${selectedCount}/${inputs.length}개 선택 · ${formatSeconds(selectedChapterDuration)}`;
    }
  });
}

function renderSegmentList(review) {
  const recommended = new Set(review.recommended_segment_ids || []);
  const byId = new Map(review.segments.map((item) => [item.segment_id, item]));
  let chapters = Array.isArray(review.chapters) ? review.chapters : [];
  if (!chapters.length) {
    chapters = [{
      chapter_id: "chapter-all",
      title: "전체 후보",
      summary: "AI가 평가한 전체 후보 구간입니다.",
      start: review.segments[0]?.start || 0,
      end: review.segments.at(-1)?.end || 0,
      segment_ids: review.segments.map((item) => item.segment_id),
    }];
  }
  currentSegmentReview.chapters = chapters;
  chapterCount.textContent = String(chapters.length);
  chapterList.innerHTML = chapters.map((chapter, chapterIndex) => {
    const chapterId = escapeHtml(chapter.chapter_id);
    const items = chapter.segment_ids.map((id) => byId.get(id)).filter(Boolean);
    const rawDuration = unionDuration(items);
    const recommendationCount = items.filter((item) => recommended.has(item.segment_id)).length;
    return `
      <section class="chapter-block" data-open="false">
        <article class="chapter-card" data-chapter-id="${chapterId}" data-selection="none">
          <input class="chapter-checkbox" type="checkbox" aria-label="${escapeHtml(chapter.title || "챕터")} 전체 선택">
          <span class="chapter-copy">
            <span class="chapter-title-row">
              <strong>${escapeHtml(chapter.title || "제목 없는 챕터")}</strong>
              ${recommendationCount ? `<span class="recommended-pill">추천 ${recommendationCount}개</span>` : ""}
            </span>
            <span class="chapter-summary">${escapeHtml(chapter.summary || "요약 없음")}</span>
            <span class="chapter-meta">
              ${formatSeconds(chapter.start)}–${formatSeconds(chapter.end)} · 후보 ${items.length}개 · 약 ${formatSeconds(rawDuration)}
              <strong data-chapter-selected>0/${items.length}개 선택</strong>
            </span>
          </span>
          <button type="button" class="ghost-button chapter-detail-button" data-chapter-toggle="${chapterId}" aria-expanded="false" aria-controls="chapter-panel-${chapterIndex}">세부 보기</button>
        </article>
        <section id="chapter-panel-${chapterIndex}" class="chapter-detail" data-chapter-detail="${chapterId}" hidden>
          ${items.map((item) => renderSegmentCard(item, recommended)).join("")}
        </section>
      </section>`;
  }).join("");
  syncChapterSelection();
}

function showRenderedPreview(review, force = false) {
  const hasRender = force || Number(review.revision) > 0;
  renderedPreviewCard.hidden = !hasRender;
  if (!hasRender) {
    renderedPreview.removeAttribute("src");
    if (downloadRenderedButton) downloadRenderedButton.hidden = true;
    return;
  }
  renderedRevisionLabel.textContent = `수정 ${review.revision || 0}회`;
  renderedPreview.src = `${review.rendered_video_url}?revision=${encodeURIComponent(review.revision || Date.now())}`;
  if (downloadRenderedButton) {
    downloadRenderedButton.href = renderedPreview.src;
    downloadRenderedButton.hidden = false;
  }
  renderedPreview.load();
}

async function loadSegmentReviewer(result) {
  if (!result?.job_id) return;
  const response = await fetch(`/api/youtube/edit/${encodeURIComponent(result.job_id)}/segments`);
  const review = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(review.detail || "AI 후보 구간을 불러오지 못했습니다.");
  currentEditJobId = result.job_id;
  currentSegmentReview = review;
  showPhase("review");
  sourcePreview.src = review.source_video_url;
  sourcePreview.load();
  renderSegmentList(review);
  updateSelectionSummary({ markDirty: false });
  segmentReviewMessage.textContent = result.job_status === "awaiting_selection"
    ? "AI 분석이 끝났습니다. 원하는 구간을 선택한 뒤 영상을 생성하세요."
    : "현재 결과를 확인하고 구간을 다시 선택할 수 있습니다.";
  showRenderedPreview(review, Boolean(result.rendered_video_path));
}

function setSegmentReviewerBusy(busy) {
  chapterList.querySelectorAll("input, button").forEach((element) => {
    element.disabled = busy && !element.matches(".chapter-detail-button, .preview-segment-button");
  });
  selectRecommendedButton.disabled = busy;
  selectAllSegmentsButton.disabled = busy;
  clearSegmentsButton.disabled = busy;
  segmentFeedback.disabled = busy;
  renderSelectedButton.disabled = busy || selectedSegmentIds().length === 0;
  backToAnalysisButton.disabled = busy;
  backToReviewButton.disabled = busy;
  restartAnalysisButton.disabled = busy;
}

segmentList?.addEventListener("change", (event) => {
  if (event.target.matches(".segment-checkbox")) updateSelectionSummary();
});

chapterList?.addEventListener("change", (event) => {
  if (!event.target.matches(".chapter-checkbox") || !currentSegmentReview) return;
  const card = event.target.closest(".chapter-card");
  const chapter = currentSegmentReview.chapters.find(
    (item) => item.chapter_id === card?.dataset.chapterId,
  );
  const ids = new Set(chapter?.segment_ids || []);
  segmentList.querySelectorAll(".segment-checkbox").forEach((input) => {
    if (ids.has(input.value)) input.checked = event.target.checked;
  });
  updateSelectionSummary();
});

chapterList?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-chapter-toggle]");
  if (!button) return;
  const chapterId = button.dataset.chapterToggle;
  const target = [...segmentList.querySelectorAll("[data-chapter-detail]")]
    .find((detail) => detail.dataset.chapterDetail === chapterId);
  if (!target) return;
  const shouldOpen = target.hidden;
  segmentList.querySelectorAll("[data-chapter-detail]").forEach((detail) => { detail.hidden = true; });
  chapterList.querySelectorAll(".chapter-block").forEach((block) => { block.dataset.open = "false"; });
  chapterList.querySelectorAll("[data-chapter-toggle]").forEach((toggle) => {
    toggle.setAttribute("aria-expanded", "false");
    toggle.textContent = "세부 보기";
  });
  target.hidden = !shouldOpen;
  target.closest(".chapter-block").dataset.open = String(shouldOpen);
  button.setAttribute("aria-expanded", String(shouldOpen));
  button.textContent = shouldOpen ? "세부 닫기" : "세부 보기";
  if (shouldOpen) target.scrollIntoView({ behavior: "smooth", block: "nearest" });
});

segmentList?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-preview-id]");
  if (!button || !currentSegmentReview) return;
  event.preventDefault();
  event.stopPropagation();
  const item = currentSegmentReview.segments.find(
    (segment) => segment.segment_id === button.dataset.previewId,
  );
  if (!item) return;
  const start = Math.max(0, Number(item.start) || 0);
  currentPreviewEnd = Math.max(start, Number(item.end) || start);
  previewRangeLabel.textContent = `${formatSeconds(start)}–${formatSeconds(currentPreviewEnd)}`;
  const playRange = () => {
    sourcePreview.currentTime = start;
    sourcePreview.play().catch(() => {});
  };
  if (sourcePreview.readyState >= 1) playRange();
  else sourcePreview.addEventListener("loadedmetadata", playRange, { once: true });
});

sourcePreview?.addEventListener("timeupdate", () => {
  if (currentPreviewEnd !== null && sourcePreview.currentTime >= currentPreviewEnd) {
    sourcePreview.pause();
    currentPreviewEnd = null;
  }
});

selectRecommendedButton?.addEventListener("click", () => {
  if (!currentSegmentReview) return;
  const recommended = new Set(currentSegmentReview.recommended_segment_ids || []);
  segmentList.querySelectorAll(".segment-checkbox").forEach((input) => {
    input.checked = recommended.has(input.value);
  });
  updateSelectionSummary();
});

selectAllSegmentsButton?.addEventListener("click", () => {
  segmentList.querySelectorAll(".segment-checkbox").forEach((input) => { input.checked = true; });
  updateSelectionSummary();
});

clearSegmentsButton?.addEventListener("click", () => {
  segmentList.querySelectorAll(".segment-checkbox").forEach((input) => { input.checked = false; });
  updateSelectionSummary();
});

backToAnalysisButton?.addEventListener("click", () => {
  if (!isEditJobRunning) showPhase("analysis", { resetProgress: true });
});
backToReviewButton?.addEventListener("click", () => {
  if (isEditJobRunning) return;
  if (currentSegmentReview) showPhase("review", { resetProgress: true });
  else showPhase("analysis", { resetProgress: true });
});
restartAnalysisButton?.addEventListener("click", () => {
  if (!isEditJobRunning) showPhase("analysis", { resetProgress: true });
});

renderSelectedButton?.addEventListener("click", async () => {
  if (!currentEditJobId) return;
  const segmentIds = selectedSegmentIds();
  if (!segmentIds.length) {
    segmentReviewMessage.textContent = "한 개 이상의 구간을 선택하세요.";
    return;
  }
  setSegmentReviewerBusy(true);
  setEditJobRunning(true);
  segmentReviewMessage.textContent = "선택한 구간으로 영상을 생성하는 중입니다.";
  renderEditProgress({ progress: 0, status: "queued", phase: "render", message: "렌더링을 준비하는 중입니다." });
  try {
    const response = await fetch(`/api/youtube/edit/${encodeURIComponent(currentEditJobId)}/segments`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        segment_ids: segmentIds,
        feedback: segmentFeedback.value.trim() || null,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "선택 구간 렌더링을 시작하지 못했습니다.");
    renderEditProgress(data);
    const generation = ++editPollGeneration;
    const result = await pollEditJob(currentEditJobId, generation);
    renderJson(editResult, result);
    await loadSegmentReviewer(result);
    await loadSubtitleEditor(result);
    showPhase("render");
    segmentReviewMessage.textContent = result.message || "선택한 구간으로 영상을 생성했습니다.";
  } catch (error) {
    segmentReviewMessage.textContent = error.message;
  } finally {
    setEditJobRunning(false);
    setSegmentReviewerBusy(false);
  }
});

async function loadSubtitleEditor(result) {
  if (!subtitleEditor || !result?.job_id || !result.generated_subtitles_path) return;
  const response = await fetch(`/api/youtube/edit/${encodeURIComponent(result.job_id)}/subtitles`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "생성된 자막을 불러오지 못했습니다.");
  currentEditJobId = result.job_id;
  renderSubtitleRows(parseSrt(data.content || ""));
  subtitleEditor.hidden = false;
  subtitleEditorMessage.textContent = "자막을 수정한 뒤 저장할 수 있습니다.";
}

function parseSrt(content) {
  return String(content).replaceAll("\r\n", "\n").trim().split(/\n{2,}/).map((block, index) => {
    const lines = block.split("\n");
    const timeIndex = lines.findIndex((line) => line.includes("-->"));
    if (timeIndex < 0) return null;
    const [start = "", end = ""] = lines[timeIndex].split("-->").map((value) => value.trim());
    return { id: lines.slice(0, timeIndex).join(" ").trim() || String(index + 1), start, end, text: lines.slice(timeIndex + 1).join("\n") };
  }).filter(Boolean);
}

function renderSubtitleRows(rows) {
  if (!subtitleTableBody) return;
  subtitleTableBody.innerHTML = rows.map((row, index) => `
    <tr data-subtitle-id="${escapeHtml(row.id)}">
      <td>${escapeHtml(row.id || String(index + 1))}</td>
      <td><span class="subtitle-time-value">${escapeHtml(row.start)}</span></td>
      <td><span class="subtitle-time-value">${escapeHtml(row.end)}</span></td>
      <td><textarea class="subtitle-content-input" rows="1" aria-label="${index + 1}번 자막 내용">${escapeHtml(row.text)}</textarea></td>
    </tr>`).join("");
  subtitleTableBody.querySelectorAll(".subtitle-content-input").forEach((input) => {
    const resize = () => {
      input.style.height = "auto";
      input.style.height = `${input.scrollHeight}px`;
    };
    input.addEventListener("input", resize);
    resize();
  });
}

function subtitleTableToSrt() {
  if (!subtitleTableBody) return "";
  return [...subtitleTableBody.querySelectorAll("tr")].map((row, index) => {
    const id = row.dataset.subtitleId || String(index + 1);
    const [start, end] = [...row.querySelectorAll(".subtitle-time-value")].map((value) => value.textContent.trim());
    const text = row.querySelector(".subtitle-content-input")?.value.trim() || "";
    return `${id}\n${start} --> ${end}\n${text}`;
  }).join("\n\n");
}

saveSubtitleButton?.addEventListener("click", async () => {
  if (!currentEditJobId) return;
  saveSubtitleButton.disabled = true;
  subtitleEditorMessage.textContent = "자막을 저장하고 영상을 다시 생성하는 중입니다.";
  try {
    const response = await fetch(`/api/youtube/edit/${encodeURIComponent(currentEditJobId)}/subtitles`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: subtitleTableToSrt() }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "자막 저장에 실패했습니다.");
    subtitleEditorMessage.textContent = `${data.message} 결과: ${data.rendered_video_path}`;
    if (currentSegmentReview && data.revision) {
      currentSegmentReview.revision = data.revision;
      showRenderedPreview(currentSegmentReview, true);
    }
  } catch (error) {
    subtitleEditorMessage.textContent = error.message;
  } finally {
    saveSubtitleButton.disabled = false;
  }
});

async function startAnalysis(event) {
  event?.preventDefault();
  const form = new FormData(editForm);
  const requestPayload = {
    vod_url: form.get("vod_url"),
    llm_provider: form.get("llm_provider") || "gemini",
    genre: form.get("genre") || "ai_news",
    target_duration_seconds: Number(form.get("target_duration_seconds") || 600),
    chat_delay_seconds: Number(form.get("chat_delay_seconds") || 0),
    clean_subtitles: form.get("clean_subtitles") === "on",
    subtitle_offset_seconds: Number(form.get("subtitle_offset_seconds") || 0),
    subtitle_font_name: form.get("subtitle_font_name") || "Malgun Gothic",
    subtitle_font_size: Number(form.get("subtitle_font_size") || 18),
    render_mode: form.get("render_mode") || "preview",
    interactive_selection: true,
  };
  setAnalysisFormBusy(true);
  setEditJobRunning(true);
  const generation = ++editPollGeneration;
  const submittedFingerprint = analysisSettingsFingerprint();
  segmentReviewer.hidden = true;
  renderPhase.hidden = true;
  subtitleEditor.hidden = true;
  currentSegmentReview = null;
  currentEditJobId = null;
  setMessage(editResult, "자막 정제, 요약, 채팅 점수 계산 후 선택 가능한 후보를 만들고 있습니다.");
  try {
    const response = await fetch("/api/youtube/edit/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestPayload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => `${item.loc?.at(-1) || "입력값"}: ${item.msg}`).join(", ")
        : data.detail;
      throw new Error(detail || "AI 영상 편집에 실패했습니다.");
    }
    analysisFingerprint = submittedFingerprint;
    renderEditProgress(data);
    const result = await pollEditJob(data.job_id, generation);
    renderJson(editResult, result);
    await loadSegmentReviewer(result);
    if (result.job_status === "completed") await loadSubtitleEditor(result);
  } catch (error) {
    setMessage(editResult, error.message, "error");
    renderEditProgress({ progress: 0, status: "failed", phase: "analysis", message: error.message });
    showPhase("analysis");
  } finally {
    setEditJobRunning(false);
    setAnalysisFormBusy(false);
  }
}


logoutButton.addEventListener("click", async () => {
  await supabaseClient.auth.signOut();
  updateAuthState(null);
});

editForm?.addEventListener("submit", startAnalysis);

showPhase("analysis");

loadConfig().catch((error) => {
  setStatus("Supabase 설정 필요", "error");
  setMessage(editResult, error.message, "error");
});
