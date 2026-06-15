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

# Cookies Facebook (Ads Library + Reels). Présent localement ou via FB_COOKIES_B64.
_fb_b64 = os.environ.get("FB_COOKIES_B64")
if _fb_b64:
    with open("/tmp/fb_cookies.txt", "wb") as _f:
        _f.write(base64.b64decode(_fb_b64))
_fb_local = os.path.expanduser("~/Documents/transcribe-api/www.facebook.com_cookies.txt")
FB_COOKIES_FILE = (
    "/tmp/fb_cookies.txt" if os.path.exists("/tmp/fb_cookies.txt")
    else _fb_local if os.path.exists(_fb_local)
    else None
)

print(f"[boot] TK_COOKIES_FILE={TK_COOKIES_FILE}, TIKTOK_COOKIES_B64={'set' if _tk_b64 else 'NOT SET'}")
print(f"[boot] FB_COOKIES_FILE={FB_COOKIES_FILE}, FB_COOKIES_B64={'set' if _fb_b64 else 'NOT SET'}")

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

        # 🆕 Télécharge MP4 (vidéo + audio) au lieu d'audio seul :
        # - Whisper Groq accepte les MP4 (extrait l'audio auto)
        # - On peut renvoyer la vidéo encodée en base64 pour analyse plans Gemini
        ydl_opts = {
            "format": "best[ext=mp4][filesize<25M]/best[ext=mp4]/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "cookiefile": (
                IG_COOKIES_FILE if "instagram.com" in url
                else TK_COOKIES_FILE if "tiktok.com" in url
                else FB_COOKIES_FILE if ("facebook.com" in url or "fb.com" in url or "fbcdn.net" in url)
                else COOKIES_FILE
            ),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            return jsonify({"error": f"Téléchargement échoué : {str(e)}"}), 422

        # Trouve le fichier téléchargé
        files = glob.glob(f"/tmp/audio_{unique_id}.*")
        if not files:
            return jsonify({"error": "Fichier vidéo/audio introuvable après téléchargement"}), 422

        audio_path = files[0]

        # Limite 25 MB (Groq Whisper)
        size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        if size_mb > 25:
            return jsonify({"error": f"Fichier trop grand ({size_mb:.1f} MB, max 25 MB)"}), 422

        with open(audio_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(audio_path), f),
                model="whisper-large-v3",
            )

        # 🆕 Encode la vidéo en base64 pour analyse plans côté Next.js (Gemini)
        # Limite stricte 20MB pour rester sous le payload max Vercel
        video_b64 = None
        MAX_B64_BYTES = 20 * 1024 * 1024
        try:
            with open(audio_path, "rb") as f:
                video_bytes = f.read()
            if len(video_bytes) <= MAX_B64_BYTES:
                video_b64 = base64.b64encode(video_bytes).decode("utf-8")
                print(f"[transcribe] ✓ video_b64 encodé ({size_mb:.1f}MB)", flush=True)
            else:
                print(f"[transcribe] ⊘ vidéo trop grosse pour b64 ({size_mb:.1f}MB > 20MB)", flush=True)
        except Exception as e:
            print(f"[transcribe] ⚠ encode b64 échoué : {e}", flush=True)

        return jsonify({
            "transcription": transcription.text,
            "video_b64": video_b64,
            "video_size_mb": round(size_mb, 1),
        })

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


# ── 🎬 /analyze-deep : Download MP4 + envoie direct à Gemini pour analyse complète
# Pas de Whisper ici : Gemini transcrit nativement + analyse plans/hook/ton/etc.
# Retourne le JSON d'analyse parsé prêt à consommer côté front.
ANALYZE_DEEP_PROMPT = """Tu es Lead Creative Director spécialisé en ads UGC TikTok/Meta et direction artistique visuelle.
Analyse cette vidéo publicitaire avec précision. Réponds UNIQUEMENT en JSON valide, sans markdown, sans texte autour.

{"summary":"résumé 2-3 phrases du concept et de l'efficacité marketing","hook":"accroche exacte des 3 premières secondes mot pour mot","tone":"ton exact (ex: authentique/confiant/humoristique)","persona":"description physique précise du créateur si présent","characterMasterBlock":"description stable en anglais ou vide","characterProfile":"dark|mediterranean|light|none","angles":["angle marketing 1","angle marketing 2"],"cta":"CTA exact","visualModeDetected":"ugc|product_texture|product_demo|broll|podcast|before_after","narrationMode":"talking_head|voice_over|mixed","selfieStyle":"handheld|tripod|unknown","pacing":"slow|medium|fast","detectedEnvironments":["env1","env2"],"production":{"cameraStyle":"...","lighting":"...","background":"...","backgroundExact":"description en anglais ultra-précise","outfit":"...","expressions":"...","editingStyle":"...","promptDirections":"english technical directions"},"sceneBreakdown":[{"order":1,"timestamp":"00:00-00:03","visualDescription":"...","textOrSpeech":"...","role":"hook|problème|présentation|texture|application|réaction|preuve|CTA","shotType":"face cam|close-up visage|macro produit|plan épaule|regard miroir|macro texture|b-roll","productVisible":false,"emotion":"...","motionType":"idle-breath|head-turn|micro-expression|handheld-drift|product-pour|skin-close|blink-speak|apply-motion"}],"transcript":"transcription mot pour mot complète","creativeInsights":{"hooks":["hook alt 1","hook alt 2","hook alt 3"],"angles":["angle 1","angle 2"],"formats":["format 1"],"ideas":["idée 1","idée 2"]}}"""

def _upload_to_gemini_file_api(video_path: str, api_key: str, mime_type: str = "video/mp4") -> str:
    """Upload via resumable upload STREAM (pas de read en RAM), attend ACTIVE, retourne fileUri.
    🆕 Prend video_path au lieu de bytes pour économiser la mémoire (Render Free 512MB)."""
    import time as _time
    file_size = os.path.getsize(video_path)
    # 1. Init upload session
    init_res = _requests.post(
        f"https://generativelanguage.googleapis.com/resumable/upload/v1beta/files?key={api_key}",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(file_size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
        },
        json={"file": {"display_name": f"video_{uuid.uuid4().hex[:8]}.mp4"}},
        timeout=30,
    )
    if not init_res.ok:
        raise Exception(f"Gemini File API init: HTTP {init_res.status_code} — {init_res.text[:200]}")
    upload_url = init_res.headers.get("x-goog-upload-url")
    if not upload_url:
        raise Exception("Gemini File API : pas de upload URL")
    # 2. Upload en STREAM (fichier passé directement, requests envoie en chunks)
    with open(video_path, "rb") as f:
        up_res = _requests.post(
            upload_url,
            headers={
                "Content-Length": str(file_size),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            data=f,
            timeout=120,
        )
    if not up_res.ok:
        raise Exception(f"Gemini upload: HTTP {up_res.status_code} — {up_res.text[:200]}")
    file_info = up_res.json().get("file", {})
    file_name = file_info.get("name")
    state = file_info.get("state")
    uri = file_info.get("uri")
    # 3. Poll until ACTIVE (max 35 × 2s = 70s)
    attempts = 0
    while state != "ACTIVE" and attempts < 35:
        _time.sleep(2)
        st_res = _requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}",
            timeout=15,
        )
        if st_res.ok:
            st_data = st_res.json()
            state = st_data.get("state")
            uri = st_data.get("uri")
        attempts += 1
    if state != "ACTIVE":
        raise Exception(f"Gemini File API : fichier pas ACTIVE après 70s (state={state})")
    return uri

@app.route("/analyze-deep", methods=["POST"])
def analyze_deep():
    """Analyse complète d'une vidéo (URL → MP4 → Gemini direct)."""
    import time as _t
    video_path = None
    t0 = _t.time()
    try:
        data = request.get_json(force=True)
        if not data or not data.get("url"):
            return jsonify({"error": "url manquante"}), 400
        url = data["url"].strip()
        api_key = os.environ.get("GOOGLE_AI_API_KEY")
        if not api_key:
            return jsonify({"error": "GOOGLE_AI_API_KEY non définie côté serveur"}), 500

        print(f"[analyze-deep] ▶ START URL={url[:60]} t=0s", flush=True)
        unique_id = str(uuid.uuid4())[:8]
        output_template = f"/tmp/video_{unique_id}.%(ext)s"
        ydl_opts = {
            "format": "best[ext=mp4][filesize<25M]/best[ext=mp4]/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "cookiefile": (
                IG_COOKIES_FILE if "instagram.com" in url
                else TK_COOKIES_FILE if "tiktok.com" in url
                else FB_COOKIES_FILE if ("facebook.com" in url or "fb.com" in url or "fbcdn.net" in url)
                else COOKIES_FILE
            ),
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            return jsonify({"error": f"Téléchargement échoué : {str(e)}"}), 422

        files = glob.glob(f"/tmp/video_{unique_id}.*")
        if not files:
            return jsonify({"error": "Fichier vidéo introuvable après téléchargement"}), 422
        video_path = files[0]
        size_mb = os.path.getsize(video_path) / (1024 * 1024)
        print(f"[analyze-deep] ✓ download OK ({size_mb:.1f}MB) t={_t.time()-t0:.1f}s", flush=True)

        # Upload à Gemini File API (STREAM, pas de read en RAM)
        print(f"[analyze-deep] ▶ Gemini upload stream, {size_mb:.1f}MB t={_t.time()-t0:.1f}s", flush=True)
        file_uri = _upload_to_gemini_file_api(video_path, api_key)
        print(f"[analyze-deep] ✓ Gemini upload OK : {file_uri[:60]} t={_t.time()-t0:.1f}s", flush=True)

        # Appel Gemini avec prompt + fileUri
        print(f"[analyze-deep] ▶ Gemini generateContent t={_t.time()-t0:.1f}s", flush=True)
        gem_res = _requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
            json={
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"fileData": {"mimeType": "video/mp4", "fileUri": file_uri}},
                        {"text": ANALYZE_DEEP_PROMPT},
                    ],
                }],
                "generationConfig": {
                    "maxOutputTokens": 16000,
                    "temperature": 0.2,
                    "responseMimeType": "application/json",
                },
            },
            timeout=240,
        )
        print(f"[analyze-deep] ← Gemini HTTP {gem_res.status_code} t={_t.time()-t0:.1f}s", flush=True)
        if not gem_res.ok:
            return jsonify({"error": f"Gemini analyse : HTTP {gem_res.status_code} — {gem_res.text[:300]}"}), 502

        gem_data = gem_res.json()
        text = (gem_data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text") or "")
        if not text:
            print(f"[analyze-deep] ❌ Gemini text vide. raw keys: {list(gem_data.keys())}", flush=True)
            return jsonify({"error": "Gemini a renvoyé une réponse vide", "raw_keys": list(gem_data.keys())}), 502
        # Parse le JSON
        import json as _json
        try:
            analysis = _json.loads(text)
        except Exception as je:
            print(f"[analyze-deep] ⚠ JSON parse fail: {je} — fallback regex", flush=True)
            m = re.search(r"\{[\s\S]*\}", text)
            analysis = _json.loads(m.group(0)) if m else {"raw": text}

        analysis["video_size_mb"] = round(size_mb, 1)
        print(f"[analyze-deep] ✅ END t={_t.time()-t0:.1f}s — scenes={len(analysis.get('sceneBreakdown', []))}", flush=True)
        return jsonify(analysis)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if video_path and os.path.exists(video_path):
            try: os.remove(video_path)
            except Exception: pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
