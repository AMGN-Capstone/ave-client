import os
from .modules.auto_scripter import AutoScripter
from .modules.stt_scripter import STTScripter
from .modules.auto_scripter_post import AutoScripterPostProcessor

class Scripter:
    def __init__(self, method: str):
        self.method = method.lower()
        
        if self.method == "auto":
            self.client = AutoScripter()
        elif self.method == "stt":
            self.client = STTScripter()
        else:
            raise ValueError(f"지원하지 않는 방식입니다: {method}")

    def process(self, video_url: str, target_dir: str, **kwargs) -> dict:
        result = self.client.process(video_url, target_dir, **kwargs)
        
        if not result or "error" in result or not result.get("success"):
            return result or {"error": "결과 값이 반환되지 않았습니다."}
            
        if self.method == "auto":
            video_id = result.get("video_id")
            lang = kwargs.get("lang")
            
            txt_filepath = os.path.join(target_dir, f"{video_id}_{lang}.txt")
            csv_filepath = os.path.join(target_dir, f"{video_id}_{lang}.csv")
            
            post_processor = AutoScripterPostProcessor()
            post_result = post_processor.process(txt_filepath, csv_filepath)
            
            if post_result.get("success"):
                result["message"] += " / CSV 후처리 완료"
                result["csv_path"] = post_result["csv_path"]
                result["row_count"] = post_result["row_count"]
            else:
                result["error"] = post_result.get("error")
                
        elif self.method == "stt":
            pass
            
        return result
