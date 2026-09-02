# AVE Client

AVE Client는 사용자의 Windows PC에서 영상 편집을 수행하는 클라이언트 모듈이다. 원본 영상과 렌더링 결과는 로컬 `media/`에만 보관한다.

## 책임

* React 웹 UI와 로컬 FastAPI 서버 실행
* yt-dlp로 YouTube 메타데이터·영상·자막·채팅 리플레이 수집
* FFmpeg로 구간 편집, 음원 추출, 자막 합성, 결과 렌더링
* AVE Server를 통한 로그인 검증, LLM·원격 Whisper 호출, 작업 이력 동기화
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
```

`YTDLP_COOKIES_FROM_BROWSER`, `YTDLP_COOKIEFILE`, `YTDLP_PLAYER_CLIENT`, `YTDLP_FORMAT`은 YouTube 수집 문제를 조정할 때만 선택적으로 사용한다.

Supabase 공개 로그인 설정과 LLM·Whisper 자격 증명은 AVE Server가 관리한다. 클라이언트 `.env`에 저장하지 않는다.

## 실행

의존성을 설치한 뒤 실행한다.

```powershell
python -m pip install -r requirements.txt
python -m app
```

`python -m app`은 로컬 FastAPI 서버를 백그라운드에서 실행하고 시스템 트레이 아이콘과 기본 React UI를 연다. 트레이 메뉴에서 웹 UI, `client.log`, 종료 기능을 사용할 수 있다.

개발 중 UI를 수정했다면 다음 명령으로 빌드한다.

```powershell
cd ui
npm install
npm run build
```

## 주요 API

* `POST /api/youtube/metadata`: 영상 메타데이터 조회
* `POST /api/youtube/edit/start`: 분석 작업 시작
* `GET /api/youtube/edit/status/{job_id}`: 분석·렌더링 진행 상태 조회
* `GET`/`PUT /api/youtube/edit/{job_id}/segments`: 추천 구간 조회·선택 렌더링
* `GET`/`PUT /api/youtube/edit/{job_id}/subtitles`: 자막 조회·수정·재렌더링

## 문서

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): 클라이언트와 서버 책임 경계
* [docs/TOOLCHAIN.md](docs/TOOLCHAIN.md): Windows 바이너리 관리
* [docs/UI_PARITY.md](docs/UI_PARITY.md): React UI 기능과 수동 확인 절차
