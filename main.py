from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import re
import urllib.request
import urllib.parse
import json
import xml.etree.ElementTree as ET

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

API_SECRET = os.environ.get("API_SECRET", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/transcript/{video_id}")
def get_transcript(video_id: str, lang: str = "ja", secret: str = ""):
    if API_SECRET and secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not re.match(r"^[\w-]{11}$", video_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")

    # 方法1: 動画ページからytInitialPlayerResponseを取得して字幕URLを抽出
    text = try_via_page_scrape(video_id, lang)
    if text:
        return {"videoId": video_id, "lang": lang, "text": text}

    # 方法2: YouTube Data API で字幕トラック情報を取得
    if YOUTUBE_API_KEY:
        text = try_via_data_api(video_id, lang)
        if text:
            return {"videoId": video_id, "lang": lang, "text": text}

    # 方法3: timedtext API を直接試行
    text = try_timedtext_direct(video_id, lang)
    if text:
        return {"videoId": video_id, "lang": lang, "text": text}

    raise HTTPException(status_code=404, detail="No subtitles found")


def try_via_page_scrape(video_id: str, lang: str) -> str | None:
    """動画ページのHTMLからytInitialPlayerResponseを抽出して字幕URLを取得"""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")

        match = re.search(r'ytInitialPlayerResponse\s*=\s*(\{.+?\});', html)
        if not match:
            return None

        data = json.loads(match.group(1))
        tracks = data.get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
        if not tracks:
            return None

        # 指定言語の字幕トラックを探す
        caption_url = None
        for track in tracks:
            if track.get("languageCode") == lang:
                caption_url = track.get("baseUrl")
                break
        if not caption_url:
            caption_url = tracks[0].get("baseUrl")

        if not caption_url:
            return None

        # fmt=srv3 でXML形式の字幕を取得
        if "fmt=" not in caption_url:
            caption_url += "&fmt=srv3"

        return download_caption_xml(caption_url)
    except Exception:
        return None


def try_via_data_api(video_id: str, lang: str) -> str | None:
    """YouTube Data APIでcaptionsリストを取得し、timedtext APIで字幕をダウンロード"""
    try:
        api_url = f"https://www.googleapis.com/youtube/v3/captions?part=snippet&videoId={video_id}&key={YOUTUBE_API_KEY}"
        req = urllib.request.Request(api_url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        items = data.get("items", [])
        if not items:
            return None

        # 指定言語のトラックを探す
        target = None
        for item in items:
            snippet = item.get("snippet", {})
            if snippet.get("language") == lang:
                target = snippet
                break
        if not target:
            target = items[0].get("snippet", {})

        # timedtext APIで字幕を取得
        tt_url = f"https://www.youtube.com/api/timedtext?v={video_id}&lang={target.get('language', lang)}&fmt=srv3"
        if target.get("trackKind") == "asr":
            tt_url += "&kind=asr"
        if target.get("name"):
            tt_url += f"&name={urllib.parse.quote(target['name'])}"

        return download_caption_xml(tt_url)
    except Exception:
        return None


def try_timedtext_direct(video_id: str, lang: str) -> str | None:
    """timedtext APIを直接叩く"""
    urls = [
        f"https://www.youtube.com/api/timedtext?v={video_id}&lang={lang}&fmt=srv3&kind=asr",
        f"https://www.youtube.com/api/timedtext?v={video_id}&lang={lang}&fmt=srv3",
    ]
    for url in urls:
        text = download_caption_xml(url)
        if text:
            return text
    return None


def download_caption_xml(url: str) -> str | None:
    """字幕XMLをダウンロードしてテキストに変換"""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_text = resp.read().decode("utf-8")

        if not xml_text or len(xml_text) < 50:
            return None
        if "automated queries" in xml_text:
            return None

        # XML をパース
        try:
            root = ET.fromstring(xml_text)
            texts = []
            for elem in root.iter():
                if elem.text and elem.text.strip():
                    clean = elem.text.strip()
                    clean = clean.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                    clean = clean.replace("&#39;", "'").replace("&quot;", '"')
                    if clean and clean != "\n":
                        texts.append(clean)
            if texts:
                return " ".join(texts)
        except ET.ParseError:
            pass

        # フォールバック: 正規表現でテキスト抽出
        import re as regex
        texts = []
        for m in regex.finditer(r'>([^<]+)<', xml_text):
            t = m.group(1).strip()
            if t and t != "\n":
                texts.append(t)
        return " ".join(texts) if texts else None

    except Exception:
        return None
