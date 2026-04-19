from flask import Flask, request
import requests
import os
import json
import re

DATA_FOLDER = "data"
ALLOWED_THREAD_ID = 25
PAGE_SIZE = 5
MAX_RESULTS_PER_BOOK = 5
OPEN_CONTEXT = 1800

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
URL = f"https://api.telegram.org/bot{TOKEN}/"

BOOKS = {}
SEARCH_CACHE = {}  # только для хранения query+results на время пагинации


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
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg_post("sendMessage", payload)


def edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
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


def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


def make_open_text_with_highlight(content, match_start, match_end, context=OPEN_CONTEXT):
    left = max(0, match_start - context)
    right = min(len(content), match_end + context)

    before = format_open_chunk(content[left:match_start])
    found = content[match_start:match_end]
    after = format_open_chunk(content[match_end:right])

    prefix = "…\n" if left > 0 else ""
    suffix = "\n…" if right < len(content) else ""

    return (
        prefix
        + escape_html(before)
        + " ‣ <b>" + escape_html(found) + "</b> ◂ "
        + escape_html(after)
        + suffix
    )[:4000]


def is_noise_snippet(snippet):
    s = normalize_text(snippet)
    noise_markers = ["список основных понятий", "глоссарии", "глоссарий", "указатель", "содержание", "оглавление"]
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


def encode_filename(filename):
    """Сокращаем имя файла для callback_data: убираем расширения."""
    # Используем индекс из BOOKS чтобы не хранить длинное имя
    keys = list(BOOKS.keys())
    if filename in keys:
        return str(keys.index(filename))
    return "0"


def decode_filename(idx_str):
    """Восстанавливаем имя файла по индексу."""
    try:
        idx = int(idx_str)
        keys = list(BOOKS.keys())
        if 0 <= idx < len(keys):
            return keys[idx]
    except ValueError:
        pass
    return None


def build_page_text(query, results, page):
    total = len(results)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    lines = [
        f"Найдено по запросу: <b>{escape_html(query)}</b>",
        f"Показаны {start + 1}–{end} из {total}\n"
    ]

    for idx, item in enumerate(results[start:end], start=start + 1):
        lines.append(f"{idx}. <i>{escape_html(item['filename'])}</i>")
        lines.append(escape_html(item["snippet"]))
        lines.append("")

    lines.append("Открыть фрагмент: нажми кнопку с номером ниже")
    return "\n".join(lines), total_pages


def build_pagination_keyboard(results, page, total_pages):
    """
    Кнопки открытия теперь хранят filename_idx:start:end прямо в callback_data,
    чтобы не зависеть от кэша при перезапуске сервера.
    """
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(results))

    keyboard = []

    open_row = []
    for idx in range(start, end):
        item = results[idx]
        fidx = encode_filename(item["filename"])
        # open2:fidx:start:end
        open_row.append({
            "text": str(idx + 1),
            "callback_data": f"open2:{fidx}:{item['start']}:{item['end']}"
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


def build_reader_keyboard(filename, current_left, current_right, match_start, match_end):
    content_len = len(BOOKS.get(filename, ""))
    fidx = encode_filename(filename)
    keyboard = []
    nav_row = []

    if current_left > 0:
        new_left = max(0, current_left - OPEN_CONTEXT)
        nav_row.append({
            "text": "⬆️ Читать выше",
            "callback_data": f"scroll:{fidx}:{new_left}:{current_left}:{match_start}:{match_end}"
        })

    if current_right < content_len:
        new_right = min(content_len, current_right + OPEN_CONTEXT)
        nav_row.append({
            "text": "⬇️ Читать ниже",
            "callback_data": f"scroll:{fidx}:{current_right}:{new_right}:{match_start}:{match_end}"
        })

    if nav_row:
        keyboard.append(nav_row)

    return {"inline_keyboard": keyboard} if keyboard else None


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

            # --- Открыть фрагмент (новый формат, без кэша) ---
            if callback_data.startswith("open2:"):
                parts = callback_data.split(":")
                # open2:fidx:start:end
                if len(parts) != 4:
                    answer_callback_query(callback_id, "Ошибка кнопки.")
                    return "ok"
                _, fidx, start_s, end_s = parts
                filename = decode_filename(fidx)
                if not filename:
                    answer_callback_query(callback_id, "Файл не найден.")
                    return "ok"
                try:
                    match_start = int(start_s)
                    match_end = int(end_s)
                except ValueError:
                    answer_callback_query(callback_id, "Ошибка позиции.")
                    return "ok"

                content = BOOKS.get(filename, "")
                if not content:
                    answer_callback_query(callback_id, "Не удалось открыть файл.")
                    return "ok"

                left = max(0, match_start - OPEN_CONTEXT)
                right = min(len(content), match_end + OPEN_CONTEXT)

                full_text = make_open_text_with_highlight(content, match_start, match_end)
                header = f"📖 <i>{escape_html(filename)}</i>\n\n"
                keyboard = build_reader_keyboard(filename, left, right, match_start, match_end)
                send_message(chat_id, header + full_text, message_thread_id, keyboard)
                answer_callback_query(callback_id)
                return "ok"

            # --- Навигация внутри текста ---
            if callback_data.startswith("scroll:"):
                parts = callback_data.split(":")
                # scroll:fidx:left:right:match_start:match_end
                if len(parts) != 6:
                    answer_callback_query(callback_id, "Ошибка навигации.")
                    return "ok"
                _, fidx, left_s, right_s, ms_s, me_s = parts
                filename = decode_filename(fidx)
                if not filename:
                    answer_callback_query(callback_id, "Файл не найден.")
                    return "ok"
                try:
                    left = int(left_s)
                    right = int(right_s)
                    match_start = int(ms_s)
                    match_end = int(me_s)
                except ValueError:
                    answer_callback_query(callback_id, "Ошибка навигации.")
                    return "ok"

                content = BOOKS.get(filename, "")
                if not content:
                    answer_callback_query(callback_id, "Файл не найден.")
                    return "ok"

                chunk = format_open_chunk(content[left:right].strip())
                prefix = "…\n" if left > 0 else ""
                suffix = "\n…" if right < len(content) else ""
                text_out = prefix + escape_html(chunk) + suffix

                keyboard = build_reader_keyboard(filename, left, right, match_start, match_end)
                edit_message(chat_id, message_id, text_out, keyboard)
                answer_callback_query(callback_id)
                return "ok"

            # --- Пагинация (требует кэша) ---
            if callback_data.startswith("page:"):
                session_key = (chat_id, message_thread_id)
                cached = SEARCH_CACHE.get(session_key)
                if not cached:
                    answer_callback_query(callback_id, "Поиск устарел. Запусти /find ещё раз.")
                    return "ok"
                try:
                    page = int(callback_data.split(":")[1])
                except ValueError:
                    answer_callback_query(callback_id, "Некорректная страница.")
                    return "ok"

                query = cached["query"]
                results = cached["results"]
                text_out, total_pages = build_page_text(query, results, page)
                keyboard = build_pagination_keyboard(results, page, total_pages)
                edit_message(chat_id, message_id, text_out, keyboard)
                answer_callback_query(callback_id)
                return "ok"

            answer_callback_query(callback_id, "Неизвестная команда кнопки.")
            return "ok"

        # --- Обычные сообщения ---
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
                "<b>Команды:</b>\n"
                "/help — инструкция\n"
                "/find &lt;запрос&gt; — найти слово или фразу во всех книгах\n\n"
                "<b>После поиска:</b>\n"
                "— листай результаты кнопками ⬅️ Вперёд ➡️\n"
                "— нажми номер чтобы открыть фрагмент с выделенным словом\n"
                "— в открытом тексте нажми ⬆️ или ⬇️ чтобы читать выше/ниже\n"
                "— можно открыть несколько результатов одновременно\n\n"
                "<b>Пример:</b>\n"
                "/find асана"
            )
            send_message(chat_id, help_text, message_thread_id)

        elif text.startswith("/find"):
            query = text.replace("/find", "", 1).strip()
            if not query:
                send_message(chat_id, "Напиши запрос после команды.\nПример: /find асана", message_thread_id)
            else:
                results = find_matches(query)
                SEARCH_CACHE[session_key] = {"query": query, "results": results}

                if not results:
                    send_message(chat_id, "Ничего не найдено.", message_thread_id)
                else:
                    page = 0
                    text_out, total_pages = build_page_text(query, results, page)
                    keyboard = build_pagination_keyboard(results, page, total_pages)
                    send_message(chat_id, text_out, message_thread_id, keyboard)

        else:
            send_message(chat_id, "Пожалуйста, используй команды.\nНапиши /help для инструкции.", message_thread_id)

        return "ok"

    except Exception as e:
        print("WEBHOOK ERROR:", str(e), flush=True)
        return "error", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
