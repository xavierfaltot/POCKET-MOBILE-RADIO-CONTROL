#!/usr/bin/env python3
"""POCKET MOBILE RADIO CONTROL — push-to-talk ingest service.

Receives raw signed 16-bit little-endian mono PCM from the browser and
publishes it as a temporary Icecast source mount (/pocket-talk.mp3) via ffmpeg.

Run this service on localhost:8093 and let nginx proxy only
/pocket-control/api/talk/* to it. Existing CURRENT/NEXT backend can stay intact.
"""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = os.environ.get("POCKET_TALK_HOST", "127.0.0.1")
PORT = int(os.environ.get("POCKET_TALK_PORT", "8093"))
ICECAST_HOST = os.environ.get("ICECAST_HOST", "127.0.0.1")
ICECAST_PORT = int(os.environ.get("ICECAST_PORT", "8000"))
ICECAST_MOUNT = os.environ.get("ICECAST_TALK_MOUNT", "pocket-talk.mp3").lstrip("/")
ICECAST_USER = os.environ.get("ICECAST_SOURCE_USER", "source")
ICECAST_PASSWORD = os.environ.get("ICECAST_SOURCE_PASSWORD", "")
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")

_lock = threading.Lock()
_proc = None
_sample_rate = 48000
_channels = 1


def _target_url():
    # Credentials are used only locally by ffmpeg and never returned by the API.
    return f"icecast://{ICECAST_USER}:{ICECAST_PASSWORD}@{ICECAST_HOST}:{ICECAST_PORT}/{ICECAST_MOUNT}"


def _stop_locked():
    global _proc
    p = _proc
    _proc = None
    if not p:
        return
    try:
        if p.stdin:
            p.stdin.close()
    except Exception:
        pass
    try:
        p.wait(timeout=1.5)
    except Exception:
        try:
            p.terminate()
            p.wait(timeout=1.0)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def start(sample_rate=48000, channels=1):
    global _proc, _sample_rate, _channels
    if not ICECAST_PASSWORD:
        raise RuntimeError("ICECAST_SOURCE_PASSWORD is not configured")
    sample_rate = int(sample_rate or 48000)
    channels = int(channels or 1)
    if sample_rate < 8000 or sample_rate > 192000:
        raise ValueError("invalid sample rate")
    if channels != 1:
        raise ValueError("only mono PCM is accepted")

    with _lock:
        _stop_locked()
        _sample_rate, _channels = sample_rate, channels
        cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels), "-i", "pipe:0",
            "-af", "highpass=f=90,lowpass=f=12000,acompressor=threshold=-18dB:ratio=3:attack=10:release=120",
            "-ac", "1", "-ar", "44100", "-codec:a", "libmp3lame", "-b:a", "128k",
            "-content_type", "audio/mpeg", "-f", "mp3", _target_url(),
        ]
        _proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, bufsize=0)
        if _proc.poll() is not None:
            err = (_proc.stderr.read() or b"").decode("utf-8", "replace")[-1000:]
            _proc = None
            raise RuntimeError(f"ffmpeg failed to start: {err}")


def write_pcm(data):
    with _lock:
        if not _proc or _proc.poll() is not None or not _proc.stdin:
            raise RuntimeError("talk session is not active")
        _proc.stdin.write(data)
        _proc.stdin.flush()


def stop():
    with _lock:
        _stop_locked()


def active():
    with _lock:
        return bool(_proc and _proc.poll() is None)


class Handler(BaseHTTPRequestHandler):
    server_version = "PocketTalk/1.0"

    def log_message(self, fmt, *args):
        print("[pocket-talk]", fmt % args, flush=True)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sample-Rate, X-Channels")
        self.send_header("Cache-Control", "no-store")

    def _json(self, code, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path.endswith("/api/talk/status") or path == "/health":
            return self._json(200, {"ok": True, "talking": active(), "mount": "/" + ICECAST_MOUNT})
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path.endswith("/api/talk/start"):
                n = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(n) if n else b"{}"
                cfg = json.loads(body or b"{}")
                start(cfg.get("sampleRate", 48000), cfg.get("channels", 1))
                return self._json(200, {"ok": True, "talking": True, "mount": "/" + ICECAST_MOUNT})

            if path.endswith("/api/talk/pcm"):
                n = int(self.headers.get("Content-Length", "0") or 0)
                if n <= 0 or n > 1024 * 1024:
                    return self._json(400, {"ok": False, "error": "invalid PCM body"})
                data = self.rfile.read(n)
                write_pcm(data)
                return self._json(200, {"ok": True, "bytes": len(data)})

            if path.endswith("/api/talk/stop"):
                stop()
                return self._json(200, {"ok": True, "talking": False})

            self._json(404, {"ok": False, "error": "not found"})
        except BrokenPipeError:
            stop()
            self._json(502, {"ok": False, "error": "ffmpeg pipe closed"})
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)[:300]})


def main():
    print(f"POCKET TALK listening on http://{HOST}:{PORT}", flush=True)
    print(f"Icecast mount: /{ICECAST_MOUNT}", flush=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        httpd.serve_forever()
    finally:
        stop()


if __name__ == "__main__":
    main()
