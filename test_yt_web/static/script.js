const apiStatus = document.querySelector("#apiStatus");
const googleLoginButton = document.querySelector("#googleLoginButton");
const logoutButton = document.querySelector("#logoutButton");
const liveForm = document.querySelector("#liveForm");
const liveResult = document.querySelector("#liveResult");
const liveChatResult = document.querySelector("#liveChatResult");
const finalizeForm = document.querySelector("#finalizeForm");
const finalizeResult = document.querySelector("#finalizeResult");
const editForm = document.querySelector("#editForm");
const editResult = document.querySelector("#editResult");
const editProgress = document.querySelector("#editProgress");
const editProgressPercent = document.querySelector("#editProgressPercent");
const editProgressBar = document.querySelector("#editProgressBar");
const editProgressMessage = document.querySelector("#editProgressMessage");
const editDelaySeconds = document.querySelector("#editDelaySeconds");
const subtitleOffsetSeconds = document.querySelector("#subtitleOffsetSeconds");
const subtitleEditor = document.querySelector("#subtitleEditor");
const subtitleText = document.querySelector("#subtitleText");
const saveSubtitleButton = document.querySelector("#saveSubtitleButton");
const subtitleEditorMessage = document.querySelector("#subtitleEditorMessage");

let supabaseClient = null;
let currentSession = null;
let liveChatTimer = null;
let liveSession = null;
let currentEditJobId = null;

function syncEditDelayFromFinalize() {
  const value = document.querySelector("#delaySeconds")?.value || "0";
  if (editDelaySeconds && !editDelaySeconds.dataset.manual) editDelaySeconds.value = value;
}

document.querySelector("#delaySeconds")?.addEventListener("input", syncEditDelayFromFinalize);
editDelaySeconds?.addEventListener("input", () => { editDelaySeconds.dataset.manual = "true"; });
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
  target.innerHTML = `<p class="message ${type}">${escapeHtml(text)}</p>`;
}

function renderJson(target, value) {
  target.innerHTML = `<pre class="json-result">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
}

function renderChatSummary(data) {
  const windows = (data.highlight_windows || []).slice(0, 8);
  const windowHtml = windows.length
    ? windows.map((item) => `<li>${formatSeconds(item.start_seconds)} ~ ${formatSeconds(item.end_seconds)} · ${item.message_count}개 · 점수 ${item.burst_score}</li>`).join("")
    : "<li>현재까지 뚜렷한 채팅 급증 구간이 없습니다.</li>";
  liveChatResult.innerHTML = `
    <div class="chat-metrics">
      <strong>누적 채팅 ${escapeHtml(data.total_messages || 0)}개</strong>
      <span>최근 응답 ${escapeHtml((data.messages || []).length)}개</span>
    </div>
    <p class="summary-label">하이라이트 후보</p>
    <ul class="highlight-list">${windowHtml}</ul>`;
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
  setStatus(loggedIn ? `${session.user.email} 로그인됨` : "로그인 필요", loggedIn ? "success" : "idle");
}

googleLoginButton.addEventListener("click", async () => {
  if (!supabaseClient) return;
  const { error } = await supabaseClient.auth.signInWithOAuth({
    provider: "google",
    options: {
      redirectTo: window.location.origin,
      scopes: "https://www.googleapis.com/auth/youtube.readonly",
    },
  });
  if (error) setMessage(liveResult, error.message, "error");
});

async function pollLiveChat() {
  if (!liveSession?.live_chat_id || !currentSession?.provider_token) return;
  const query = new URLSearchParams({ live_chat_id: liveSession.live_chat_id });
  if (liveSession.next_page_token) query.set("page_token", liveSession.next_page_token);
  if (liveSession.actual_start_time) query.set("actual_start_time", liveSession.actual_start_time);
  query.set("delay_seconds", String(liveSession.delay_seconds || 0));

  const response = await fetch(`/api/youtube/live/chat?${query}`, {
    headers: { "X-YouTube-Access-Token": currentSession.provider_token },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    setMessage(liveChatResult, data.detail || "라이브 채팅을 가져오지 못했습니다.", "error");
    return;
  }
  liveSession.next_page_token = data.next_page_token;
  renderChatSummary(data);
  if (data.offline_at) {
    setMessage(liveResult, "방송이 종료되었습니다. 다시보기 URL을 입력해 분석을 결합하세요.", "success");
    return;
  }
  liveChatTimer = setTimeout(pollLiveChat, Math.max(Number(data.polling_interval_millis || 5000), 1000));
}

liveForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (liveChatTimer) clearTimeout(liveChatTimer);
  if (!currentSession?.provider_token) {
    setMessage(liveResult, "Google 로그인 후 YouTube 읽기 권한을 허용하세요.", "error");
    return;
  }

  const submitButton = liveForm.querySelector("button");
  submitButton.disabled = true;
  setMessage(liveResult, "라이브 세션을 확인하는 중입니다.");
  try {
    const response = await fetch("/api/youtube/live/inspect", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-YouTube-Access-Token": currentSession.provider_token,
      },
      body: JSON.stringify({ url: new FormData(liveForm).get("url") }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "라이브 세션 확인에 실패했습니다.");

    liveSession = {
      ...data.session,
      ...data.broadcast,
      next_page_token: null,
      delay_seconds: Number(document.querySelector("#delaySeconds")?.value || 0),
    };
    renderJson(liveResult, {
      title: data.broadcast?.title,
      status: data.broadcast?.life_cycle_status,
      actual_start_time: data.broadcast?.actual_start_time,
      live_chat_id: data.broadcast?.live_chat_id,
      warning: data.session?.warning,
    });
    if (!liveSession.live_chat_id) {
      setMessage(liveChatResult, data.session?.warning || "현재 사용할 수 있는 라이브 채팅이 없습니다.", "warning");
    } else {
      await pollLiveChat();
    }
  } catch (error) {
    setMessage(liveResult, error.message, "error");
  } finally {
    submitButton.disabled = false;
  }
});

finalizeForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentSession?.provider_token) {
    setMessage(finalizeResult, "다시보기 메타데이터 확인을 위해 Google 로그인이 필요합니다.", "error");
    return;
  }
  const submitButton = finalizeForm.querySelector("button");
  submitButton.disabled = true;
  setMessage(finalizeResult, "다시보기와 채팅 시간축을 결합하는 중입니다.");
  try {
    const form = new FormData(finalizeForm);
    const response = await fetch("/api/youtube/live/finalize", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-YouTube-Access-Token": currentSession.provider_token,
      },
      body: JSON.stringify({
        live_chat_id: liveSession?.live_chat_id || null,
        vod_url: form.get("vod_url"),
        genre: form.get("genre") || "ai_news",
        actual_start_time: liveSession?.actual_start_time || null,
        bucket_seconds: Number(form.get("bucket_seconds") || 30),
        delay_seconds: Number(form.get("delay_seconds") || 0),
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "다시보기 결합에 실패했습니다.");
    renderJson(finalizeResult, data);
  } catch (error) {
    setMessage(finalizeResult, error.message, "error");
  } finally {
    submitButton.disabled = false;
  }
});

function renderEditProgress(data) {
  if (!editProgress) return;
  const progress = Math.max(0, Math.min(100, Number(data.progress) || 0));
  editProgress.hidden = false;
  editProgressPercent.textContent = `${progress}%`;
  editProgressBar.style.setProperty("--progress", `${progress}%`);
  editProgressBar.setAttribute("aria-valuenow", String(progress));
  editProgressMessage.textContent = data.message || "처리 중입니다.";
  editProgress.dataset.state = data.status || "running";
}

async function pollEditJob(jobId) {
  while (true) {
    const response = await fetch(`/api/youtube/live/edit/status/${encodeURIComponent(jobId)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "AI 편집 상태를 확인할 수 없습니다.");
    renderEditProgress(data);
    if (data.status === "completed") return data.result;
    if (data.status === "failed") throw new Error(data.error || data.message || "AI 영상 편집에 실패했습니다.");
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

async function loadSubtitleEditor(result) {
  if (!subtitleEditor || !result?.job_id || !result.generated_subtitles_path) return;
  const response = await fetch(`/api/youtube/live/edit/${encodeURIComponent(result.job_id)}/subtitles`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "생성된 자막을 불러오지 못했습니다.");
  currentEditJobId = result.job_id;
  subtitleText.value = data.content || "";
  subtitleEditor.hidden = false;
  subtitleEditorMessage.textContent = "자막을 수정한 뒤 저장할 수 있습니다.";
}

saveSubtitleButton?.addEventListener("click", async () => {
  if (!currentEditJobId) return;
  saveSubtitleButton.disabled = true;
  subtitleEditorMessage.textContent = "자막을 저장하고 영상을 다시 생성하는 중입니다.";
  try {
    const response = await fetch(`/api/youtube/live/edit/${encodeURIComponent(currentEditJobId)}/subtitles`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: subtitleText.value }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "자막 저장에 실패했습니다.");
    subtitleEditorMessage.textContent = `${data.message} 결과: ${data.rendered_video_path}`;
  } catch (error) {
    subtitleEditorMessage.textContent = error.message;
  } finally {
    saveSubtitleButton.disabled = false;
  }
});

editForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentSession?.provider_token) {
    setMessage(editResult, "AI 편집을 실행하려면 Google 로그인이 필요합니다.", "error");
    return;
  }
  const submitButton = editForm.querySelector("button");
  submitButton.disabled = true;
  setMessage(editResult, "자막 정제, 요약, 채팅 점수 계산 및 영상 렌더링 중입니다. 영상 길이에 따라 시간이 걸릴 수 있습니다.");
  try {
    const form = new FormData(editForm);
    const response = await fetch("/api/youtube/live/edit/start", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-YouTube-Access-Token": currentSession.provider_token,
      },
      body: JSON.stringify({
        live_chat_id: liveSession?.live_chat_id || null,
        vod_url: form.get("vod_url"),
        actual_start_time: liveSession?.actual_start_time || null,
        target_duration_seconds: Number(form.get("target_duration_seconds") || 600),
        bucket_seconds: Number(document.querySelector("#bucketSeconds")?.value || 30),
        delay_seconds: Number(form.get("delay_seconds") || 0),
        subtitle_offset_seconds: Number(form.get("subtitle_offset_seconds") || 0),
        subtitle_font_name: form.get("subtitle_font_name") || "Malgun Gothic",
        subtitle_font_size: Number(form.get("subtitle_font_size") || 18),
        render_mode: form.get("render_mode") || "preview",
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "AI 영상 편집에 실패했습니다.");
    renderEditProgress(data);
    const result = await pollEditJob(data.job_id);
    renderJson(editResult, result);
    await loadSubtitleEditor(result);
  } catch (error) {
    setMessage(editResult, error.message, "error");
  } finally {
    submitButton.disabled = false;
  }
});


logoutButton.addEventListener("click", async () => {
  await supabaseClient.auth.signOut();
  updateAuthState(null);
});

loadConfig().catch((error) => {
  setStatus("Supabase 설정 필요", "error");
  setMessage(liveResult, error.message, "error");
});
