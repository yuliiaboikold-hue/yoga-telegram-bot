from flask import Flask, request
import requests
import os
DATA_FOLDER = "data"

def load_books():
    books = {}
    for filename in os.listdir(DATA_FOLDER):
        if filename.endswith(".txt"):
            path = os.path.join(DATA_FOLDER, filename)
            with open(path, "r", encoding="utf-8") as f:
                books[filename] = f.read()
    return books

BOOKS = load_books()
import json

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
URL = f"https://api.telegram.org/bot{TOKEN}/"

ALLOWED_THREAD_ID = 25  # тема "Справочник"

def send_message(chat_id, text, message_thread_id=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id

    response = requests.post(URL + "sendMessage", json=payload, timeout=20)
    print("SEND MESSAGE STATUS:", response.status_code, flush=True)
    print("SEND MESSAGE RESPONSE:", response.text, flush=True)

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
            return "ok"

        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        message_thread_id = message.get("message_thread_id")

        print("CHAT ID:", chat_id, flush=True)
        print("TEXT:", text, flush=True)
        print("THREAD ID:", message_thread_id, flush=True)

        if message_thread_id != ALLOWED_THREAD_ID:
            return "ok"

        if not chat_id:
            return "ok"

        if text.startswith("/help"):
            help_text = (
                "Я бот-справочник по йога-текстам.\n\n"
                "Доступные команды:\n"
                "/help — показать эту инструкцию\n"
                "/find <запрос> — найти тему или термин\n\n"
                "Пример:\n"
                "/find асана\n"
                "/find Патанджали про медитацию"
            )
            send_message(chat_id, help_text, message_thread_id)

elif text.startswith("/find"):
    query = text.replace("/find", "", 1).strip().lower()

    if not query:
        send_message(chat_id, "Напиши запрос: /find асана", message_thread_id)
    else:
        results = []

        for filename, content in BOOKS.items():
            blocks = content.split("---")

            for block in blocks:
                if query in block.lower():
                    # найти reference
                    lines = block.strip().split("\n")
                    if lines:
                        ref = lines[0].replace("##", "").strip()
                        results.append(f"{filename} → {ref}")

        if not results:
            send_message(chat_id, "Ничего не найдено", message_thread_id)
        else:
            response = "Найдено:\n" + "\n".join(results[:20])
            send_message(chat_id, response, message_thread_id)
            query = text.replace("/find", "", 1).strip()

            if not query:
                send_message(
                    chat_id,
                    "Напиши запрос после команды.\nПример: /find асана",
                    message_thread_id
                )
            else:
                send_message(
                    chat_id,
                    f"Ищу по запросу: {query}",
                    message_thread_id
                )

        else:
            send_message(
                chat_id,
                "Пожалуйста, используй команды.\nНапиши /help для инструкции.",
                message_thread_id
            )

        return "ok"

    except Exception as e:
        print("WEBHOOK ERROR:", str(e), flush=True)
        return "error", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
