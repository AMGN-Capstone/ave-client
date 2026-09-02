# React UI 기능 대응

React 기본 UI(`/`)는 기존 정적 UI의 영상 편집 흐름을 같은 로컬 FastAPI API로 제공한다. 정적 UI는 전환 완료에 따라 제거했으며 React UI만 제공한다.

| 단계 | React UI 기능 | 사용 API |
| --- | --- | --- |
| 영상 확인 | URL 검증, 썸네일, 개요, 설명, 챕터, 히트맵 표시 | `POST /api/youtube/metadata` |
| 분석 설정 | LLM·장르·목표 길이·채팅/자막 싱크·STT·Whisper 세부 값·자막·렌더링 설정 | `POST /api/youtube/edit/start` |
| 진행 상태 | 분석/렌더링 단계, 메시지, 오류, 진행률 표시 | `GET /api/youtube/edit/{job_id}/status` |
| 구간 검토 | 원본 미리보기, 챕터 단위 선택, 개별 선택, AI 추천 복원, 전체 선택/해제, 예상 길이, 피드백 | `GET`/`PUT /api/youtube/edit/{job_id}/segments` |
| 결과 | 렌더링 영상 재생/다운로드, SRT 행 단위 자막 편집, 저장 후 재렌더링 | `GET`/`PUT /api/youtube/edit/{job_id}/subtitles`, `GET /api/youtube/edit/{job_id}/media/{kind}` |

## 확인 방법

1. 클라이언트 가상환경에서 `python -m app`을 실행한다.
2. 브라우저가 열리면 Google 로그인 후 YouTube URL을 입력한다.
3. **정보 확인**에서 메타데이터 탭을 확인하고 **AI 분석 시작**을 누른다.
4. 분석 완료 후 챕터와 후보 구간을 조정하고 렌더링한다.
5. 결과 영상과 자막 편집 후 재렌더링을 확인한다.

테스트에서는 외부 yt-dlp·LLM·FFmpeg 호출을 대체하여 메타데이터와 구간 검토 API의 응답 계약을 확인한다. 실제 전체 편집은 로컬에 yt-dlp와 FFmpeg가 설치되고 AVE 서버의 인증·작업 이력 스키마가 준비된 환경에서 수행한다.
