"""로컬 AVE 클라이언트 진입점."""

from __future__ import annotations

import ctypes
import logging
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import pystray
import requests
import uvicorn
from PIL import Image, ImageDraw, ImageFont


HOST = "127.0.0.1"
PORT = 8000
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "client.log"


def _create_icon_image() -> Image.Image:
    """웹 파비콘과 동일한 AVE 워드마크를 트레이용 래스터로 만든다."""

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, 63, 63), radius=12, fill="#1f2937")
    try:
        font = ImageFont.truetype("arialbd.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    bounds = draw.textbbox((0, 0), "AVE", font=font)
    draw.text(((64 - (bounds[2] - bounds[0])) / 2, (64 - (bounds[3] - bounds[1])) / 2 - bounds[1]), "AVE", font=font, fill="#f97316")
    return image


def _wait_for_local_server(timeout_seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _open_web_ui() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}")


def _open_logs() -> None:
    LOG_PATH.touch(exist_ok=True)
    subprocess.Popen(
        ["cmd", "/k", "powershell", "-NoExit", "-Command", f"Get-Content -Path '{LOG_PATH}' -Wait"],
        cwd=PROJECT_ROOT,
    )


def _stop(icon: pystray.Icon, server: uvicorn.Server) -> None:
    result = ctypes.windll.user32.MessageBoxW(
        None,
        "진행 중인 작업을 취소하고 AVE 클라이언트를 종료할까요?",
        "AVE 클라이언트 종료",
        0x24,  # MB_YESNO | MB_ICONQUESTION
    )
    if result != 6:  # IDYES
        return
    try:
        requests.post(f"http://{HOST}:{PORT}/api/youtube/edit/cancel-active", timeout=15)
    except requests.RequestException:
        logging.getLogger(__name__).warning("종료 전 작업 취소 요청을 전송하지 못했습니다.", exc_info=True)
    server.should_exit = True
    icon.stop()


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
        force=True,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            "app.main:app",
            host=HOST,
            port=PORT,
            log_level="info",
            access_log=True,
            log_config=None,
        )
    )
    threading.Thread(target=server.run, name="ave-local-api", daemon=True).start()
    if not _wait_for_local_server():
        raise RuntimeError("로컬 AVE 서버를 시작하지 못했습니다. client.log를 확인하세요.")

    icon = pystray.Icon(
        "ave-client",
        _create_icon_image(),
        "AVE 클라이언트",
        menu=pystray.Menu(
            pystray.MenuItem("웹 UI 열기", _open_web_ui, default=True),
            pystray.MenuItem("서버 로그 열기", _open_logs),
            pystray.MenuItem("종료", lambda tray: _stop(tray, server)),
        ),
    )
    _open_web_ui()
    icon.run()
