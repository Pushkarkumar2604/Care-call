"""Maintains a persistent, auto-reconnecting public SSH tunnel to 127.0.0.1:8000 and writes the live public URL to tunnel.txt."""
import re
import subprocess
import sys
import time
from pathlib import Path

TUNNEL_FILE = Path(__file__).resolve().parent / "tunnel.txt"


def run_tunnel():
    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=3",
        "-R", "80:127.0.0.1:8000",
        "nokey@localhost.run"
    ]
    print("[tunnel] Starting auto-reconnecting tunnel...")
    while True:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            for line in proc.stdout:
                line_str = line.strip()
                if "lhr.life" in line_str:
                    match = re.search(r"https://[a-zA-Z0-9\-]+\.lhr\.life", line_str)
                    if match:
                        url = match.group(0)
                        TUNNEL_FILE.write_text(url)
                        print(f"[tunnel] Live public URL: {url}")
            proc.wait()
            print("[tunnel] Process exited, reconnecting in 2s...")
        except Exception as e:
            print("[tunnel] Error:", e)
        time.sleep(2)


if __name__ == "__main__":
    run_tunnel()
