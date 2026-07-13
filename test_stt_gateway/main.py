import os
import json
import warnings
from dotenv import load_dotenv

warnings.filterwarnings("ignore", message="Couldn't find ffmpeg.*", category=RuntimeWarning)
warnings.filterwarnings("ignore", message="Couldn't find ffprobe.*", category=RuntimeWarning)

from stt_gateway import STTGateway

# ==============================================================================
PROVIDER = "groq" 
MODEL_NAME = "whisper-large-v3-turbo"
LANGUAGE = "ko"

SPEED_FACTOR = 1.5
# ==============================================================================

load_dotenv()

def main():
    try:
        gateway = STTGateway(provider=PROVIDER)
    except ValueError as e:
        print(e)
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.join(base_dir, "file_src")
    trg_dir = os.path.join(base_dir, "file_trg")

    os.makedirs(src_dir, exist_ok=True)
    os.makedirs(trg_dir, exist_ok=True)

    print(f"🔵 파일 처리를 시작합니다. (사용 모델: {MODEL_NAME}, 언어: {LANGUAGE})")

    mp3_files = [f for f in os.listdir(src_dir) if f.lower().endswith('.mp3')]
    
    if not mp3_files:
        return

    for filename in mp3_files:
        file_base_name = os.path.splitext(filename)[0]
        src_path = os.path.join(src_dir, filename)
        
        trg_path = os.path.join(trg_dir, f"{file_base_name}.txt")

        if os.path.exists(trg_path):
            print(f"🟡 {filename} - 이미 처리된 파일입니다.")
            continue

        print(f"🟡 {filename} - 변환 중...")

        response = gateway.request(
            file_path=src_path,
            speed_factor=SPEED_FACTOR,
            model=MODEL_NAME,
            language=LANGUAGE
        )

        if "error" in response:
            print(f"🔴 {filename} - 처리 실패: {response['error']}")
            continue

        with open(trg_path, "w", encoding="utf-8") as f:
            json.dump(response, f, ensure_ascii=False, indent=4)
        
        print(f"🟢 {filename} - 변환 완료")

    print("🔵 모든 파일의 처리가 완료되었습니다.")

if __name__ == "__main__":
    main()
