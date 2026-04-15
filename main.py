from flask import Flask, request
import requests
import os
import json
import re
from collections import defaultdict

DATA_FOLDER = "data"
ALLOWED_THREAD_ID = 25  # тема "Справочник"
PAGE_SIZE = 5
MAX_RESULTS_PER_BOOK = 5

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
URL = f"https://api.telegram.org/bot{TOKEN}/"

BOOKS = {}
SEARCH_CACHE = {}  # key: (chat_id, thread_id) -> {"query": str, "results": list}


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


def tg_post(method, payload):
    try:
        response = requests.post(URL + method, json=payload, timeout=20)
        print(f"{method} STATUS:", response.status_code, flush=True)
        print(f"{method} RESPONSE:", response.text, flush=True)
        return response
    except Exception as e:
        print(f"{method} ERROR:", str(e), flush=True)
        return None


def send_message(chat_id, text, message_thread_id=None, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup

    tg_post("sendMessage", payload)


def edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    tg_post("editMessageText", payload)


def answer_callback_query(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    tg_post("answerCallbackQuery", payload)


def normalize_text(text):
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def make_snippet(content, start_idx, end_idx, context=180):
    left = max(0, start_idx - context)
    right = min(len(content), end_idx + context)

    snippet = content[left:right]
    snippet = snippet.replace("\n", " ")
    snippet = re.sub(r"\s+", " ", snippet).strip()

    if left > 0:
        snippet = "…" + snippet
    if right < len(content):
        snippet = snippet + "…"

    return snippet[:450]


def find_matches(query):
    results = []
    seen = set()
    query_norm = normalize_text(query)

    for filename, content in BOOKS.items():
        content_norm = content.lower()
        file_count = 0
        search_pos = 0

        while True:
            idx = content_norm.find(query_norm, search_pos)
            if idx == -1:
                break

            end_idx = idx + len(query_norm)
            snippet = make_snippet(content, idx, end_idx)

            dedupe_key = normalize_text(snippet)
            if dedupe_key not in seen:
                seen.add(dedupe_key)
                results.append({
                    "filename": filename,
                    "snippet": snippet
                })
                file_count += 1

            search_pos = end_idx

            if file_count >= MAX_RESULTS_PER_BOOK:
                break

    return results


def build_page_text(query, results, page):
    total = len(results)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    lines = [f"Найдено по запросу: {query}", f"Показаны {start + 1}–{end} из {total}\n"]

    for idx, item in enumerate(results[start:end], start=start + 1):
        lines.append(f"{idx}. {item['filename']}")
        lines.append(item["snippet"])
        lines.append("")

    return "\n".join(lines), total_pages


def build_pagination_keyboard(page, total_pages):
    buttons = []

    row = []
    if page > 0:
        row.append({"text": "⬅️ Назад", "callback_data": f"page:{page - 1}"})
    if page < total_pages - 1:
        row.append({"text": "Вперёд ➡️", "callback_data": f"page:{page + 1}"})

    if row:
        buttons.append(row)

    return {"inline_keyboard": buttons} if buttons else None


@app.route("/", methods=["GET"])
def home():
    return "Bot is running!"


@app.route("/", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        print("INCOMING UPDATE:", json.dumps(data, ensure_ascii=False), flush=True)

        callback_query = data.get("callback_query")
        if callback_query:
            callback_id = callback_query.get("id")
            callback_data = callback_query.get("data", "")
            callback_message = callback_query.get("message", {})

            chat_id = callback_message.get("chat", {}).get("id")
            message_id = callback_message.get("message_id")
            message_thread_id = callback_message.get("message_thread_id")

            print("CALLBACK DATA:", callback_data, flush=True)

            if message_thread_id != ALLOWED_THREAD_ID:
                answer_callback_query(callback_id, "Эта пагинация работает только в теме Справочник.")
                return "ok"

            if not chat_id or not message_id:
                answer_callback_query(callback_id, "Не удалось обработать кнопку.")
                return "ok"

            session_key = (chat_id, message_thread_id)
            cached = SEARCH_CACHE.get(session_key)

            if not cached:
                answer_callback_query(callback_id, "Поиск устарел. Запусти /find ещё раз.")
                return "ok"

            if callback_data.startswith("page:"):
                try:
                    page = int(callback_data.split(":")[1])
                except ValueError:
                    answer_callback_query(callback_id, "Некорректная страница.")
                    return "ok"

                query = cached["query"]
                results = cached["results"]

                text, total_pages = build_page_text(query, results, page)
                keyboard = build_pagination_keyboard(page, total_pages)
                edit_message(chat_id, message_id, text, keyboard)
                answer_callback_query(callback_id)
                return "ok"

            answer_callback_query(callback_id, "Неизвестная команда кнопки.")
            return "ok"

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

        session_key = (chat_id, message_thread_id)

        if text.startswith("/help"):
            help_text = (
                "Я бот-справочник по йога-текстам.\n\n"
                "Команды:\n"
                "/help — инструкция\n"
                "/find <запрос> — найти слово или фразу во всех книгах\n\n"
                "Примеры:\n"
                "/find асана\n"
                "/find пранаяма\n"
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
                SEARCH_CACHE[session_key] = {
                    "query": query,
                    "results": results
                }

                if not results:
                    send_message(chat_id, "Ничего не найдено.", message_thread_id)
                else:
                    page = 0
                    text_out, total_pages = build_page_text(query, results, page)
                    keyboard = build_pagination_keyboard(page, total_pages)
                    send_message(chat_id, text_out, message_thread_id, keyboard)

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
