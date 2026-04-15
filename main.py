from flask import Flask, request
import requests
import os
import json
import re

DATA_FOLDER = "data"
ALLOWED_THREAD_ID = 25  # тема "Справочник"
PAGE_SIZE = 5
MAX_RESULTS_PER_BOOK = 5
OPEN_CONTEXT = 900
COMPARE_CONTEXT = 1200

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
URL = f"https://api.telegram.org/bot{TOKEN}/"

BOOKS = {}
SEARCH_CACHE = {}  # key: (chat_id, thread_id) -> {"query": str, "results": list}


def clean_text(text):
    # убираем переносы слов: "пони-\nмание" -> "понимание"
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)

    # одиночные переносы строк внутри абзаца превращаем в пробел
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)

    # схлопываем лишние пробелы
    text = re.sub(r'[ \t]+', ' ', text)

    # схлопываем слишком много пустых строк
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


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
                    raw = f.read()
                    books[filename] = clean_text(raw)
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


def build_word_pattern(query):
    """
    Ищем целое слово / словоформу, а не случайное совпадение букв.
    Например, для 'асана' будут находиться:
    асана, асаны, асанах, асаной...
    """
    query = re.escape(query.strip().lower())
    return re.compile(rf"(?<!\w){query}\w*", re.IGNORECASE)


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


def make_open_text(content, start_idx, end_idx, context=OPEN_CONTEXT):
    left = max(0, start_idx - context)
    right = min(len(content), end_idx + context)

    chunk = content[left:right].strip()
    chunk = re.sub(r"\n{3,}", "\n\n", chunk)

    if left > 0:
        chunk = "…\n" + chunk
    if right < len(content):
        chunk = chunk + "\n…"

    return chunk[:3500]


def make_compare_text(content, start_idx, end_idx, context=COMPARE_CONTEXT):
    left = max(0, start_idx - context)
    right = min(len(content), end_idx + context)

    chunk = content[left:right].strip()
    chunk = re.sub(r"\n{3,}", "\n\n", chunk)

    if left > 0:
        chunk = "…\n" + chunk
    if right < len(content):
        chunk = chunk + "\n…"

    return chunk[:2500]


def is_noise_snippet(snippet):
    s = normalize_text(snippet)

    noise_markers = [
        "список основных понятий",
        "глоссарии",
        "глоссарий",
        "указатель",
        "содержание",
        "оглавление"
    ]

    if any(marker in s for marker in noise_markers):
        return True

    if s.count(".") > 20:
        return True

    return False


def find_matches(query):
    results = []
    seen = set()

    query_norm = normalize_text(query)
    pattern = build_word_pattern(query_norm)

    for filename, content in BOOKS.items():
        file_count = 0

        for match in pattern.finditer(content):
            start_idx = match.start()
            end_idx = match.end()

            snippet = make_snippet(content, start_idx, end_idx)

            if is_noise_snippet(snippet):
                continue

            dedupe_key = normalize_text(snippet)
            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)

            results.append({
                "filename": filename,
                "start": start_idx,
                "end": end_idx,
                "snippet": snippet
            })
            file_count += 1

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

    lines.append("Открыть: кнопкой с номером ниже")
    lines.append("Сравнить: /compare 2 7")

    return "\n".join(lines), total_pages


def build_pagination_keyboard(results, page, total_pages):
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(results))

    keyboard = []

    open_row = []
    for idx in range(start, end):
        open_row.append({
            "text": str(idx + 1),
            "callback_data": f"open:{idx}"
        })

    if open_row:
        keyboard.append(open_row)

    nav_row = []
    if page > 0:
        nav_row.append({"text": "⬅️ Назад", "callback_data": f"page:{page - 1}"})
    if page < total_pages - 1:
        nav_row.append({"text": "Вперёд ➡️", "callback_data": f"page:{page + 1}"})

    if nav_row:
        keyboard.append(nav_row)

    return {"inline_keyboard": keyboard} if keyboard else None


def get_cached_results(chat_id, message_thread_id):
    session_key = (chat_id, message_thread_id)
    cached = SEARCH_CACHE.get(session_key)
    if not cached:
        return None, None
    return cached["query"], cached["results"]


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
                answer_callback_query(callback_id, "Эта функция работает только в теме Справочник.")
                return "ok"

            if not chat_id or not message_id:
                answer_callback_query(callback_id, "Не удалось обработать кнопку.")
                return "ok"

            session_key = (chat_id, message_thread_id)
            cached = SEARCH_CACHE.get(session_key)

            if not cached:
                answer_callback_query(callback_id, "Поиск устарел. Запусти /find ещё раз.")
                return "ok"

            query = cached["query"]
            results = cached["results"]

            if callback_data.startswith("page:"):
                try:
                    page = int(callback_data.split(":")[1])
                except ValueError:
                    answer_callback_query(callback_id, "Некорректная страница.")
                    return "ok"

                text_out, total_pages = build_page_text(query, results, page)
                keyboard = build_pagination_keyboard(results, page, total_pages)
                edit_message(chat_id, message_id, text_out, keyboard)
                answer_callback_query(callback_id)
                return "ok"

            if callback_data.startswith("open:"):
                try:
                    index = int(callback_data.split(":")[1])
                except ValueError:
                    answer_callback_query(callback_id, "Некорректный результат.")
                    return "ok"

                if index < 0 or index >= len(results):
                    answer_callback_query(callback_id, "Нет такого результата.")
                    return "ok"

                item = results[index]
                filename = item["filename"]
                content = BOOKS.get(filename, "")

                if not content:
                    answer_callback_query(callback_id, "Не удалось открыть файл.")
                    return "ok"

                full_text = make_open_text(content, item["start"], item["end"])
                open_message = (
                    f"Результат {index + 1}\n"
                    f"Файл: {filename}\n\n"
                    f"{full_text}"
                )

                send_message(chat_id, open_message, message_thread_id)
                answer_callback_query(callback_id, f"Открываю {index + 1}")
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
                "/find <запрос> — найти слово или фразу во всех книгах\n"
                "/compare <номер1> <номер2> — сравнить два результата из последнего поиска\n\n"
                "После поиска можно:\n"
                "— листать результаты кнопками\n"
                "— открывать результат кнопкой с номером\n"
                "— сравнивать по номерам, например: /compare 2 7\n\n"
                "Примеры:\n"
                "/find асана\n"
                "/compare 2 7"
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
                    keyboard = build_pagination_keyboard(results, page, total_pages)
                    send_message(chat_id, text_out, message_thread_id, keyboard)

        elif text.startswith("/compare"):
            args = text.replace("/compare", "", 1).strip().split()

            if len(args) != 2:
                send_message(
                    chat_id,
                    "Напиши два номера результата.\nПример: /compare 2 7",
                    message_thread_id
                )
            else:
                query, results = get_cached_results(chat_id, message_thread_id)

                if not results:
                    send_message(
                        chat_id,
                        "Сначала сделай поиск командой /find",
                        message_thread_id
                    )
                else:
                    try:
                        first_idx = int(args[0]) - 1
                        second_idx = int(args[1]) - 1

                        if first_idx < 0 or second_idx < 0 or first_idx >= len(results) or second_idx >= len(results):
                            send_message(
                                chat_id,
                                "Один из номеров выходит за пределы найденных результатов.",
                                message_thread_id
                            )
                        else:
                            first_item = results[first_idx]
                            second_item = results[second_idx]

                            first_content = BOOKS.get(first_item["filename"], "")
                            second_content = BOOKS.get(second_item["filename"], "")

                            first_text = make_compare_text(first_content, first_item["start"], first_item["end"])
                            second_text = make_compare_text(second_content, second_item["start"], second_item["end"])

                            compare_message = (
                                f"Сравнение результатов {first_idx + 1} и {second_idx + 1}\n\n"
                                f"——— [1] {first_item['filename']} ———\n"
                                f"{first_text}\n\n"
                                f"==============================\n\n"
                                f"——— [2] {second_item['filename']} ———\n"
                                f"{second_text}"
                            )

                            send_message(chat_id, compare_message[:4000], message_thread_id)

                    except ValueError:
                        send_message(
                            chat_id,
                            "Номера должны быть числами.\nПример: /compare 2 7",
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
