from flask import Flask, request
import requests
import os

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
    requests.post(URL + "sendMessage", json=payload)

@app.route("/", methods=["GET"])
def home():
    return "Bot is running!"

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    print("UPDATE:", data, flush=True)

    message = data.get("message") or data.get("edited_message")
    if not message:
        return "ok"

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")
    message_thread_id = message.get("message_thread_id")

    if chat_id:
        send_message(chat_id, f"Получил: {text or '[без текста]'}", message_thread_id)

    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
