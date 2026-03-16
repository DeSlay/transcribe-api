{
  "metadata": {
    "kernelspec": {
      "name": "xpython",
      "display_name": "Python 3.13 (XPython)",
      "language": "python"
    },
    "language_info": {
      "file_extension": ".py",
      "mimetype": "text/x-python",
      "name": "python",
      "version": "3.13.1"
    }
  },
  "nbformat_minor": 5,
  "nbformat": 4,
  "cells": [
    {
      "id": "2f69dd2b-8756-46c6-9168-f07da4d1bf93",
      "cell_type": "code",
      "source": "# main.py\nfrom flask import Flask, request, jsonify\nimport yt_dlp\nimport groq\nimport os\nimport glob\n\napp = Flask(__name__)\nclient = groq.Groq(api_key=os.environ[\"GROQ_API_KEY\"])\n\n@app.route(\"/transcribe\", methods=[\"POST\"])\ndef transcribe():\n    url = request.json[\"url\"]\n    \n    ydl_opts = {\"format\": \"bestaudio\", \"outtmpl\": \"/tmp/audio.%(ext)s\"}\n    with yt_dlp.YoutubeDL(ydl_opts) as ydl:\n        ydl.download([url])\n    \n    audio_file = glob.glob(\"/tmp/audio.*\")[0]\n    \n    with open(audio_file, \"rb\") as f:\n        transcription = client.audio.transcriptions.create(\n            file=f, model=\"whisper-large-v3\"\n        )\n    \n    return jsonify({\"transcription\": transcription.text})\n\nif __name__ == \"__main__\":\n    app.run(host=\"0.0.0.0\", port=8000)",
      "metadata": {
        "trusted": true
      },
      "outputs": [],
      "execution_count": null
    }
  ]
}