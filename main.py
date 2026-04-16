from flask import Flask, request, render_template_string
import requests
import os
import json
import re
from urllib.parse import quote

DATA_FOLDER = "data"
ALLOWED_THREAD_ID = 25
PAGE_SIZE = 5
MAX_RESULTS_PER_BOOK = 5
OPEN_CONTEXT = 900

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
URL = f"https://api.telegram.org/bot{TOKEN}/"

BOOKS = {}
SEARCH_CACHE = {}


def clean_text(text):
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r'[ \t]+', ' ', text)

    paragraphs = re.split(r'\n\s*\n', text)
    cleaned_paragraphs = []

    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        p = re.sub(r'\n+', ' ', p)
        p = re.sub(r'\s+', ' ', p).strip()
        cleaned_paragraphs.append(p)

    text = "\n\n".join(cleaned_paragraphs)
    text = re.sub(r'([:;])\s+—\s+', r'\1\n— ', text)
    text = re.sub(r'\s+(\d+\.\s+[А-ЯA-Z])', r'\n\n\1', text)
    text = re.sub(r'\s+—\s+', r'\n— ', text)
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


def format_open_chunk(chunk):
    chunk = re.sub(r'\n{3,}', '\n\n', chunk)
    chunk = re.sub(r'\n?(\d+\.\s+[А-ЯA-Z][^\n]+)', r'\n\n\1', chunk)
    chunk = re.sub(r'\s+—\s+', r'\n— ', chunk)
    chunk = re.sub(r'[ \t]+', ' ', chunk)
    chunk = re.sub(r'\n{3,}', '\n\n', chunk)
    return chunk.strip()


def make_open_text(content, start_idx, end_idx, context=OPEN_CONTEXT):
    left = max(0, start_idx - context)
    right = min(len(content), end_idx + context)

    chunk = content[left:right].strip()
    chunk = format_open_chunk(chunk)

    if left > 0:
        chunk = "…\n" + chunk
    if right < len(content):
        chunk = chunk + "\n…"

    return chunk[:3500]


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
                "query": query,
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

    return "\n".join(lines), total_pages


def build_reader_url(item):
    filename = quote(item["filename"])
    query = quote(item["query"])
    return f"/reader?file={filename}&start={item['start']}&end={item['end']}&query={query}"


def build_pagination_keyboard(results, page, total_pages):
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(results))

    keyboard = []

    open_row = []
    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")

    for idx in range(start, end):
        item = results[idx]
        reader_url = f"{base_url}{build_reader_url(item)}"

        open_row.append({
            "text": str(idx + 1),
            "url": reader_url
        })

    if open_row:
        keyboard.append(open_row)

    nav_row = []
    if page > 0:
        nav_row.append({
            "text": "⬅️ Назад",
            "callback_data": f"page:{page - 1}"
        })

    if page < total_pages - 1:
        nav_row.append({
            "text": "Вперёд ➡️",
            "callback_data": f"page:{page + 1}"
        })

    if nav_row:
        keyboard.append(nav_row)

    return {"inline_keyboard": keyboard} if keyboard else None
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(results))

    keyboard = []

   open_row = []
base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")

for idx in range(start, end):
    item = results[idx]
    reader_url = f"{base_url}{build_reader_url(item)}"

    open_row.append({
        "text": str(idx + 1),
        "url": reader_url
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


READER_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Ридер</title>
  <style>
    body {
      margin: 0;
      font-family: system-ui, -apple-system, sans-serif;
      background: #111;
      color: #eee;
    }
    .topbar {
      position: sticky;
      top: 0;
      background: #1a1a1a;
      border-bottom: 1px solid #333;
      padding: 14px 16px;
      z-index: 10;
    }
    .title {
      font-size: 18px;
      font-weight: 600;
      margin-bottom: 4px;
      word-break: break-word;
    }
    .meta {
      font-size: 13px;
      color: #aaa;
    }
    .content {
      padding: 20px 16px 40px;
      line-height: 1.7;
      font-size: 18px;
      white-space: pre-wrap;
      max-width: 900px;
      margin: 0 auto;
    }
    mark {
      background: #ffd54f;
      color: #111;
      padding: 0 2px;
      border-radius: 3px;
    }
    #target {
      scroll-margin-top: 80px;
    }
  </style>
</head>
<body>
  <div class="topbar">
    <div class="title">{{ filename }}</div>
    <div class="meta">Запрос: {{ query }}</div>
  </div>

  <div class="content">{{ content|safe }}</div>

  <script>
    const target = document.getElementById("target");
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  </script>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def home():
    return "Bot is running!"


@app.route("/reader", methods=["GET"])
def reader():
    filename = request.args.get("file", "")
    query = request.args.get("query", "")
    start = request.args.get("start", type=int)
    end = request.args.get("end", type=int)

    if filename not in BOOKS:
        return "Файл не найден", 404

    content = BOOKS[filename]

    if start is None or end is None:
        return "Некорректные параметры", 400

    left = max(0, start - OPEN_CONTEXT)
    right = min(len(content), end + OPEN_CONTEXT)
    chunk = content[left:right]

    if query:
        pattern = build_word_pattern(query)

        first_done = False

        def replace_first(m):
            nonlocal first_done
            if not first_done:
                first_done = True
                return f'<mark id="target">{m.group(0)}</mark>'
            return f'<mark>{m.group(0)}</mark>'

        chunk = pattern.sub(replace_first, chunk)
    else:
        relative_pos = start - left
        chunk = (
            chunk[:relative_pos]
            + '<mark id="target">'
            + chunk[relative_pos:end-left]
            + '</mark>'
            + chunk[end-left:]
        )

    chunk = chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    chunk = chunk.replace("&lt;mark", "<mark").replace("/mark&gt;", "/mark>").replace('id="target"&gt;', 'id="target">')

    chunk = re.sub(r'\n{3,}', '\n\n', chunk)

    return render_template_string(
        READER_TEMPLATE,
        filename=filename,
        query=query,
        content=chunk
    )


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
                "После поиска можно:\n"
                "— листать результаты кнопками\n"
                "— открывать результат кнопкой с номером\n\n"
                "Пример:\n"
                "/find асана"
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
