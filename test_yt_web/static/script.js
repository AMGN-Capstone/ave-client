const apiStatus = document.querySelector("#apiStatus");
const youtubeForm = document.querySelector("#youtubeForm");
const uploadForm = document.querySelector("#uploadForm");
const youtubeResult = document.querySelector("#youtubeResult");
const uploadResult = document.querySelector("#uploadResult");

function setStatus(text, state = "idle") {
  apiStatus.textContent = text;
  apiStatus.dataset.state = state;
}

function setMessage(target, text, type = "success") {
  target.innerHTML = `<p class="message ${type}">${escapeHtml(text)}</p>`;
}

function renderDetails(target, rows) {
  const content = rows
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([label, value]) => {
      const printable = Array.isArray(value) ? value.join(", ") || "-" : String(value);
      return `<div class="result-row"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(printable)}</dd></div>`;
    })
    .join("");

  target.innerHTML = `<dl class="result-list">${content}</dl>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function parseResponse(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "요청을 처리하지 못했습니다.");
  }
  return data;
}

youtubeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = youtubeForm.querySelector("button");
  const url = new FormData(youtubeForm).get("url");

  submitButton.disabled = true;
  setStatus("YouTube 수집 중", "busy");
  setMessage(youtubeResult, "수집 작업을 시작했습니다. 영상 길이에 따라 시간이 걸릴 수 있습니다.");

  try {
    const response = await fetch("/api/youtube/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await parseResponse(response);
    renderDetails(youtubeResult, [
      ["작업 ID", data.job_id],
      ["제목", data.title],
      ["길이", data.duration ? `${data.duration}초` : "-"],
      ["영상 경로", data.video_path],
      ["자막 파일", data.subtitle_files],
      ["메타데이터", data.metadata_path],
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

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = uploadForm.querySelector("button");
  const formData = new FormData(uploadForm);

  submitButton.disabled = true;
  setStatus("업로드 중", "busy");
  setMessage(uploadResult, "파일을 서버에 저장하고 있습니다.");

  try {
    const response = await fetch("/api/videos/upload", {
      method: "POST",
      body: formData,
    });
    const data = await parseResponse(response);
    renderDetails(uploadResult, [
      ["원본 파일", data.original_filename],
      ["저장 파일", data.stored_filename],
      ["형식", data.content_type],
      ["크기", `${data.size_bytes.toLocaleString()} bytes`],
      ["저장 경로", data.path],
    ]);
    setStatus("업로드 완료", "success");
  } catch (error) {
    setMessage(uploadResult, error.message, "error");
    setStatus("업로드 실패", "error");
  } finally {
    submitButton.disabled = false;
  }
});
