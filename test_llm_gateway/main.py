from dotenv import load_dotenv
from llm_gateway import LLMGateway

load_dotenv()

def main():
    model_name = "openai/gpt-oss-120b"
    temperature = 0.7
    gateway = LLMGateway(provider="groq")
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

        conversation_history.append({"role": "user", "content": user_input})

        response = gateway.request(
            messages=conversation_history,
            model=model_name,
            temperature=temperature,
            max_tokens=1024
        )

        print(response)
        print("=" * 80)

        conversation_history.append({"role": "assistant", "content": response})

if __name__ == "__main__":
    main()
