# Longform Auto Editor MVP

정보성 YouTube 풀영상과 사용자가 직접 올린 영상을 자동 편집 파이프라인의 입력 자료로 저장하는 최소 웹앱입니다.

## 실행

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

브라우저에서 `http://127.0.0.1:8000`을 열면 됩니다.

## 현재 기능

- `POST /api/youtube/import`: YouTube URL을 받아 `yt-dlp`로 영상, 자막, 메타데이터를 `media/youtube/<job_id>/`에 저장합니다.
- `POST /api/videos/upload`: `mp4`, `mov`, `mkv`, `webm` 파일을 `media/uploads/`에 저장합니다.
- `/`: YouTube URL 수집과 직접 영상 업로드를 수행하는 최소 화면을 제공합니다.

## 왜 API와 yt-dlp를 같이 쓰는가

브라우저는 파일 선택과 URL 입력만 담당하고, 서버 API가 저장과 수집 작업을 담당하는 구조가 안전합니다. YouTube 원본 영상과 자막을 가져오는 단계는 공식 YouTube Data API보다 `yt-dlp`가 프로토타입에 적합합니다. YouTube Data API는 주로 영상 정보 조회, 채널/재생목록/업로드 관리에 쓰이고, 공개 영상 파일과 자동자막을 다운로드하는 용도에는 맞지 않습니다.

`yt-dlp`는 영상 파일, 자동 생성 자막, 작성자가 올린 자막, 썸네일, 챕터, 설명 같은 자료를 가져올 수 있습니다. 이후 자동 편집 단계에서는 자막과 챕터를 기준으로 핵심 구간을 찾고, `ffmpeg`로 결과 영상을 만들면 됩니다.

## 주의할 점

- `ffmpeg`가 설치되어 있지 않으면 앱은 자동으로 단일 파일 포맷(`best[ext=mp4]/best`)을 받아서 병합 오류를 피합니다. 이 경우 화질이 낮아질 수 있고 화면의 `경고`에 표시됩니다.
- 이후 자동 편집 단계에서는 컷 편집과 인코딩을 위해 `ffmpeg` 설치가 필요합니다.
- YouTube가 자막 요청을 `HTTP 429 Too Many Requests`로 제한하면, 현재 앱은 자막 없이 영상과 메타데이터만 다시 수집하고 화면에 경고를 표시합니다.
- 실제 서비스에서는 YouTube 약관, 저작권, 사용자의 이용 허가 범위를 반드시 검토해야 합니다.
- 현재 구현은 MVP라서 수집 요청이 끝날 때까지 HTTP 요청을 유지합니다. 긴 영상 처리에는 작업 큐와 진행률 API를 붙이는 구조가 좋습니다.
