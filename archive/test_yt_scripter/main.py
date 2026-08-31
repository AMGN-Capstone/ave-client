import os
from yt_scripter import Scripter

# ==============================================================================
VIDEO_URL = "https://www.youtube.com/watch?v=3tejmt47Hkw"
LANGUAGE = "ko"
METHOD = "auto"
# ==============================================================================

def main():
    try:
        scripter = Scripter(method=METHOD)
    except ValueError as e:
        print(e)
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    audio_dir = os.path.join(base_dir, "file_audio")
    stt_dir = os.path.join(base_dir, "file_script_stt")
    auto_dir = os.path.join(base_dir, "file_script_auto")

    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(stt_dir, exist_ok=True)
    os.makedirs(auto_dir, exist_ok=True)

    print(f"🔵 유튜브 영상 스크립트 추출을 시작합니다. (방식: {METHOD}, 언어: {LANGUAGE})")
    print(f"URL: {VIDEO_URL}\n")

    target_dir = auto_dir if METHOD == "auto" else stt_dir

    response = scripter.process(
        video_url=VIDEO_URL,
        target_dir=target_dir,
        lang=LANGUAGE
    )

    if "error" in response:
        print(f"🔴 처리 실패: {response['error']}")
    else:
        print(f"🟢 처리 완료: {response['video_id']}")

if __name__ == "__main__":
    main()
