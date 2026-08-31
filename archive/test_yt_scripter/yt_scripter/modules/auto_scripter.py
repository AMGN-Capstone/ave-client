import os
import subprocess

class AutoScripter:
    def __init__(self):
        current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.bin_dir = os.path.join(current_dir, "bin")
        
        os.environ["PATH"] = self.bin_dir + os.pathsep + os.environ.get("PATH", "")
        
        self.ytdlp_path = os.path.join(self.bin_dir, "yt-dlp.exe")

    def process(self, video_url: str, target_dir: str, lang: str = "ko") -> dict:
        if not os.path.exists(self.ytdlp_path):
            self.ytdlp_path = "yt-dlp"

        try:
            id_cmd = [self.ytdlp_path, "--print", "id", video_url]
            id_result = subprocess.run(id_cmd, capture_output=True, text=True, check=True)
            video_id = id_result.stdout.strip()

            if not video_id:
                return {"error": "비디오 ID를 추출하지 못했습니다."}

            target_file = os.path.join(target_dir, f"{video_id}_{lang}.txt")

            target_template = os.path.join(target_dir, f"{video_id}_auto.%(ext)s")
            
            dl_cmd = [
                self.ytdlp_path,
                "--write-auto-subs",
                "--skip-download",
                "--sub-langs", lang,
                "--convert-subs", "vtt",
                "-o", target_template,
                video_url
            ]
            
            subprocess.run(dl_cmd, capture_output=True, text=True, check=True)

            downloaded_file = os.path.join(target_dir, f"{video_id}_auto.{lang}.vtt")
            
            if os.path.exists(downloaded_file):
                os.replace(downloaded_file, target_file)
            
            return {
                "success": True,
                "video_id": video_id,
                "message": "자동 생성 스크립트 다운로드 완료"
            }
            
        except subprocess.CalledProcessError as e:
            return {"error": f"명령어 실행 실패: {e.stderr}"}
        except Exception as e:
            return {"error": f"알 수 없는 오류 발생: {e}"}
