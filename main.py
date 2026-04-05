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
import re
import http.cookiejar
import instaloader
import requests as _requests

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

# Décode les cookies TikTok
_tk_b64 = os.environ.get("TIKTOK_COOKIES_B64")
if _tk_b64:
    with open("/tmp/tk_cookies.txt", "wb") as _f:
        _f.write(base64.b64decode(_tk_b64))

COOKIES_FILE = "/tmp/yt_cookies.txt" if os.path.exists("/tmp/yt_cookies.txt") else None
IG_COOKIES_FILE = "/tmp/ig_cookies.txt" if os.path.exists("/tmp/ig_cookies.txt") else None
_tk_local = os.path.expanduser("~/Documents/content-studio/www.tiktok.com_cookies.txt")
TK_COOKIES_FILE = (
    "/tmp/tk_cookies.txt" if os.path.exists("/tmp/tk_cookies.txt")
    else _tk_local if os.path.exists(_tk_local)
    else None
)

print(f"[boot] TK_COOKIES_FILE={TK_COOKIES_FILE}, TIKTOK_COOKIES_B64={'set' if _tk_b64 else 'NOT SET'}")

def make_instaloader():
    """Crée un Instaloader avec les cookies Instagram si disponibles."""
    L = instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
    )
    if IG_COOKIES_FILE:
        try:
            cj = http.cookiejar.MozillaCookieJar(IG_COOKIES_FILE)
            cj.load(ignore_discard=True, ignore_expires=True)
            L.context._session.cookies.update(cj)
        except Exception as e:
            print(f"[instaloader] Cookies non chargés : {e}")
    return L


@app.route("/transcribe", methods=["POST"])
def transcribe():
    audio_path = None
    try:
        data = request.get_json(force=True)
        if not data or not data.get("url"):
            return jsonify({"error": "url manquante"}), 400

        url = data["url"].strip()
        print(f"[transcribe] URL reçue: {url}", flush=True)

        # Fichier unique par requête pour éviter les conflits
        unique_id = str(uuid.uuid4())[:8]
        output_template = f"/tmp/audio_{unique_id}.%(ext)s"

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "cookiefile": IG_COOKIES_FILE if "instagram.com" in url else TK_COOKIES_FILE if "tiktok.com" in url else COOKIES_FILE,
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
    limit = int(data.get("limit", 5))

    if "instagram.com" in url:
        return _fetch_instagram(url, limit=limit)
    else:
        return _fetch_youtube(url, limit=limit)


def _apify_instagram(username):
    """Scrape via Apify Instagram Profile Scraper (proxies résidentiels)."""
    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        raise Exception("APIFY_API_TOKEN non défini")

    run_url = (
        "https://api.apify.com/v2/acts/apify~instagram-profile-scraper"
        f"/run-sync-get-dataset-items?token={token}&timeout=90"
    )
    resp = _requests.post(
        run_url,
        json={"usernames": [username], "resultsLimit": 30},
        timeout=120,
    )
    resp.raise_for_status()
    items = resp.json()

    posts = []
    for item in items:
        item_type = item.get("type", "")
        if item_type == "Video":
            post_type = "video"
        elif item_type == "Sidecar":
            post_type = "carousel"
        else:
            post_type = "image"

        shortcode = item.get("shortCode", "")
        posts.append({
            "id": shortcode,
            "title": (item.get("caption") or "Sans titre")[:100],
            "url": f"https://www.instagram.com/p/{shortcode}/",
            "thumbnail": item.get("displayUrl"),
            "duration": item.get("videoDuration"),
            "type": post_type,
            "likes": item.get("likesCount") or 0,
            "views": item.get("videoViewCount"),
            "comments": item.get("commentsCount") or 0,
        })

    if not posts:
        raise Exception("Apify n'a retourné aucun post")

    return posts


def _fetch_instagram(url, limit=5):
    """Scrape un profil Instagram : Apify → yt-dlp → instaloader."""
    # Extrait le username
    parts = url.rstrip("/").split("/")
    username = parts[-1].lstrip("@")
    if not username or "." in username.split("instagram")[-1] == 0:
        username = next((p for p in reversed(parts) if p and not p.startswith("http") and "instagram" not in p), None)
    if not username:
        return jsonify({"error": "Username Instagram introuvable dans l'URL"}), 400

    profile_url = f"https://www.instagram.com/{username}/"

    # ── Tentative 0 : Apify (proxies résidentiels — contourne le blocage IP) ──
    try:
        posts = _apify_instagram(username)
        posts.sort(key=lambda p: p["likes"], reverse=True)
        return jsonify({"videos": posts, "platform": "instagram"})
    except Exception as e0:
        print(f"[instagram] Apify échoué ({e0}), tentative yt-dlp...")

    # ── Tentative 1 : yt-dlp (plus robuste sur les IPs datacenter) ────────────
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "cookiefile": IG_COOKIES_FILE,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                "Accept-Language": "fr-FR,fr;q=0.9",
            },
            "playlist_items": "1:30",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(profile_url, download=False)

        entries = info.get("entries") or []
        posts = []
        for e in entries:
            if not e:
                continue
            duration = e.get("duration")
            post_type = "video" if duration else "image"
            posts.append({
                "id": e.get("id") or e.get("shortcode", ""),
                "title": (e.get("title") or e.get("description") or "Sans titre")[:100],
                "url": e.get("url") or e.get("webpage_url") or "",
                "thumbnail": e.get("thumbnail"),
                "duration": duration,
                "type": post_type,
                "likes": e.get("like_count") or 0,
                "views": e.get("view_count"),
                "comments": e.get("comment_count") or 0,
            })

        if posts:
            posts.sort(key=lambda p: p["likes"], reverse=True)
            return jsonify({"videos": posts, "platform": "instagram"})

        raise Exception("yt-dlp n'a retourné aucun post, tentative instaloader")

    except Exception as e1:
        print(f"[instagram] yt-dlp échoué ({e1}), tentative instaloader...")

    # ── Tentative 2 : instaloader avec cookies ────────────────────────────────
    try:
        L = make_instaloader()
        profile = instaloader.Profile.from_username(L.context, username)

        posts = []
        count = 0
        for post in profile.get_posts():
            count += 1
            if post.typename == "GraphSidecar":
                post_type = "carousel"
            elif post.is_video:
                post_type = "video"
            else:
                post_type = "image"

            posts.append({
                "id": post.shortcode,
                "title": (post.caption[:100] if post.caption else "Sans titre"),
                "url": f"https://www.instagram.com/p/{post.shortcode}/",
                "thumbnail": post.url,
                "duration": post.video_duration if post.is_video else None,
                "type": post_type,
                "likes": post.likes or 0,
                "views": post.video_view_count if post.is_video else None,
                "comments": post.comments or 0,
            })
            if count >= limit:
                break

        posts.sort(key=lambda p: p["likes"], reverse=True)
        return jsonify({"videos": posts, "platform": "instagram"})

    except Exception as e2:
        traceback.print_exc()
        return jsonify({
            "error": (
                f"Instagram bloque les IPs datacenter ({str(e2)}). "
                "Ajoutez APIFY_API_TOKEN sur Render (proxy résidentiel) "
                "ou utilisez les URLs individuelles."
            )
        }), 500


def _fetch_youtube(url, limit=5):
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

        entries = info.get("entries", [info])[:limit]
        videos = [
            {
                "id": e.get("id", ""),
                "title": e.get("title", "Sans titre"),
                "url": e.get("webpage_url") or e.get("url", ""),
                "duration": e.get("duration"),
                "thumbnail": e.get("thumbnail"),
            }
            for e in entries if e
        ]

        return jsonify({"videos": videos, "platform": "youtube"})
    except Exception as e:
        return jsonify({"error": f"YouTube : {str(e)}"}), 500


def _ytdlp_post(post_url):
    """Extrait images/vidéos d'un post Instagram via yt-dlp (pas d'appel GraphQL)."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "cookiefile": IG_COOKIES_FILE,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.4 Mobile/15E148 Safari/604.1"
            ),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(post_url, download=False)

    images = []
    caption = info.get("description") or info.get("title") or ""

    entries = info.get("entries") or []
    if entries:
        # carousel
        for i, entry in enumerate(entries):
            media_url = entry.get("url") or entry.get("thumbnail")
            ext = (entry.get("ext") or "").lower()
            is_video = ext in ("mp4", "m4a", "webm", "mov")
            if media_url:
                images.append({"index": i, "url": media_url, "type": "video" if is_video else "image"})
    else:
        # post unique
        ext = (info.get("ext") or "").lower()
        is_video = ext in ("mp4", "m4a", "webm", "mov")
        media_url = info.get("url") if is_video else None
        thumbnail = info.get("thumbnail")
        if is_video and media_url:
            images.append({"index": 0, "url": media_url, "type": "video"})
        elif thumbnail:
            images.append({"index": 0, "url": thumbnail, "type": "image"})

    return images, caption


def _apify_post(post_url):
    """Extrait images/vidéos d'un post Instagram via Apify (proxies résidentiels)."""
    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        raise Exception("APIFY_API_TOKEN non défini")

    run_url = (
        "https://api.apify.com/v2/acts/apify~instagram-scraper"
        f"/run-sync-get-dataset-items?token={token}&timeout=90"
    )
    resp = _requests.post(
        run_url,
        json={"directUrls": [post_url], "resultsType": "posts", "resultsLimit": 1},
        timeout=120,
    )
    resp.raise_for_status()
    items = resp.json()

    if not items:
        raise Exception("Apify n'a retourné aucun résultat")

    item = items[0]
    caption = item.get("caption") or ""
    images = []

    # Carousel : childPosts
    child_posts = item.get("childPosts") or []
    if child_posts:
        for i, child in enumerate(child_posts):
            video_url = child.get("videoUrl")
            display_url = child.get("displayUrl")
            if video_url:
                images.append({"index": i, "url": video_url, "type": "video"})
            elif display_url:
                images.append({"index": i, "url": display_url, "type": "image"})
    else:
        video_url = item.get("videoUrl")
        display_url = item.get("displayUrl")
        if video_url:
            images.append({"index": 0, "url": video_url, "type": "video"})
        elif display_url:
            images.append({"index": 0, "url": display_url, "type": "image"})

    return images, caption


def _instaloader_post(shortcode):
    """Extrait images/vidéos d'un post via instaloader (risque 403 sur IP datacenter)."""
    L = make_instaloader()
    post = instaloader.Post.from_shortcode(L.context, shortcode)

    images = []
    caption = post.caption or ""

    if post.typename == "GraphSidecar":
        for i, node in enumerate(post.get_sidecar_nodes()):
            img_url = node.video_url if node.is_video else node.display_url
            images.append({"index": i, "url": img_url, "type": "video" if node.is_video else "image"})
    elif post.is_video:
        images.append({"index": 0, "url": post.video_url, "type": "video"})
    else:
        images.append({"index": 0, "url": post.url, "type": "image"})

    return images, caption


@app.route("/carousel-images", methods=["POST"])
def carousel_images():
    data = request.get_json(force=True)
    if not data or not data.get("url"):
        return jsonify({"error": "url manquante"}), 400

    url = data["url"].strip().split("?")[0]  # retire ?img_index=1 etc.

    m = re.search(r'/p/([A-Za-z0-9_-]+)', url)
    if not m:
        return jsonify({"error": "Shortcode Instagram introuvable dans l'URL"}), 400
    shortcode = m.group(1)
    post_url = f"https://www.instagram.com/p/{shortcode}/"

    # ── Tentative 1 : instaloader (méthode principale — fonctionne bien) ──────
    try:
        images, caption = _instaloader_post(shortcode)
        if images:
            print(f"[carousel] instaloader OK — {len(images)} média(s)")
            return jsonify({"images": images, "caption": caption})
        print("[carousel] instaloader : aucune image, passage à yt-dlp...")
    except Exception as e1:
        print(f"[carousel] instaloader échoué ({e1}), passage à yt-dlp...")

    # ── Tentative 2 : yt-dlp (fallback si instaloader 403) ───────────────────
    try:
        images, caption = _ytdlp_post(post_url)
        if images:
            print(f"[carousel] yt-dlp OK — {len(images)} média(s)")
            return jsonify({"images": images, "caption": caption})
        print("[carousel] yt-dlp : aucune image...")
    except Exception as e2:
        print(f"[carousel] yt-dlp échoué ({e2})")

    # ── Tentative 3 : Apify (proxies résidentiels — si APIFY_API_TOKEN dispo) ─
    try:
        images, caption = _apify_post(post_url)
        if images:
            print(f"[carousel] Apify OK — {len(images)} média(s)")
            return jsonify({"images": images, "caption": caption})
        print("[carousel] Apify : aucune image...")
    except Exception as e3:
        print(f"[carousel] Apify échoué ({e3})")

    traceback.print_exc()
    return jsonify({"error": "Toutes les méthodes ont échoué pour ce post Instagram."}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
