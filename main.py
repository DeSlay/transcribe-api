# main.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import groq
import os
import glob
import uuid
import base64
import traceback
import instaloader

app = Flask(__name__)
CORS(app)
client = groq.Groq(api_key=os.environ["GROQ_API_KEY"])

# Décode les cookies YouTube
_yt_b64 = os.environ.get("YOUTUBE_COOKIES_B64")
if _yt_b64:
    with open("/tmp/yt_cookies.txt", "wb") as _f:
        _f.write(base64.b64decode(_yt_b64))

# Décode les cookies Instagram
_ig_b64 = os.environ.get("INSTAGRAM_COOKIES_B64")
if _ig_b64:
    with open("/tmp/ig_cookies.txt", "wb") as _f:
        _f.write(base64.b64decode(_ig_b64))

COOKIES_FILE = "/tmp/yt_cookies.txt" if os.path.exists("/tmp/yt_cookies.txt") else None
IG_COOKIES_FILE = "/tmp/ig_cookies.txt" if os.path.exists("/tmp/ig_cookies.txt") else None

@app.route("/transcribe", methods=["POST"])
def transcribe():
    audio_path = None
    try:
        data = request.get_json(force=True)
        if not data or not data.get("url"):
            return jsonify({"error": "url manquante"}), 400

        url = data["url"].strip()

        # Fichier unique par requête pour éviter les conflits
        unique_id = str(uuid.uuid4())[:8]
        output_template = f"/tmp/audio_{unique_id}.%(ext)s"

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "cookiefile": IG_COOKIES_FILE if "instagram.com" in url else COOKIES_FILE,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            "extractor_args": {
                "tiktok": {"webpage_download": ["1"]},
            },
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            return jsonify({"error": f"Téléchargement échoué : {str(e)}"}), 422

        # Trouve le fichier téléchargé
        files = glob.glob(f"/tmp/audio_{unique_id}.*")
        if not files:
            return jsonify({"error": "Fichier audio introuvable après téléchargement"}), 422

        audio_path = files[0]

        # Limite 25 MB (Groq Whisper)
        size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        if size_mb > 25:
            return jsonify({"error": f"Fichier audio trop grand ({size_mb:.1f} MB, max 25 MB)"}), 422

        with open(audio_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(audio_path), f),
                model="whisper-large-v3",
            )

        return jsonify({"transcription": transcription.text})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        # Nettoyage systématique
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception:
                pass


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/profile-videos", methods=["POST"])
def profile_videos():
    data = request.get_json(force=True)
    if not data or not data.get("url"):
        return jsonify({"error": "url manquante"}), 400

    url = data["url"].strip()

    if "instagram.com" in url:
        return _fetch_instagram(url)
    else:
        return _fetch_youtube(url)


def _fetch_instagram(url):
    try:
        # Extrait le username depuis l'URL
        username = url.rstrip("/").split("/")[-1].lstrip("@")
        if not username:
            return jsonify({"error": "Username Instagram introuvable dans l'URL"}), 400

        L = instaloader.Instaloader(quiet=True, download_pictures=False,
                                     download_videos=False, download_video_thumbnails=False)
        profile = instaloader.Profile.from_username(L.context, username)

        videos = []
        for post in profile.get_posts():
            if post.is_video:
                videos.append({
                    "id": post.shortcode,
                    "title": (post.caption[:80] if post.caption else "Sans titre"),
                    "url": f"https://www.instagram.com/p/{post.shortcode}/",
                    "thumbnail": post.url,
                    "duration": post.video_duration,
                })
            if len(videos) >= 20:
                break

        return jsonify({"videos": videos, "platform": "instagram"})
    except Exception as e:
        return jsonify({"error": f"Instagram : {str(e)}"}), 500


def _fetch_youtube(url):
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "cookiefile": COOKIES_FILE,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        entries = info.get("entries", [info])
        videos = [
            {
                "id": e.get("id", ""),
                "title": e.get("title", "Sans titre"),
                "url": e.get("url") or e.get("webpage_url", ""),
                "duration": e.get("duration"),
                "thumbnail": e.get("thumbnail"),
            }
            for e in entries if e
        ]

        return jsonify({"videos": videos, "platform": "youtube"})
    except Exception as e:
        return jsonify({"error": f"YouTube : {str(e)}"}), 500


@app.route("/carousel-images", methods=["POST"])
def carousel_images():
    data = request.get_json(force=True)
    if not data or not data.get("url"):
        return jsonify({"error": "url manquante"}), 400

    url = data["url"].strip()

    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "cookiefile": IG_COOKIES_FILE,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        images = []
        caption = info.get("description", "") or info.get("title", "")

        if info.get("_type") == "playlist":
            for i, entry in enumerate(info.get("entries") or []):
                if not entry:
                    continue
                img_url = entry.get("url") or entry.get("thumbnail")
                if img_url:
                    images.append({"index": i, "url": img_url})
        else:
            # Single post — image ou vidéo
            img_url = info.get("url") or info.get("thumbnail")
            if img_url:
                images.append({"index": 0, "url": img_url})

        if not images:
            return jsonify({"error": "Aucune image trouvée dans ce post"}), 422

        return jsonify({"images": images, "caption": caption})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
