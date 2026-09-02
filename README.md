# Automatic Video Editor

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

로컬 `media`는 원본과 작업 결과를 엄격히 분리합니다. `yt-data/<video_id>`에는
yt-dlp가 만든 원본 파일만 보관하며 서비스가 이를 수정하거나 서비스용 필드를
추가하지 않습니다. 서비스가 만든 채팅 정규화본, 전사본, 편집 계획, 렌더 결과,
업로드 파일 등은 모두 `yt-edit/<job_id>`에 보관합니다.

```text
media/
├── yt-data/
│   └── video_id/          # yt-dlp output only: ID.mp4, ID.info.json, ID.ko.vtt …
│       └── thumbnails/    # sddefault.jpg, sd1.jpg, sd2.jpg, sd3.jpg만 저장
└── yt-edit/
    ├── job_id/            # import.json, chat-replay.jsonl, transcripts, plans, renders …
    └── uploads/upload_id/ # locally uploaded source video
```

```text
longform-media/
└── user_id/
    └── job_id/
        ├── video.mp4
        ├── subtitle-0.vtt
        ├── video_id.info.json
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

자막을 내려받을 수 없는 영상은 로컬 `faster-whisper`를 사용합니다.

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

## 업로드 완료 영상 자동 편집 흐름

기본 입력은 **이미 업로드되어 재생 가능한 YouTube 영상 URL**입니다. 방송 중 채팅을 별도로 수집하거나, 종료 뒤 다시보기와 결합할 필요가 없습니다.

1. 업로드 완료된 YouTube 영상 URL을 입력하면 `yt-dlp`가 썸네일, 통계, 자막·캡션, 챕터, 히트맵 등 공개 메타데이터를 확인합니다.
2. 메타데이터 확인 후 분석 설정을 선택하고, 서버가 영상 ID를 확인해 `yt-dlp`로 채팅 리플레이를 한 번 수집합니다.
3. 리플레이가 있으면 `elapsedTime`에 사용자가 지정한 지연 보정을 적용하고, 각 스크립트 클러스터 시간 범위의 평균 채팅 밀도를 계산합니다.
4. 리플레이가 없거나 수집에 실패해도 작업을 중단하지 않고 자막 기반 분석으로 계속 진행합니다.
5. 같은 YouTube 영상 ID의 원본 영상과 VTT 자막이 이미 있으면 `media/yt-data/<video_id>`에서 재사용하고, 둘 중 하나라도 없으면 새로 수집합니다.
6. `yt-dlp`로 영상과 한국어 자막을 수집하고, 필요할 때만 Gemini 자막 정제를 실행합니다. 정제 응답은 변경된 `id`, `text`, `keep`만 반환합니다.
7. 요약과 주제 챕터는 시간 대신 자막 `id` 경계로 생성하고, 서버가 실제 시간으로 검증·변환합니다. 청크 경계는 인접 요약을 한 번 더 검토합니다.
8. 모든 스크립트 클러스터를 Gemini가 `id`, `text`만으로 평가합니다. 채팅 반응은 보조 분석값으로 보존하지만 중요도 점수를 미리 거르는 기준으로 쓰지 않습니다.
9. AI 추천 후보를 사용자가 검토·선택한 뒤 선택 구간만 렌더링합니다.

채팅 리플레이는 선택적 보조 신호입니다. `yt-dlp`가 리플레이를 제공하지 않거나 수집에 실패해도 자막 기반 편집을 계속할 수 있습니다.

### 로컬 실행

프로젝트 루트에서 다음 명령을 실행합니다.

```powershell
.\start.bat
```

`start.bat`는 가상환경을 초기화하고 의존성을 설치한 뒤 서버를 실행합니다.
서버 창에서 Ctrl+C를 누르면 확인 문구 없이 서버가 종료됩니다.

그 다음 브라우저에서 `http://127.0.0.1:8000`을 열고 Google 로그인 → 업로드 완료 영상 URL 입력 → AI 후보 검토 및 렌더링 순서로 진행합니다.

### 주요 API

- `POST /api/youtube/edit/start`: 업로드 완료 영상 분석 작업 시작. 채팅 리플레이는 있으면 사용하고 없으면 건너뜀
- `POST /api/youtube/metadata`: `yt-dlp`로 URL의 공개 메타데이터 조회
- `GET /api/youtube/edit/status/{job_id}`: 분석·렌더링 작업 상태 조회
- `POST /api/videos/upload`: 별도 승인된 로컬 원본을 보관할 때 사용하는 MVP API

`/api/youtube/live/*` 경로는 기존 방송 중 채팅 수집 실험과의 호환을 위해 남아 있지만, 기본 웹 흐름에서는 사용하지 않습니다. 기존 `/api/youtube/live/edit/*` 경로도 새 `/api/youtube/edit/*` 경로의 호환용 별칭입니다.

## 사용자 구간 검토

AI 분석 작업은 기본적으로 영상을 바로 렌더링하지 않고 `awaiting_selection` 상태에서 멈춥니다.
웹 화면에는 AI가 평가한 후보 구간이 시간순으로 표시되며 사용자는 다음 작업을 할 수 있습니다.

- 실제 주제 전환 수에 맞는 챕터로 먼저 요약해 주제 단위로 선택
- Gemini 요약 응답의 자막 ID 시작·종료 경계를 서버가 검증한 뒤 후보를 챕터에 배정하고, 잘못된 ID면 시간순 안전 대체
- 선택한 챕터의 세부 구간만 펼쳐 포함 여부를 정밀 조정
- 후보의 원본 시간대, 자막, AI 점수 확인
- 원본 영상에서 각 후보 구간 미리보기
- AI 추천 구간 복원, 전체 선택 또는 직접 선택
- 선택 개수와 예상 결과 길이 즉시 확인
- 선택한 구간만 시간순으로 연결해 영상 재생성
- 선택 피드백과 수정 이력을 `segment_revisions.json`에 저장

관련 API는 다음과 같습니다.

- `GET /api/youtube/edit/{job_id}/segments`: 후보와 현재 선택 조회
- `PUT /api/youtube/edit/{job_id}/segments`: 사용자 선택으로 백그라운드 재렌더 시작
- `GET /api/youtube/edit/{job_id}/media/source`: 원본 미리보기 스트리밍
- `GET /api/youtube/edit/{job_id}/media/rendered`: 최신 편집 결과 스트리밍

### AVE Whisper API 사용

영상 분석 화면의 `STT 소스`에서 `AVE Whisper API 사용`을 선택하면 YouTube 자막 대신 배포된 `ave-whisper-api`로 전사합니다. `token.env`에 다음 값을 설정해야 합니다.

```env
WHISPER_RUNPOD_ENDPOINT_ID=<RunPod Queue Endpoint ID>
RUNPOD_API_KEY=<RunPod API key>
WHISPER_RUNPOD_TIMEOUT_SECONDS=3600
AZURE_MEDIA_SFTP_HOST=
AZURE_MEDIA_SFTP_PORT=
AZURE_MEDIA_SFTP_USER=
AZURE_MEDIA_SFTP_KEY_PATH=
AZURE_MEDIA_SSH_KNOWN_HOSTS=
AZURE_MEDIA_PUBLIC_BASE_URL=
```

Whisper를 선택하면 `yt-dlp`로 수집한 원본에서 음원을 추출해 Azure VM의 SFTP 저장소에 임시 업로드합니다. RunPod worker가 HTTPS URL을 내려받아 전사하며, 성공·실패 여부와 무관하게 ave-client가 업로드 파일을 즉시 삭제합니다. `WHISPER_RUNPOD_TIMEOUT_SECONDS`는 앱이 Queue 작업 완료를 기다리는 최대 시간이며 기본값은 3,600초(1시간)입니다. RunPod Endpoint의 worker 실행 제한도 이보다 짧지 않게 설정하세요. SFTP 개인키와 RunPod API 키는 Git에 포함하지 마세요.
