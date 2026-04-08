from flask import Flask, request
import requests
import os
import json

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
URL = f"https://api.telegram.org/bot{TOKEN}/"

def send_message(chat_id, text, message_thread_id=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id

    try:
        response = requests.post(URL + "sendMessage", json=payload, timeout=20)
        print("SEND MESSAGE PAYLOAD:", json.dumps(payload, ensure_ascii=False), flush=True)
        print("SEND MESSAGE STATUS:", response.status_code, flush=True)
        print("SEND MESSAGE RESPONSE:", response.text, flush=True)
    except Exception as e:
        print("SEND MESSAGE ERROR:", str(e), flush=True)

@app.route("/", methods=["GET"])
def home():
    return "Bot is running!"

@app.route("/", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        print("INCOMING UPDATE:", json.dumps(data, ensure_ascii=False), flush=True)

        message = data.get("message") or data.get("edited_message")
        if not message:
            print("NO MESSAGE FIELD FOUND", flush=True)
            return "ok"

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")
        message_thread_id = message.get("message_thread_id")

        print("CHAT ID:", chat_id, flush=True)
        print("TEXT:", text, flush=True)
        print("THREAD ID:", message_thread_id, flush=True)

        if chat_id:
            send_message(chat_id, f"Получил: {text or '[без текста]'}", message_thread_id)

        return "ok"
    except Exception as e:
        print("WEBHOOK ERROR:", str(e), flush=True)
        return "error", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
