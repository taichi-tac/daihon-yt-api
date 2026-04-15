from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import os
import re

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

    ydl_opts = {
        "skip_download": True,
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "json3",
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # 手動字幕を優先、なければ自動字幕
            subs = info.get("subtitles", {})
            auto_subs = info.get("automatic_captions", {})

            tracks = subs.get(lang) or auto_subs.get(lang)
            if not tracks:
                # 言語フォールバック
                tracks = subs.get(lang[:2]) or auto_subs.get(lang[:2])
            if not tracks:
                raise HTTPException(status_code=404, detail="No subtitles found")

            # json3 形式のURLを取得してダウンロード
            json3_url = None
            for t in tracks:
                if t.get("ext") == "json3":
                    json3_url = t["url"]
                    break
            if not json3_url:
                json3_url = tracks[0]["url"]

            import urllib.request
            import json

            req = urllib.request.Request(json3_url)
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())

            # json3形式からテキスト抽出
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

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
