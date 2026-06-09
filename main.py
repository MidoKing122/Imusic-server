from flask import Flask, jsonify, request
import yt_dlp
import re
import time

app = Flask(__name__)

# ══════════════════════════════════════════════════════
#  Cache
# ══════════════════════════════════════════════════════
_cache: dict = {}
_AUDIO_TTL   = 4 * 60 * 60   # 4 ساعات
_SEARCH_TTL  = 5 * 60         # 5 دقايق
_INFO_TTL    = 30 * 60        # 30 دقيقة

def _cache_get(key, ttl):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["data"]
    _cache.pop(key, None)
    return None

def _cache_set(key, data):
    if len(_cache) > 500:
        for k in sorted(_cache, key=lambda k: _cache[k]["ts"])[:100]:
            del _cache[k]
    _cache[key] = {"ts": time.time(), "data": data}

def _cache_delete(key):
    _cache.pop(key, None)

# ══════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════
def clean_artist(name):
    if not name: return "Unknown"
    name = re.sub(r'\s*-\s*[Tt]opic$', '', name)
    name = re.sub(r'(?i)vevo$', '', name)
    return name.strip().rstrip('-').strip() or "Unknown"

# ══════════════════════════════════════════════════════
#  yt-dlp opts — بنجرب clients بالترتيب
#  الحل الأساسي للـ "Sign in to confirm you're not a bot"
#  هو استخدام po_token + visitor_data مع web client
# ══════════════════════════════════════════════════════

def _make_opts(client: str) -> dict:
    base = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 25,
        "retries": 2,
        "prefer_ffmpeg": False,
        "postprocessors": [],
        "geo_bypass": True,
        "geo_bypass_country": "EG",
    }

    if client == "ios":
        return {**base,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "http_headers": {
                "User-Agent": "com.google.ios.youtube/19.29.1 CFNetwork/1408.0.4 Darwin/22.5.0",
                "X-YouTube-Client-Name": "5",
                "X-YouTube-Client-Version": "19.29.1",
            },
            "extractor_args": {"youtube": {
                "player_client": ["ios"],
                "player_skip": ["configs", "webpage"],
            }},
        }

    if client == "android":
        return {**base,
            "format": "bestaudio/best",
            "http_headers": {
                "User-Agent": "com.google.android.youtube/17.36.4 (Linux; U; Android 12) gzip",
                "X-YouTube-Client-Name": "3",
                "X-YouTube-Client-Version": "17.36.4",
            },
            "extractor_args": {"youtube": {
                "player_client": ["android"],
                "player_skip": ["configs", "webpage"],
            }},
        }

    if client == "tv":
        return {**base,
            "format": "bestaudio/best",
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebKit/538.1 Chrome/92.0.4515.166 TV Safari/538.1",
            },
            "extractor_args": {"youtube": {
                "player_client": ["tv_embedded"],
                "player_skip": ["configs"],
            }},
        }

    if client == "mweb":
        # ✅ Mobile web — أحياناً بيتجاوز الـ bot detection
        return {**base,
            "format": "bestaudio/best",
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
                "X-YouTube-Client-Name": "2",
                "X-YouTube-Client-Version": "2.20230726.01.00",
                "Origin": "https://m.youtube.com",
                "Referer": "https://m.youtube.com/",
            },
            "extractor_args": {"youtube": {
                "player_client": ["mweb"],
            }},
        }

    if client == "web_creator":
        # ✅ YouTube Studio client — مش بيتطلب تسجيل دخول عادةً
        return {**base,
            "format": "bestaudio/best",
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
                "X-YouTube-Client-Name": "62",
                "X-YouTube-Client-Version": "1.20230726.03.00",
                "Origin": "https://www.youtube.com",
                "Referer": "https://www.youtube.com/",
            },
            "extractor_args": {"youtube": {
                "player_client": ["web_creator"],
                "player_skip": ["webpage"],
            }},
        }

    # default web
    return {**base,
        "format": "bestaudio/best",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
        },
    }


def _extract_url(info: dict) -> str | None:
    """استخرج audio URL من الـ info dict"""
    if info.get("url"):
        return info["url"]

    formats = info.get("formats", [])

    # audio-only أولاً
    audio_only = [f for f in formats
                  if f.get("url")
                  and f.get("acodec") not in (None, "none")
                  and f.get("vcodec") in (None, "none", "")]

    if audio_only:
        best = max(audio_only, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        return best["url"]

    # fallback: أي format فيه audio
    any_audio = [f for f in formats
                 if f.get("url") and f.get("acodec") not in (None, "none")]
    if any_audio:
        return max(any_audio, key=lambda f: f.get("abr") or 0)["url"]

    return None


def _get_audio(video_id: str) -> str | None:
    url_str = f"https://youtube.com/watch?v={video_id}"
    clients = ["ios", "android", "tv", "mweb", "web_creator"]

    for client in clients:
        try:
            print(f"Trying [{client}] for {video_id}")
            with yt_dlp.YoutubeDL(_make_opts(client)) as ydl:
                info = ydl.extract_info(url_str, download=False)
            if not info:
                continue
            url = _extract_url(info)
            if url:
                print(f"✅ [{client}] OK for {video_id}")
                return url
            print(f"[{client}] no URL found")
        except Exception as e:
            err = str(e)
            print(f"❌ [{client}] failed: {err[:120]}")
            # لو الـ error مش bot-related — ما نكملش
            if "not a bot" not in err and "Sign in" not in err and "bot" not in err.lower():
                if "unavailable" in err.lower() or "private" in err.lower():
                    print(f"Video {video_id} unavailable — stopping")
                    return None
            continue

    return None


# ══════════════════════════════════════════════════════
#  Routes
# ══════════════════════════════════════════════════════

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "iMusic API", "version": "3.0"})


@app.route("/audio/<video_id>")
def audio(video_id):
    if len(video_id) != 11:
        return jsonify({"success": False, "error": "Invalid ID"}), 400

    force = request.args.get("force", "0") == "1"
    cache_key = f"audio:{video_id}"

    if force:
        _cache_delete(cache_key)
        print(f"Force refresh: {video_id}")

    cached = _cache_get(cache_key, _AUDIO_TTL)
    if cached:
        print(f"Cache hit: {video_id}")
        return jsonify({"success": True, "url": cached, "cached": True})

    url = _get_audio(video_id)
    if not url:
        return jsonify({"success": False, "error": "No stream found"}), 500

    _cache_set(cache_key, url)
    return jsonify({"success": True, "url": url})


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"success": False, "error": "No query"}), 400

    cache_key = f"search:{q}"
    cached = _cache_get(cache_key, _SEARCH_TTL)
    if cached:
        return jsonify({"success": True, "results": cached})

    try:
        opts = {**_make_opts("ios"),
            "extract_flat": True,
            "format": None,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch15:{q}", download=False)

        results = []
        for e in (info.get("entries") or []):
            if not e or not e.get("id"): continue
            results.append({
                "id":        e["id"],
                "title":     e.get("title", "Unknown"),
                "artist":    clean_artist(e.get("uploader") or e.get("channel") or "Unknown"),
                "duration":  e.get("duration", 0),
                "viewCount": e.get("view_count", 0),
                "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
            })

        _cache_set(cache_key, results)
        return jsonify({"success": True, "results": results})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/info/<video_id>")
def info(video_id):
    if len(video_id) != 11:
        return jsonify({"success": False, "error": "Invalid ID"}), 400

    cache_key = f"info:{video_id}"
    cached = _cache_get(cache_key, _INFO_TTL)
    if cached:
        return jsonify(cached)

    try:
        with yt_dlp.YoutubeDL(_make_opts("ios")) as ydl:
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
        _cache_set(cache_key, resp)
        return jsonify(resp)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/trending")
def trending():
    cached = _cache_get("trending", _SEARCH_TTL)
    if cached:
        return jsonify({"success": True, "results": cached})

    try:
        opts = {**_make_opts("ios"), "extract_flat": True, "format": None}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info("ytsearch15:اغاني عربي 2025", download=False)

        results = []
        for e in (info.get("entries") or []):
            if not e or not e.get("id"): continue
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
