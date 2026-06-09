from flask import Flask, jsonify, request
import yt_dlp
import re

app = Flask(__name__)

def clean_artist(name):
    if not name:
        return "Unknown"
    name = re.sub(r'\s*-\s*[Tt]opic$', '', name)
    name = re.sub(r'(?i)vevo$', '', name)
    return name.strip().rstrip('-').strip() or "Unknown"

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "iMusic API"})

@app.route("/audio/<video_id>")
def audio(video_id):
    if len(video_id) != 11:
        return jsonify({"success": False, "error": "Invalid ID"}), 400
    try:
        opts = {
            "quiet": True,
            "skip_download": True,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "extractor_args": {
                "youtube": {
                    "player_client": ["ios"],
                    "player_skip": ["configs", "webpage"],
                }
            },
            "http_headers": {
                "User-Agent": "com.google.ios.youtube/19.29.1 CFNetwork/1408.0.4 Darwin/22.5.0",
            },
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(
                f"https://youtube.com/watch?v={video_id}",
                download=False
            )
        url = info.get("url")
        if not url:
            audio_fmts = [
                f for f in info.get("formats", [])
                if f.get("url") and f.get("acodec") not in (None, "none")
            ]
            if audio_fmts:
                url = min(audio_fmts, key=lambda f: f.get("abr") or 999)["url"]
        if not url:
            return jsonify({"success": False, "error": "No stream found"}), 404
        return jsonify({"success": True, "url": url})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"success": False, "error": "No query"}), 400
    try:
        opts = {
            "quiet": True,
            "extract_flat": True,
            "default_search": f"ytsearch15:{q}",
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(q if "youtube.com" in q else f"ytsearch15:{q}", download=False)
        results = []
        for e in info.get("entries", []):
            if not e.get("id"):
                continue
            results.append({
                "id": e["id"],
                "title": e.get("title", "Unknown"),
                "artist": clean_artist(e.get("uploader") or e.get("channel") or "Unknown"),
                "duration": e.get("duration", 0),
                "viewCount": e.get("view_count", 0),
                "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
            })
        return jsonify({"success": True, "results": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/info/<video_id>")
def info(video_id):
    if len(video_id) != 11:
        return jsonify({"success": False, "error": "Invalid ID"}), 400
    try:
        opts = {
            "quiet": True,
            "skip_download": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(
                f"https://youtube.com/watch?v={video_id}",
                download=False
            )
        artist = clean_artist(data.get("artist") or data.get("channel") or data.get("uploader") or "Unknown")
        return jsonify({
            "success": True,
            "id": video_id,
            "title": data.get("title", "Unknown"),
            "artist": artist,
            "duration": data.get("duration", 0),
            "viewCount": data.get("view_count", 0),
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/trending")
def trending():
    try:
        opts = {
            "quiet": True,
            "extract_flat": True,
            "default_search": "ytsearch15:اغاني 2025",
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info("ytsearch15:اغاني 2025", download=False)
        results = []
        for e in info.get("entries", []):
            if not e.get("id"):
                continue
            results.append({
                "id": e["id"],
                "title": e.get("title", "Unknown"),
                "artist": clean_artist(e.get("uploader") or "Unknown"),
                "duration": e.get("duration", 0),
                "viewCount": e.get("view_count", 0),
                "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
            })
        return jsonify({"success": True, "results": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)