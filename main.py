# main.py
from flask import Flask, request, jsonify
import yt_dlp
import groq
import os
import glob

app = Flask(__name__)
client = groq.Groq(api_key=os.environ["GROQ_API_KEY"])

@app.route("/transcribe", methods=["POST"])
def transcribe():
    url = request.json["url"]

    ydl_opts = {"format": "bestaudio", "outtmpl": "/tmp/audio.%(ext)s"}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    audio_file = glob.glob("/tmp/audio.*")[0]

    with open(audio_file, "rb") as f:
        transcription = client.audio.transcriptions.create(
            file=f, model="whisper-large-v3"
        )

    return jsonify({"transcription": transcription.text})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
