import os
from dotenv import load_dotenv
from llm_gateway import LLMGateway

# ==============================================================================
PROVIDER = "gemini"

if PROVIDER == "groq":
    MODEL_NAME = "openai/gpt-oss-120b"
elif PROVIDER == "gemini":
    MODEL_NAME = "gemini-3.5-flash"

TEMPERATURE = 0.7
MAX_TOKENS = 1024
TOP_P = 1.0
# ==============================================================================

load_dotenv()

def main():
    try:
        gateway = LLMGateway(provider=PROVIDER)
    except ValueError as e:
        print(e)
        return

    conversation_history = []

    print("대화를 시작합니다. (종료하려면 '/q' 입력)")
    print("=" * 80)

    while True:
        print("🟡 Q:")
        print()
        user_input = input().strip()

        if not user_input:
            continue

        print()
        print("🔵 A:")
        print()

        if user_input.lower() in ["/q"]:
            print("대화를 종료합니다.")
            break

        if PROVIDER == "groq":
            conversation_history.append({"role": "user", "content": user_input})
            payload_messages = conversation_history
        elif PROVIDER == "gemini":
            conversation_history.append(f"User: {user_input}")
            payload_messages = "\n".join(conversation_history)

        response = gateway.request(
            messages=payload_messages,
            model=MODEL_NAME,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            top_p=TOP_P
        )

        print(response)
        print("=" * 80)

        if PROVIDER == "groq":
            conversation_history.append({"role": "assistant", "content": response})
        elif PROVIDER == "gemini":
            conversation_history.append(f"Model: {response}")

if __name__ == "__main__":
    main()
