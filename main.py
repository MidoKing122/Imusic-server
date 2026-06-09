from flask import Flask, jsonify, request
import yt_dlp
import re
import time

app = Flask(__name__)

# ── Cache بسيط عشان ما نكررش نفس الطلب ──────────────
_audio_cache: dict = {}
_AUDIO_TTL = 4 * 60 * 60  # 4 ساعات (YouTube URLs بتعيش 6)

def _cache_get(key):
    entry = _audio_cache.get(key)
    if entry and (time.time() - entry["ts"]) < _AUDIO_TTL:
        return entry["data"]
    _audio_cache.pop(key, None)
    return None

def _cache_set(key, data):
    # نظف الـ cache لو كبر
    if len(_audio_cache) > 500:
        oldest = sorted(_audio_cache.keys(), key=lambda k: _audio_cache[k]["ts"])
        for k in oldest[:100]:
            del _audio_cache[k]
    _audio_cache[key] = {"ts": time.time(), "data": data}

def clean_artist(name):
    if not name:
        return "Unknown"
    name = re.sub(r'\s*-\s*[Tt]opic$', '', name)
    name = re.sub(r'(?i)vevo$', '', name)
    return name.strip().rstrip('-').strip() or "Unknown"

# ✅ iOS client — الأكثر نجاحاً مع YouTube بدون cookies
_IOS_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "socket_timeout": 20,
    "retries": 3,
    "format": "bestaudio[ext=m4a]/bestaudio[abr<=128]/bestaudio/best",
    "http_headers": {
        # ✅ iOS YouTube app headers — بتخدع YouTube
        "User-Agent": "com.google.ios.youtube/19.29.1 CFNetwork/1408.0.4 Darwin/22.5.0",
        "X-YouTube-Client-Name": "5",
        "X-YouTube-Client-Version": "19.29.1",
    },
    "extractor_args": {
        "youtube": {
            # ✅ iOS client بيشتغل بدون cookies في معظم الأحيان
            "player_client": ["ios"],
            "player_skip": ["configs", "webpage"],
        }
    },
    "prefer_ffmpeg": False,
    "postprocessors": [],
}

# ✅ Android client — fallback لو iOS فشل
_ANDROID_OPTS = {
    **_IOS_OPTS,
    "http_headers": {
        "User-Agent": "com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip",
        "X-YouTube-Client-Name": "3",
        "X-YouTube-Client-Version": "17.36.4",
    },
    "extractor_args": {
        "youtube": {
            "player_client": ["android"],
            "player_skip": ["configs", "webpage"],
        }
    },
}

# ✅ TV client — fallback ثالث، مش محتاج cookies عادةً
_TV_OPTS = {
    **_IOS_OPTS,
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebKit/538.1 Chrome/92.0.4515.166 TV Safari/538.1",
    },
    "extractor_args": {
        "youtube": {
            "player_client": ["tv_embedded"],
            "player_skip": ["configs"],
        }
    },
}

def _extract_audio_url(video_id: str) -> str | None:
    """جرب clients بالترتيب: iOS → Android → TV"""
    url_str = f"https://youtube.com/watch?v={video_id}"

    for client_name, opts in [("ios", _IOS_OPTS), ("android", _ANDROID_OPTS), ("tv", _TV_OPTS)]:
        try:
            print(f"Trying {client_name} client for {video_id}...")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url_str, download=False)

            if not info:
                continue

            # Direct URL
            if info.get("url"):
                print(f"✅ {client_name} got direct URL")
                return info["url"]

            # من الـ formats
            audio_fmts = [
                f for f in info.get("formats", [])
                if f.get("url")
                and f.get("acodec") not in (None, "none")
                and f.get("vcodec") in (None, "none", "")  # audio only
            ]

            if not audio_fmts:
                # fallback: أي format عنده audio
                audio_fmts = [
                    f for f in info.get("formats", [])
                    if f.get("url") and f.get("acodec") not in (None, "none")
                ]

            if audio_fmts:
                # اختار أحسن جودة متاحة
                best = max(audio_fmts, key=lambda f: f.get("abr") or f.get("tbr") or 0)
                print(f"✅ {client_name} got format: {best.get('ext')} {best.get('abr', '?')}kbps")
                return best["url"]

        except Exception as e:
            print(f"❌ {client_name} failed: {e}")
            continue

    return None

# ══════════════════════════════════════════════════════
#  Routes
# ══════════════════════════════════════════════════════

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "iMusic API", "version": "2.0"})

@app.route("/audio/<video_id>")
def audio(video_id):
    if len(video_id) != 11:
        return jsonify({"success": False, "error": "Invalid ID"}), 400

    # فحص الـ cache أولاً
    cached = _cache_get(f"audio:{video_id}")
    if cached:
        print(f"Cache hit: {video_id}")
        return jsonify({"success": True, "url": cached, "cached": True})

    url = _extract_audio_url(video_id)

    if not url:
        return jsonify({"success": False, "error": "No stream found"}), 500

    _cache_set(f"audio:{video_id}", url)
    return jsonify({"success": True, "url": url})

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"success": False, "error": "No query"}), 400

    # Cache للبحث
    cached = _cache_get(f"search:{q}")
    if cached:
        return jsonify({"success": True, "results": cached})

    try:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "socket_timeout": 15,
            "http_headers": {
                "User-Agent": "com.google.ios.youtube/19.29.1 CFNetwork/1408.0.4 Darwin/22.5.0",
            },
            "extractor_args": {
                "youtube": {"player_client": ["ios"]}
            },
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch15:{q}", download=False)

        results = []
        for e in (info.get("entries") or []):
            if not e or not e.get("id"):
                continue
            results.append({
                "id":        e["id"],
                "title":     e.get("title", "Unknown"),
                "artist":    clean_artist(e.get("uploader") or e.get("channel") or "Unknown"),
                "duration":  e.get("duration", 0),
                "viewCount": e.get("view_count", 0),
                "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
            })

        _cache_set(f"search:{q}", results)
        return jsonify({"success": True, "results": results})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/info/<video_id>")
def info(video_id):
    if len(video_id) != 11:
        return jsonify({"success": False, "error": "Invalid ID"}), 400

    cached = _cache_get(f"info:{video_id}")
    if cached:
        return jsonify(cached)

    try:
        opts = {
            "quiet": True,
            "skip_download": True,
            "socket_timeout": 15,
            "http_headers": {
                "User-Agent": "com.google.ios.youtube/19.29.1 CFNetwork/1408.0.4 Darwin/22.5.0",
            },
            "extractor_args": {
                "youtube": {
                    "player_client": ["ios"],
                    "player_skip": ["configs", "webpage"],
                }
            },
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(
                f"https://youtube.com/watch?v={video_id}", download=False
            )

        artist = clean_artist(
            data.get("artist") or data.get("channel") or data.get("uploader") or "Unknown"
        )
        resp = {
            "success":   True,
            "id":        video_id,
            "title":     data.get("title", "Unknown"),
            "artist":    artist,
            "duration":  data.get("duration", 0),
            "viewCount": data.get("view_count", 0),
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        }
        _cache_set(f"info:{video_id}", resp)
        return jsonify(resp)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/trending")
def trending():
    cached = _cache_get("trending")
    if cached:
        return jsonify({"success": True, "results": cached})

    try:
        opts = {
            "quiet": True,
            "extract_flat": True,
            "socket_timeout": 15,
            "http_headers": {
                "User-Agent": "com.google.ios.youtube/19.29.1 CFNetwork/1408.0.4 Darwin/22.5.0",
            },
            "extractor_args": {
                "youtube": {"player_client": ["ios"]}
            },
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info("ytsearch15:اغاني عربي 2025", download=False)

        results = []
        for e in (info.get("entries") or []):
            if not e or not e.get("id"):
                continue
            results.append({
                "id":        e["id"],
                "title":     e.get("title", "Unknown"),
                "artist":    clean_artist(e.get("uploader") or "Unknown"),
                "duration":  e.get("duration", 0),
                "viewCount": e.get("view_count", 0),
                "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
            })

        _cache_set("trending", results)
        return jsonify({"success": True, "results": results})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
