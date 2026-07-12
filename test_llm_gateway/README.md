# Test - LLM Gateway

이 모듈은 제공되는 여러 LLM 중 하나를 선택하여 호출할 수 있는 게이트웨이를 테스트하기 위한 것입니다. 본 모듈을 실행하기 위해서는 아래 절차에 따라 진행해 주시길 바랍니다. 모든 절차는 `test_llm_gateway` 디렉토리에서 수행되어야 합니다.

- `.env` 파일을 생성하고 아래 내용을 작성하세요. API 호출을 위한 키로 사용됩니다. API 키는 아래 링크에서 획득하세요.
  - GROQ_API_KEY: [console.groq.com](https://console.groq.com/)

  ```env
  GROQ_API_KEY="gsk_your_actual_api_key_here"
  ```

- CMD에서 아래 명령어를 입력하여 가상 환경을 생성하세요.

  ```bat
  python -m venv .venv
  ```

- CMD에서 아래 명령어를 입력하여 가상 환경을 실행하세요.

  ```bat
  .venv\Scripts\activate
  ```

- CMD에서 아래 명령어를 입력하여 가상 환경에 의존성 패키지를 설치하세요.

  ```bat
  pip install -r requirements.txt
  ```

- CMD에서 아래 명령어를 입력하여 모듈을 실행하세요.

  ```bat
  python main.py
  ```

- CMD에서 아래 명령어를 입력하여 가상 환경을 종료하세요.

  ```bat
  deactivate
  ```
