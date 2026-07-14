from .modules.auto_scripter import AutoScripter
from .modules.stt_scripter import STTScripter

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
        return self.client.process(video_url, target_dir, **kwargs)
