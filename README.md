# AVE Client

AVE Client는 사용자의 Windows PC에서 영상 편집을 수행하는 클라이언트 모듈이다. 원본 영상과 렌더링 결과는 로컬 `media/`에만 보관한다.

## 책임

* React 웹 UI와 로컬 FastAPI 서버 실행
* yt-dlp로 YouTube 메타데이터·영상·자막·채팅 리플레이 수집
* FFmpeg로 구간 편집, 음원 추출, 자막 합성, 결과 렌더링
* AVE Server를 통한 로그인 검증, LLM·원격 Whisper 호출, 작업 이력 동기화
* 진행 중인 작업 상태 복구, Whisper heartbeat, RunPod 취소 요청
* 시스템 트레이에서 UI 열기·로그 확인·종료 제공

클라이언트는 원본 영상, 렌더링 영상, 로컬 경로, 서버 API 키, Supabase 서비스 키를 서버로 보내지 않는다.

## 요구 환경

* Windows x64
* Python 3.11 이상
* AVE Server URL
* `/bin/yt-dlp.exe`, `/bin/ffmpeg.exe`, `/bin/ffprobe.exe`

개발 환경에서 영상 도구를 받으려면 다음을 실행한다.

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\fetch-tools.ps1
```

도구 배치·업데이트·배포 전 확인 항목은 [docs/TOOLCHAIN.md](docs/TOOLCHAIN.md)를 참고한다.

## 설정

`.env.example`을 복사해 `.env`를 만들고 AVE Server 주소만 설정한다.

```env
AVE_SERVER_URL=https://ave-server.example.com
WHISPER_HEARTBEAT_SECONDS=10
```

`YTDLP_COOKIES_FROM_BROWSER`, `YTDLP_COOKIEFILE`, `YTDLP_PLAYER_CLIENT`, `YTDLP_FORMAT`은 YouTube 수집 문제를 조정할 때만 선택적으로 사용한다.

Supabase 공개 로그인 설정과 LLM·Whisper 자격 증명은 AVE Server가 관리한다. 클라이언트 `.env`에 저장하지 않는다.

## 장시간 작업 추적과 취소

로컬 작업 상태에는 `job_id`(로컬 작업 ID), `server_job_id`, `runpod_job_id`가 SQLite에 함께 저장된다. 1단계를 제외한 모든 단계에서 브라우저를 새로고침하거나 탭을 닫으면 브라우저의 종료 확인 창이 표시된다. 확인하면 로컬 서버의 활성 작업 취소 API를 호출해 분석·렌더링을 중단하고, 진행 중인 Whisper 작업은 AVE Server를 거쳐 RunPod 취소를 요청한다. 다음 로드에서는 취소 대상 작업을 복원하지 않는다. 브라우저가 종료 요청을 전송하지 못한 경우에도 heartbeat 중단 뒤 서버 lease 만료 정책이 정리한다.

Whisper 전사 중에는 `WHISPER_HEARTBEAT_SECONDS`마다 AVE Server에 heartbeat를 보낸다. 로컬 클라이언트 자체가 종료되거나 네트워크가 끊기면 heartbeat가 멈추며, 서버의 lease 만료 정책이 RunPod 작업과 임시 MP3를 정리한다. 사용자가 UI의 **작업 취소**를 누르면 로컬 상태를 먼저 `cancel_requested`로 기록하고 RunPod 취소를 서버에 요청한다. 다운로드·FFmpeg·전사처럼 동기 실행 중인 로컬 구간은 다음 진행도 보고 지점에서 협력적으로 취소된다.

## 실행

의존성을 설치한 뒤 실행한다.

```powershell
python -m pip install -r requirements.txt
python -m app
```

`python -m app`은 로컬 FastAPI 서버를 백그라운드에서 실행하고 시스템 트레이 아이콘과 기본 React UI를 연다. 트레이에서 종료를 선택하면 진행 중인 작업 취소 여부를 확인하며, 확인 시 로컬 서버·AVE Server·RunPod에 순서대로 취소를 요청한 뒤 종료한다.

개발 중 UI를 수정했다면 다음 명령으로 빌드한다.

```powershell
cd ui
npm install
npm run build
```

## 주요 API

* `POST /api/youtube/metadata`: 영상 메타데이터 조회
* `POST /api/youtube/edit/start`: 분석 작업 시작
* `GET /api/youtube/edit/{job_id}/events`: 분석·렌더링 진행 상태 SSE 스트림
* `GET /api/youtube/edit/status/{job_id}`: 진단·호환용 REST 상태 조회
* `GET /api/youtube/edit/active`, `POST /api/youtube/edit/{job_id}/cancel`: 진행 작업 복구와 단일 작업 취소
* `POST /api/youtube/edit/cancel-active`: 브라우저 확인 또는 트레이 종료 시 모든 활성 작업 취소
* `GET`/`PUT /api/youtube/edit/{job_id}/segments`: 추천 구간 조회·선택 렌더링
* `GET`/`PUT /api/youtube/edit/{job_id}/subtitles`: 자막 조회·수정·재렌더링

## 문서

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): 클라이언트와 서버 책임 경계
* [docs/TOOLCHAIN.md](docs/TOOLCHAIN.md): Windows 바이너리 관리
* [docs/UI_PARITY.md](docs/UI_PARITY.md): React UI 기능과 수동 확인 절차
