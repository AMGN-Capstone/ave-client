# AVE Client

AVE Client는 사용자의 Windows PC에서 영상 편집을 수행하는 클라이언트 모듈이다. 원본 영상과 렌더링 결과는 로컬 `media/`에만 보관한다.

## 책임

* React 웹 UI와 로컬 FastAPI 서버 실행
* yt-dlp로 YouTube 메타데이터·영상·댓글·채팅·자막·캡션 수집
* FFmpeg로 구간 편집, 음원 추출, 자막 합성, 결과 렌더링
* AVE Server를 통한 로그인 검증, LLM·원격 Whisper 호출, 작업 이력 동기화
* 완료된 결과만 로컬 SQLite 및 AVE Server 이력에 동기화
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

기본 LLM은 DeepSeek(`deepseek-chat`)이며 UI·API·Gateway의 기본값이 같다. Gemini도 같은 JSON 응답 계약과 공통 Gateway로 선택할 수 있다. 병렬 정책은 공급자별로 분리한다. DeepSeek는 작업당 최대 100개의 독립 요청을 병렬 실행하고, Gemini는 15 RPM 한도에 맞춰 한 번에 하나만 제출하며 요청 시작 사이를 4초 이상 둔다. 여러 클라이언트에 걸친 계정 전체 RPM은 AVE Server가 추가로 제한해야 한다. 공급자 오류는 분석을 중단하고 원인을 화면에 표시한다.

렌더링은 기본적으로 FFmpeg가 지원하고 현재 드라이버에서 실행 가능한 `h264_nvenc`(NVIDIA), `h264_amf`(AMD), `h264_qsv`(Intel) H.264 인코더를 이 순서로 사용한다. GPU 인코더를 선택한 영상 재인코딩은 입력에도 `-hwaccel auto`를 적용해 하드웨어 디코딩을 우선 시도한다. 자막 합성·구간 trim처럼 CPU 필터가 필요한 단계는 FFmpeg가 안전하게 프레임을 전송해 처리한다. 인코더·드라이버·필터 호환성으로 실제 실행에 실패하면 같은 렌더링 작업을 CPU `libx264`로 처음부터 자동 재시도한다. `AVE_VIDEO_ENCODER=cpu`로 GPU를 끌 수 있고, 특정 인코더 이름을 지정해 우선 선택할 수 있다. 오디오 추출과 스트림 복사는 영상 재인코딩이 아니므로 하드웨어 가속 대상이 아니다.

제한 오류 등으로 이미 종료·정리된 작업에 브라우저가 뒤늦게 취소를 요청해도 취소 API는 성공으로 응답하고 남아 있을 수 있는 `yt-edit/{job_id}` 임시 폴더를 다시 정리한다.

렌더링 등 실행 중 오류는 SSE로 원인을 한 번 전달한 뒤 작업 폴더·메모리·토큰을 정리한다. SSE 단절로 이 마지막 메시지를 받지 못한 경우 UI는 상태 조회의 404를 종료·정리됨으로 표시한다.

4단계 자막 합성은 작업 폴더 안의 일회용 UTF-8 SRT를 FFmpeg에 드라이브·경로 구분자를 이스케이프한 절대 경로로 전달하고 완료·실패 뒤 삭제한다. Windows 임시 폴더 경로를 FFmpeg filtergraph에 넣지 않는다. 렌더링이 성공하면 서버 이력 동기화가 실패해도 결과 영상과 로컬 완료 이력은 유지하며, UI에는 서버가 반환한 동기화 실패 원인을 표시한다.

하단 진행 메시지는 긴 로그가 화면 밖으로 넘치지 않도록 끝부분을 흐리게 표시한다. 메시지를 세 번 클릭하면 브라우저의 문단 선택으로 숨겨진 부분을 포함한 전체 로그를 선택·복사할 수 있다.

## 장시간 작업 추적과 취소

작업 ID는 `<video_id>.<unix_timestamp_hex>` 형식이다. 진행 중 상태는 프로세스 메모리에만 존재하며 새로고침·재시작·취소·실패 시 복원하지 않는다. 취소와 실패 시 해당 `yt-edit/{job_id}` 폴더를 삭제한다. 결과 영상 생성이 성공한 작업만 SQLite와 AVE Server 이력에 저장한다.

완료 작업은 활성 작업 복구 대상이 아니다. 결과 조회 화면에서 **처음**을 누르면 메타데이터 단계로 돌아가며, 새 편집은 새 작업 ID로 시작한다.

## 로컬 파일 경계

`yt-data/{video_id}`는 yt-dlp가 만든 원본만 보관하며 재작업 때 그대로 재사용한다. `yt-edit/{video_id}.metadata`는 2단계에서 원본으로부터 만든 재사용 가능한 자료이고, 완료·실패와 관계없이 보존한다. 작업별 `yt-edit/{job_id}`에는 Whisper 전사와 최종 영상만 둔다. 분석 중 챕터·섹션·후보·원본 스크립트는 메모리에만 존재한다.

| 위치 | 파일 | 역할 |
| --- | --- | --- |
| `yt-data/{video_id}` | `{video_id}.info.json`, 댓글 JSON, 채팅 JSONL, 자막·캡션 VTT | yt-dlp 원본 |
| `yt-edit/{video_id}.metadata` | `{video_id}.comments-timestamps.json`, `{video_id}.chat-times.json` | 분석용 파생 메타데이터 |
| `yt-edit/{video_id}.metadata` | `{video_id}.{lang}.captions-rolling.vtt`, `*.{subtitles,captions}-transcript.json` | 표시용 롤링 캡션과 3단계 재사용 스크립트 |
| `yt-edit/{job_id}` | `{job_id}.whisper-transcript.json`, `{job_id}.edited-preview.mp4` | 작업별 산출물 |

3단계는 이 2단계 계약(`prepared_metadata_paths`, `load_prepared_transcript`)만 읽으며 yt-dlp 호출 또는 VTT 재파싱을 하지 않는다. 선택한 자막·캡션 언어의 전사 파일만 정확히 읽으므로, 같은 영상에 여러 언어 자료가 있어도 이전에 선택한 언어가 섞이지 않는다. 전체 스크립트는 한 번의 LLM 호출로 `summary`와 0~1000 정수 `score`가 있는 챕터로 나누고, 각 챕터는 시간 범위만 있는 섹션으로 분할한다. 중요도 평가는 챕터별 summary와 섹션 `[{"id","text"}]` 배열을 한 번에 보내고 같은 ID의 `[{"id","score"}]` 배열만 받는다. JSON 문법·ID·범위 계약이 틀리면 추측해 보정하지 않고 최대 10회 재시도한다. 독립적인 챕터별 섹션 분할과 점수 평가는 최대 10개씩 병렬 처리하며 완료 순서에 맞춰 진행률을 알린다. 3단계 소스는 지원되는 자막, 캡션, Whisper 순서로 제공하며, 초기 목표 길이는 영상 전체 길이의 1/4(최대 3,600초)다. 4단계는 챕터 → 섹션 계층에서 챕터·섹션의 실제 LLM 점수 배지, 본문과 선택 상태를 보여 준다. 모든 사용자 단계 이동은 현재 패널 접힘 뒤 다음 패널 펼침 애니메이션을 사용한다. FFmpeg 자막 합성용 SRT는 작업 중에만 만들고 즉시 삭제한다. 채팅은 원본 수집과 시각 파일 생성만 유지하며 분석 점수·밀도·지연에는 사용하지 않는다.

자막·캡션 파싱 파일과 Whisper 전사 파일은 모두 `{"segments":[{"start":number,"end":number,"text":string}]}` 형식이다. Whisper 전사 배속은 `1.0 (품질)`, `1.5 (균형)`, `2.0 (속도)`만 선택할 수 있으며 기본값은 품질이다.

Whisper 전사 중에는 `WHISPER_HEARTBEAT_SECONDS`마다 AVE Server에 heartbeat를 보낸다. 클라이언트는 `whisper-transcript.json`을 성공적으로 저장한 뒤 ACK를 보내며, ACK가 네트워크 오류로 실패해도 서버는 기본 15분 동안 완료 결과를 보관해 SSE 재연결이 다시 받을 수 있게 한다. ACK 또는 TTL 만료 뒤 전사 제어 레코드는 삭제된다. 로컬 클라이언트 자체가 종료되거나 네트워크가 끊기면 heartbeat가 멈추며, 서버의 lease 만료 정책이 RunPod 작업·임시 MP3·실행 제어 레코드를 정리한다. 사용자가 UI의 **작업 취소**를 누르면 전사 준비 단계에서도 `client_job_id` 기준 취소 의도를 서버에 먼저 전달한다. 서버는 RunPod 작업 ID가 아직 없더라도 이를 확인해 요청을 건너뛰거나 즉시 취소한다. 이 제어 정보는 완료 이력이 아니며 처리 또는 만료 뒤 삭제된다. 다운로드·FFmpeg·전사처럼 동기 실행 중인 로컬 구간은 다음 진행도 보고 지점에서 협력적으로 취소된다.

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

WSL 1에서는 Windows Node.js 설치 경로를 확인할 수 없어 `npm run build`가 실행되지 않는다. 이 경우 Windows PowerShell에서 워크스페이스를 연 뒤 아래 명령으로 Python 테스트와 UI 빌드를 실행한다.

```powershell
cd modules\ave-client
.\.venv\Scripts\python.exe -m pytest -q
cd ui
npm run build
```

## 주요 API

* `POST /api/youtube/metadata`: 영상 메타데이터 조회
* `POST /api/youtube/metadata/materials/start`, `GET /api/youtube/metadata/materials/{job_id}`: 2단계 자료 준비와 일회성 완료 응답 조회
* `POST /api/youtube/edit/start`: 분석 작업 시작
* `GET /api/youtube/edit/{job_id}/events`: 분석·렌더링 진행 상태 SSE 스트림
* `GET /api/youtube/edit/status/{job_id}`: SSE 재연결 전 상태 확인
* `GET /api/youtube/edit/active`, `POST /api/youtube/edit/{job_id}/cancel`: 진행 작업 복구와 단일 작업 취소
* `POST /api/youtube/edit/cancel-active`: 브라우저 확인 또는 트레이 종료 시 모든 활성 작업 취소
* `GET`/`PUT /api/youtube/edit/{job_id}/segments`: 추천 구간 조회·선택 렌더링
* `GET /api/youtube/edit/{job_id}/media/source`: 활성 선택·렌더링 작업의 원본 미리보기
* `GET /api/youtube/edit/{job_id}/media/rendered`: 완료된 결과 영상 조회

## 문서

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): 클라이언트와 서버 책임 경계
* [docs/TOOLCHAIN.md](docs/TOOLCHAIN.md): Windows 바이너리 관리
* [docs/UI_PARITY.md](docs/UI_PARITY.md): React UI 기능과 수동 확인 절차
