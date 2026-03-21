# main.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import groq
import os
import glob
import uuid
import traceback

app = Flask(__name__)
CORS(app)
client = groq.Groq(api_key=os.environ["GROQ_API_KEY"])

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

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
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

    return jsonify({"videos": videos})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
