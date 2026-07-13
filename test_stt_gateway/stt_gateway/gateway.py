import os
from pydub import AudioSegment
from .providers.groq_client import GroqClient

current_dir = os.path.dirname(os.path.abspath(__file__))
bin_dir = os.path.join(current_dir, "bin")

os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

class STTGateway:
    def __init__(self, provider: str):
        self.provider = provider.lower()

        if self.provider == "groq":
            self.client = GroqClient()
        else:
            raise ValueError(f"지원하지 않는 STT 공급자입니다: {provider}")

    def request(self, file_path: str, speed_factor: float = 1.0, **kwargs) -> dict:
        target_file_path = file_path
        temp_file_path = None

        try:
            if speed_factor != 1.0:
                audio = AudioSegment.from_file(file_path)

                new_sample_rate = int(audio.frame_rate * speed_factor)
                fast_audio = audio._spawn(audio.raw_data, overrides={'frame_rate': new_sample_rate})
                fast_audio = fast_audio.set_frame_rate(audio.frame_rate)

                src_dir = os.path.dirname(file_path)
                temp_file_path = os.path.join(src_dir, f"temp_{os.path.basename(file_path)}")
                
                fast_audio.export(temp_file_path, format="mp3")
                target_file_path = temp_file_path

            return self.client.transcribe(target_file_path, config=kwargs)

        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
