import os

URL = "https://www.youtube.com/watch?v=IkBHQUE5FKw"  # <- 유튜브 링크를 여기에 넣으세요


SEGMENTS = [(30, 90), (260, 320), (490, 550), (720, 780),
            (950, 1010), (1180, 1240), (1500, 1560)]
SPEED = 1.0      # 배속 (예: 1.5, 2.0)
WIDTH = None     # 출력 가로 해상도. None이면 원본 유지 (예: 720)
FADE = 0         # 페이드 인/아웃 시간(초). 0이면 없음 (예: 1)
OUTPUT = "edited.mp4"  # 저장할 파일 이름
# ================================================================


# ---------------------------------------------------------------
# 1. 유튜브 다운로드 (yt-dlp)
# ---------------------------------------------------------------
def download_youtube(url: str, out_dir: str = "downloads") -> str:
    import yt_dlp

    os.makedirs(out_dir, exist_ok=True)
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    # 병합되면 확장자가 mp4로 바뀔 수 있음
    base, _ = os.path.splitext(path)
    if os.path.exists(base + ".mp4"):
        path = base + ".mp4"
    print(f"[다운로드 완료] {path}")
    return path


# ---------------------------------------------------------------
# 2. MoviePy 편집 파이프라인
# ---------------------------------------------------------------
def edit_video(src: str) -> str:
    # moviepy 2.x / 1.x 둘 다 대응
    try:
        from moviepy import VideoFileClip, concatenate_videoclips  # moviepy >= 2.0
        MOVIEPY2 = True
    except ImportError:
        from moviepy.editor import VideoFileClip, concatenate_videoclips  # moviepy 1.x
        MOVIEPY2 = False

    clip = VideoFileClip(src)
    print(f"[원본] 길이 {clip.duration:.1f}초, 해상도 {clip.size}")

    # --- 구간 자르기 (여러 구간을 순서대로 이어붙임) ---
    if SEGMENTS:
        parts = []
        for start, end in SEGMENTS:
            end = min(end, clip.duration)
            if start >= end:
                print(f"[경고] 구간 ({start}, {end})은 잘못돼서 건너뜀")
                continue
            part = clip.subclipped(start, end) if MOVIEPY2 else clip.subclip(start, end)
            parts.append(part)
            print(f"[자르기] {start}초 ~ {end}초")
        if not parts:
            raise ValueError("유효한 구간이 없습니다. SEGMENTS를 확인하세요.")
        clip = parts[0] if len(parts) == 1 else concatenate_videoclips(parts)
        print(f"[이어붙임] 총 {clip.duration:.1f}초")

    # --- 배속 ---
    if SPEED != 1.0:
        if MOVIEPY2:
            from moviepy import vfx
            clip = clip.with_effects([vfx.MultiplySpeed(SPEED)])
        else:
            clip = clip.speedx(SPEED)
        print(f"[배속] x{SPEED}")

    # --- 리사이즈 ---
    if WIDTH:
        clip = clip.resized(width=WIDTH) if MOVIEPY2 else clip.resize(width=WIDTH)
        print(f"[리사이즈] 가로 {WIDTH}px")

    # --- 페이드 인/아웃 ---
    if FADE > 0:
        if MOVIEPY2:
            from moviepy import vfx
            clip = clip.with_effects([vfx.FadeIn(FADE), vfx.FadeOut(FADE)])
        else:
            clip = clip.fadein(FADE).fadeout(FADE)
        print(f"[페이드] {FADE}초")

    # --- 저장 ---
    clip.write_videofile(OUTPUT, codec="libx264", audio_codec="aac")
    clip.close()
    return OUTPUT


# ---------------------------------------------------------------
if __name__ == "__main__":
    src = download_youtube(URL)
    out = edit_video(src)
    print(f"\n✅ 완성! -> {os.path.abspath(out)}")
