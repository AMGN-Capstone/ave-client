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

## 설치

Python 3.10 이상을 권장합니다.

```powershell
python -m pip install -r requirements.txt
```

## 환경변수

`token.env.example`을 복사해 `token.env`를 만들고 Supabase 값을 입력합니다.

```env
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=your-publishable-or-anon-key
SUPABASE_SERVICE_ROLE_KEY=server-only-secret-key
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

현재 YouTube 수집은 요청이 끝날 때까지 기다리는 동기 MVP입니다. 3시간 이상 라이브 영상에서는 다음 단계로 worker와 작업 상태 polling을 추가해야 합니다.
