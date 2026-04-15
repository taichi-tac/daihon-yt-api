from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import tempfile
import json
import os
import re
import glob

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

API_SECRET = os.environ.get("API_SECRET", "")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/transcript/{video_id}")
def get_transcript(video_id: str, lang: str = "ja", secret: str = ""):
    if API_SECRET and secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not re.match(r"^[\w-]{11}$", video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")

    url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = os.path.join(tmpdir, "%(id)s")

        cmd = [
            "yt-dlp",
            "--impersonate", "chrome",
            "--write-auto-sub",
            "--write-sub",
            "--sub-lang", lang,
            "--sub-format", "json3",
            "--skip-download",
            "--no-check-formats",
            "--ignore-errors",
            "-o", out_template,
            url,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=50,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Timeout fetching transcript")

        # json3ファイルを探す
        json3_files = glob.glob(os.path.join(tmpdir, f"*.{lang}.json3"))
        if not json3_files:
            # エラー詳細を返す
            error_msg = result.stderr.strip() if result.stderr else "No subtitles found"
            raise HTTPException(status_code=404, detail=error_msg[:500])

        with open(json3_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        segments = []
        for event in data.get("events", []):
            segs = event.get("segs", [])
            text = "".join(s.get("utf8", "") for s in segs).strip()
            if text and text != "\n":
                segments.append({
                    "text": text,
                    "offset": event.get("tStartMs", 0),
                    "duration": event.get("dDurationMs", 0),
                })

        if not segments:
            raise HTTPException(status_code=404, detail="Empty subtitles")

        return {
            "videoId": video_id,
            "lang": lang,
            "segments": segments,
            "text": " ".join(s["text"] for s in segments),
        }
