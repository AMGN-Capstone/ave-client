const apiStatus = document.querySelector("#apiStatus");
const googleLoginButton = document.querySelector("#googleLoginButton");
const logoutButton = document.querySelector("#logoutButton");
const youtubeForm = document.querySelector("#youtubeForm");
const youtubeResult = document.querySelector("#youtubeResult");

let supabaseClient = null;
let currentSession = null;

function setStatus(text, state = "idle") {
  apiStatus.textContent = text;
  apiStatus.dataset.state = state;
}

function setMessage(target, text, type = "success") {
  target.innerHTML = `<p class="message ${type}">${escapeHtml(text)}</p>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderDetails(target, rows) {
  const content = rows
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([label, value]) => `<div class="result-row"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(Array.isArray(value) ? value.join(", ") : value)}</dd></div>`)
    .join("");
  target.innerHTML = `<dl class="result-list">${content}</dl>`;
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
    options: { redirectTo: window.location.origin },
  });
  if (error) setMessage(youtubeResult, error.message, "error");
});

logoutButton.addEventListener("click", async () => {
  await supabaseClient.auth.signOut();
  updateAuthState(null);
});

youtubeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentSession?.access_token) {
    setMessage(youtubeResult, "먼저 Google 로그인을 해주세요.", "error");
    return;
  }

  const submitButton = youtubeForm.querySelector("button");
  const url = new FormData(youtubeForm).get("url");
  submitButton.disabled = true;
  setStatus("YouTube 수집 중", "busy");
  setMessage(youtubeResult, "영상 길이에 따라 시간이 걸릴 수 있습니다.");

  try {
    const response = await fetch("/api/youtube/import", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${currentSession.access_token}`,
      },
      body: JSON.stringify({ url }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "수집에 실패했습니다.");
    renderDetails(youtubeResult, [
      ["작업 ID", data.job_id],
      ["영상 ID", data.video_id],
      ["스크립트 ID", data.transcript_id],
      ["제목", data.title],
      ["영상 Storage 경로", data.video_path],
      ["자막 Storage 경로", data.subtitle_files],
      ["경고", data.warnings],
    ]);
    setStatus("수집 완료", "success");
  } catch (error) {
    setMessage(youtubeResult, error.message, "error");
    setStatus("수집 실패", "error");
  } finally {
    submitButton.disabled = false;
  }
});

loadConfig().catch((error) => {
  setStatus("Supabase 설정 필요", "error");
  setMessage(youtubeResult, error.message, "error");
});
