"""로컬 AVE 클라이언트 진입점."""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import pystray
import uvicorn
from PIL import Image, ImageDraw


HOST = "127.0.0.1"
PORT = 8000
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "client.log"


def _create_icon_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), "#111827")
    draw = ImageDraw.Draw(image)
    draw.polygon([(14, 12), (52, 32), (14, 52)], fill="#38bdf8")
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
