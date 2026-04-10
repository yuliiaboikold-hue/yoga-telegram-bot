from flask import Flask, request
import requests
import os
import json

DATA_FOLDER = "data"
ALLOWED_THREAD_ID = 25  # тема "Справочник"

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
URL = f"https://api.telegram.org/bot{TOKEN}/"


def load_books():
    books = {}

    if not os.path.exists(DATA_FOLDER):
        print(f"DATA FOLDER NOT FOUND: {DATA_FOLDER}", flush=True)
        return books

    for filename in os.listdir(DATA_FOLDER):
        if filename.endswith(".txt"):
            path = os.path.join(DATA_FOLDER, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    books[filename] = f.read()
                print(f"LOADED BOOK: {filename}", flush=True)
            except Exception as e:
                print(f"ERROR LOADING {filename}: {e}", flush=True)

    print(f"TOTAL BOOKS LOADED: {len(books)}", flush=True)
    return books


BOOKS = load_books()


def send_message(chat_id, text, message_thread_id=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if message_thread_id:
        payload["message_thread_id"] = message_thread_id

    try:
        response = requests.post(URL + "sendMessage", json=payload, timeout=20)
        print("SEND MESSAGE STATUS:", response.status_code, flush=True)
        print("SEND MESSAGE RESPONSE:", response.text, flush=True)
    except Exception as e:
        print("SEND MESSAGE ERROR:", str(e), flush=True)


def normalize_text(text):
    return text.lower().strip()


def is_useful_line(line):
    clean = line.strip()

    if not clean:
        return False

    if len(clean) < 20:
        return False

    if clean.count(".") > 10:
        return False

    if clean.isdigit():
        return False

    return True


def find_matches(query):
    results = []
    seen_snippets = set()
    query = normalize_text(query)

    for filename, content in BOOKS.items():
        lines = content.splitlines()
        file_matches = 0

        for i, line in enumerate(lines):
            clean_line = line.strip()

            if not clean_line:
                continue

            if query in clean_line.lower():
                start = max(0, i - 1)
                end = min(len(lines), i + 2)

                snippet_lines = []
                for snippet_line in lines[start:end]:
                    snippet_line = snippet_line.strip()
                    if snippet_line and is_useful_line(snippet_line):
                        snippet_lines.append(snippet_line)

                if not snippet_lines:
                    continue

                snippet = " ".join(snippet_lines)
                snippet = snippet[:250]

                snippet_key = snippet.lower()
                if snippet_key in seen_snippets:
                    continue

                seen_snippets.add(snippet_key)
                results.append(f"{filename}\n{snippet}")
                file_matches += 1

                if file_matches >= 3:
                    break

    return results


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
                "/find <запрос> — найти слово или фразу в загруженных книгах\n\n"
                "Примеры:\n"
                "/find асана\n"
                "/find медитация\n"
                "/find sthira sukham asanam"
            )
            send_message(chat_id, help_text, message_thread_id)

        elif text.startswith("/find"):
            query = text.replace("/find", "", 1).strip()

            if not query:
                send_message(
                    chat_id,
                    "Напиши запрос после команды.\nПример: /find асана",
                    message_thread_id
                )
            else:
                results = find_matches(query)

                if not results:
                    send_message(chat_id, "Ничего не найдено.", message_thread_id)
                else:
                    response = "Найдено:\n\n" + "\n\n".join(results[:10])
                    send_message(chat_id, response, message_thread_id)

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
