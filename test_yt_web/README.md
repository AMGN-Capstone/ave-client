# Longform Auto Editor

YouTube 링크를 받아 영상, 자막, 메타데이터를 수집하고 사용자별로 Supabase에 저장하는 FastAPI MVP입니다.

## 현재 흐름

1. 브라우저에서 Google 로그인
2. Supabase Auth가 사용자 세션 발급
3. 로그인 토큰을 FastAPI에 전달
4. FastAPI가 토큰을 검증
5. `yt-dlp`로 YouTube 영상과 자막 수집
6. 영상·자막·메타데이터를 `longform-media` Storage에 저장
7. `videos`, `processing_jobs`, `transcripts`에 사용자별 메타데이터 저장

## DB 구조

Supabase Auth의 사용자를 기준으로 영상, 작업, 스크립트, 분석 구간을 연결합니다.

```text
auth.users
   │
   ├── profiles
   │
   ├── videos
   │      ├── processing_jobs
   │      ├── transcripts
   │      ├── video_comments
   │      └── video_segments
   │
   └── edit_jobs
```

### `auth.users`

Supabase Auth가 관리하는 사용자 계정입니다. Google 로그인으로 생성된 사용자 UUID가 다른 테이블의 `user_id`로 사용됩니다.

### `profiles`

사용자의 추가 정보입니다.

```text
id              UUID, auth.users.id와 연결
display_name    표시 이름
created_at      생성 시각
```

### `videos`

사용자가 수집한 YouTube 원본 영상의 메타데이터입니다.

```text
id              영상 ID
user_id         소유 사용자 ID
source_url      YouTube 원본 링크
title           영상 제목
channel_name    채널명
duration_sec    영상 길이(초)
storage_path    Storage 내 영상 경로
metadata        yt-dlp 메타데이터 JSON
created_at      저장 시각
```

### `processing_jobs`

영상 수집, STT, 요약, 편집 작업의 진행 상태를 저장합니다.

```text
id              작업 ID
user_id         작업 요청 사용자
video_id        대상 영상
kind            import, transcribe, summarize, edit
status          queued, downloading, transcribing, completed, failed 등
progress        진행률(0~100)
error_message   실패 사유
started_at      시작 시각
completed_at    완료 시각
```

### `transcripts`

YouTube 자막 또는 Whisper가 생성한 스크립트입니다.

```text
id              스크립트 ID
user_id         사용자 ID
video_id        영상 ID
language        언어 코드
source          youtube_caption, whisper, manual
content         전체 스크립트
segments        구간별 스크립트 JSON
storage_path    원본 자막 파일 경로
```

### `video_comments`

댓글과 댓글 타임스탬프를 저장합니다. 향후 인기 구간과 댓글 밀도 계산에 사용합니다.

```text
id              댓글 ID
video_id        영상 ID
content         댓글 내용
timestamp_ms    영상 기준 댓글 시각(밀리초)
```

### `video_segments`

LLM 요약과 영상 편집을 위한 분석 구간입니다.

```text
id                         구간 ID
video_id                   영상 ID
segment_index              구간 순번
start_ms / end_ms          시작·종료 시각(밀리초)
content                    구간 스크립트
llm_score                  LLM 중요도 점수
comment_timestamp_count    댓글 타임스탬프 개수
comment_density            구간별 댓글 밀도
average_volume_dbfs        평균 음량
final_score                최종 편집 점수
```

### `edit_jobs`

선택된 구간을 연결해 최종 영상을 생성하는 작업입니다.

```text
id                    편집 작업 ID
user_id               사용자 ID
source_video_id       원본 영상 ID
selected_segment_ids  선택된 구간 ID 목록
edit_plan             편집 계획 JSON
result_storage_path   완성 영상 Storage 경로
status                작업 상태
```

## Storage 구조

실제 영상 파일은 DB에 직접 저장하지 않고 비공개 Storage bucket에 저장합니다. DB에는 파일 경로만 저장합니다.

```text
longform-media/
└── user_id/
    └── job_id/
        ├── video.mp4
        ├── subtitle-0.vtt
        ├── metadata.json
        └── rendered.mp4
```

## 설치

Python 3.10 이상을 권장합니다.

```powershell
python -m pip install -r requirements.txt
```

## 환경변수

`token.env.example`을 복사해 `token.env`를 만들고 Supabase 값을 입력합니다.

```env
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
```

`SUPABASE_SERVICE_ROLE_KEY`는 서버에서만 사용하며 브라우저 코드나 GitHub에 올리면 안 됩니다.

## Supabase 설정

1. `docs/supabase_schema.sql`을 Supabase SQL Editor에서 실행합니다.
2. Authentication → Providers → Google에서 Google provider를 활성화합니다.
3. Supabase URL Configuration에 다음 Redirect URL을 추가합니다.

```text
http://127.0.0.1:8000
http://localhost:8000
```

4. Google Cloud OAuth Client의 Authorized redirect URI에는 Supabase Dashboard가 안내하는 callback URL을 입력합니다.

## 실행

```powershell
python -m uvicorn app.main:app --reload
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다.

## API

- `GET /api/config`: 브라우저용 Supabase URL과 공개 키 반환
- `GET /api/auth/me`: 로그인 토큰 검증
- `POST /api/youtube/import`: 로그인 사용자의 YouTube 영상·스크립트 수집 및 저장
- `POST /api/videos/upload`: 로컬 영상 업로드 MVP

현재 YouTube 수집은 요청이 끝날 때까지 기다리는 동기 MVP입니다. 3시간 이상 영상에서는 다음 단계로 worker와 작업 상태 polling을 추가해야 합니다.

## 라이브 채팅 기반 자동 편집 흐름

새 라이브 기능은 일반 댓글과 자막 수집을 사용하지 않습니다.

1. Google 로그인 후 YouTube 읽기 권한을 허용합니다.
2. 방송 중인 라이브 URL을 `http://127.0.0.1:8000`의 `방송 중 채팅 수집`에 입력합니다.
3. 서버가 `activeLiveChatId`를 확인하고 `liveChatMessages.list`를 주기적으로 호출합니다.
4. 채팅은 `media/youtube-live-chat/<live_chat_id>.jsonl`에 중복 없이 누적 저장됩니다.
5. 방송 종료 후 다시보기 URL을 `다시보기 결합`에 입력합니다.
6. 서버는 `pytchat`으로 다시보기 채팅 리플레이를 우선 수집합니다.
7. `pytchat`이 실패하면 방송 중 저장한 공식 API JSONL을 fallback으로 사용합니다.
8. 리플레이의 `elapsedTime` 또는 저장된 채팅 작성 시각을 기준으로 30초 구간별 채팅량과 급증 구간을 계산합니다.
9. `highlight_windows`를 영상 편집기의 컷 후보 입력으로 사용합니다.

다시보기 URL은 메타데이터와 채팅 리플레이 수집에 사용됩니다. 실제 자동 편집에는 사용자가 권리를 보유한 원본 영상 파일을 `/api/videos/upload`로 업로드하거나, 별도 승인된 영상 입력을 사용해야 합니다. `pytchat`은 비공식 라이브러리이며 저장소가 보관 처리된 상태이므로 YouTube 변경에 따라 작동하지 않을 수 있습니다.

### 로컬 실행

프로젝트 루트에서 다음 명령을 실행합니다.

```powershell
cd test_yt_web
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

그 다음 브라우저에서 `http://127.0.0.1:8000`을 열고 Google 로그인 → 라이브 URL 입력 → 방송 종료 후 다시보기 URL 입력 순서로 진행합니다.

### 주요 API

- `POST /api/youtube/live/inspect`: 라이브 메타데이터와 활성 채팅 ID 확인
- `GET /api/youtube/live/chat`: 다음 채팅 페이지 수집 및 로컬 JSONL 저장
- `POST /api/youtube/live/finalize`: pytchat 리플레이 우선 수집 및 저장된 채팅 fallback 분석
- `POST /api/videos/upload`: 편집에 사용할 권한 있는 원본 영상 업로드
