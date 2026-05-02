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
SEARCH_CACHE = {}
TOPICS = []


# ──────────────────────────────────────────────
# ЗАГРУЗКА КНИГ
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
# TELEGRAM API
# ──────────────────────────────────────────────

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
    return tg_post("sendMessage", payload)


def edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg_post("editMessageText", payload)


def answer_callback_query(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    tg_post("answerCallbackQuery", payload)


def fetch_forum_topics(chat_id):
    """
    Получает список тем форума через getForumTopics.
    Возвращает список (thread_id, name) или [] если не удалось.
    """
    global TOPICS
    try:
        resp = requests.get(
            URL + "getForumTopics",
            params={"chat_id": chat_id, "limit": 100},
            timeout=15
        )
        data = resp.json()
        print("getForumTopics RESPONSE:", json.dumps(data, ensure_ascii=False), flush=True)
        if data.get("ok"):
            topics = []
            for t in data["result"].get("topics", []):
                tid  = t.get("message_thread_id")
                name = t.get("name", f"Тема {tid}")
                if tid and tid != ALLOWED_THREAD_ID:
                    topics.append((tid, name))
            TOPICS = topics
            print(f"TOPICS LOADED: {TOPICS}", flush=True)
            return topics
    except Exception as e:
        print(f"fetch_forum_topics ERROR: {e}", flush=True)
    return []


# ──────────────────────────────────────────────
# УТИЛИТЫ ТЕКСТА
# ──────────────────────────────────────────────

def normalize_text(text):
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def build_word_pattern(query):
    query = re.escape(query.strip().lower())
    return re.compile(rf"(?<!\w){query}\w*", re.IGNORECASE)


def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def make_snippet_with_highlight(content, start_idx, end_idx, context=180):
    """
    Сниппет для первичного поиска:
    - найденное слово обёрнуто в ✦ ... ✦ + жирный
    - контекст курсивом для мягкого отличия от заголовка
    """
    left  = max(0, start_idx - context)
    right = min(len(content), end_idx + context)

    before = re.sub(r"\s+", " ", content[left:start_idx].replace("\n", " ")).strip()
    found  = content[start_idx:end_idx]
    after  = re.sub(r"\s+", " ", content[end_idx:right].replace("\n", " ")).strip()

    result = "<i>"
    if left > 0:
        result += "…"
    result += escape_html(before)
    result += f"</i> ✦ <b>{escape_html(found)}</b> ✦ <i>"
    result += escape_html(after)
    if right < len(content):
        result += "…"
    result += "</i>"
    return result[:600]


def format_open_chunk(chunk):
    chunk = re.sub(r'\n{3,}', '\n\n', chunk)
    chunk = re.sub(r'\n?(\d+\.\s+[А-ЯA-Z][^\n]+)', r'\n\n\1', chunk)
    chunk = re.sub(r'\s+—\s+', r'\n— ', chunk)
    chunk = re.sub(r'[ \t]+', ' ', chunk)
    chunk = re.sub(r'\n{3,}', '\n\n', chunk)
    return chunk.strip()


def make_open_text_with_highlight(content, match_start, match_end, context=OPEN_CONTEXT):
    """
    Открытый фрагмент:
    - найденный участок обёрнут в ✦ ... ✦ + жирный
    - остальной текст как обычно
    """
    left  = max(0, match_start - context)
    right = min(len(content), match_end + context)

    before = format_open_chunk(content[left:match_start])
    found  = content[match_start:match_end]
    after  = format_open_chunk(content[match_end:right])

    prefix = "…\n" if left > 0 else ""
    suffix = "\n…" if right < len(content) else ""

    return (
        prefix
        + escape_html(before)
        + f" ✦ <b>{escape_html(found)}</b> ✦ "
        + escape_html(after)
        + suffix
    )[:4000]


def is_noise_snippet(snippet):
    s = normalize_text(snippet)
    noise_markers = [
        "список основных понятий", "глоссарии", "глоссарий",
        "указатель", "содержание", "оглавление"
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
            end_idx   = match.end()

            plain = content[max(0, start_idx - 180):min(len(content), end_idx + 180)]
            plain = re.sub(r"\s+", " ", plain.replace("\n", " ")).strip()[:450]

            if is_noise_snippet(plain):
                continue
            dedupe_key = normalize_text(plain)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            results.append({
                "filename": filename,
                "start": start_idx,
                "end": end_idx,
            })
            file_count += 1
            if file_count >= MAX_RESULTS_PER_BOOK:
                break

    return results


# ──────────────────────────────────────────────
# ENCODE / DECODE FILENAME
# ──────────────────────────────────────────────

def encode_filename(filename):
    keys = list(BOOKS.keys())
    if filename in keys:
        return str(keys.index(filename))
    return "0"


def decode_filename(idx_str):
    try:
        idx = int(idx_str)
        keys = list(BOOKS.keys())
        if 0 <= idx < len(keys):
            return keys[idx]
    except ValueError:
        pass
    return None


# ──────────────────────────────────────────────
# КЛАВИАТУРЫ И СТРАНИЦЫ
# ──────────────────────────────────────────────

def build_page_text(query, results, page):
    total = len(results)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end   = min(start + PAGE_SIZE, total)

    lines = [
        f"Найдено по запросу: <b>{escape_html(query)}</b>",
        f"Показаны {start + 1}–{end} из {total}\n"
    ]

    for idx, item in enumerate(results[start:end], start=start + 1):
        content     = BOOKS.get(item["filename"], "")
        highlighted = make_snippet_with_highlight(content, item["start"], item["end"])
        lines.append(f"{idx}. <i>{escape_html(item['filename'])}</i>")
        lines.append(highlighted)
        lines.append("")

    lines.append("Открыть фрагмент: нажми кнопку с номером ниже")
    return "\n".join(lines), total_pages


def build_pagination_keyboard(results, page, total_pages):
    start = page * PAGE_SIZE
    end   = min(start + PAGE_SIZE, len(results))

    keyboard = []

    open_row = []
    for idx in range(start, end):
        item = results[idx]
        fidx = encode_filename(item["filename"])
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
    fidx        = encode_filename(filename)
    keyboard    = []
    nav_row     = []

    if current_left > 0:
        new_left = max(0, current_left - OPEN_CONTEXT)
        nav_row.append({
            "text": "⬆️ Выше",
            "callback_data": f"scroll:{fidx}:{new_left}:{current_left}:{match_start}:{match_end}"
        })
    if current_right < content_len:
        new_right = min(content_len, current_right + OPEN_CONTEXT)
        nav_row.append({
            "text": "⬇️ Ниже",
            "callback_data": f"scroll:{fidx}:{current_right}:{new_right}:{match_start}:{match_end}"
        })
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([{
        "text": "📤 Репост в тему",
        "callback_data": f"repost_pick:{fidx}:{match_start}:{match_end}"
    }])

    return {"inline_keyboard": keyboard} if keyboard else None


def build_topic_keyboard(chat_id, fidx, match_start, match_end):
    """
    Строит клавиатуру из реальных тем форума.
    Если темы ещё не загружены — загружает сейчас.
    """
    global TOPICS
    if not TOPICS:
        fetch_forum_topics(chat_id)

    keyboard = []
    row = []
    for tid, name in TOPICS:
        cb = f"repost_do:{fidx}:{match_start}:{match_end}:{tid}"
        row.append({"text": name[:20], "callback_data": cb})
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([{"text": "❌ Отмена", "callback_data": "repost_cancel"}])
    return {"inline_keyboard": keyboard}


# ──────────────────────────────────────────────
# WEBHOOK
# ──────────────────────────────────────────────

@app.route("/", methods=["GET"])
def home():
    return "Bot is running!"


@app.route("/", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        print("INCOMING UPDATE:", json.dumps(data, ensure_ascii=False), flush=True)

        # ── CALLBACK QUERY ──
        callback_query = data.get("callback_query")
        if callback_query:
            callback_id      = callback_query.get("id")
            callback_data    = callback_query.get("data", "")
            callback_message = callback_query.get("message", {})

            chat_id           = callback_message.get("chat", {}).get("id")
            message_id        = callback_message.get("message_id")
            message_thread_id = callback_message.get("message_thread_id")

            print("CALLBACK DATA:", callback_data, flush=True)

            if message_thread_id != ALLOWED_THREAD_ID:
                answer_callback_query(callback_id, "Работает только в теме Справочник.")
                return "ok"
            if not chat_id or not message_id:
                answer_callback_query(callback_id, "Не удалось обработать кнопку.")
                return "ok"

            # ── Открыть фрагмент ──
            if callback_data.startswith("open2:"):
                parts = callback_data.split(":")
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
                    match_end   = int(end_s)
                except ValueError:
                    answer_callback_query(callback_id, "Ошибка позиции.")
                    return "ok"

                content  = BOOKS.get(filename, "")
                left     = max(0, match_start - OPEN_CONTEXT)
                right    = min(len(content), match_end + OPEN_CONTEXT)

                full_text = make_open_text_with_highlight(content, match_start, match_end)
                header    = f"📖 <i>{escape_html(filename)}</i>\n\n"
                keyboard  = build_reader_keyboard(filename, left, right, match_start, match_end)
                send_message(chat_id, header + full_text, message_thread_id, keyboard)
                answer_callback_query(callback_id)
                return "ok"

            # ── Навигация scroll ──
            if callback_data.startswith("scroll:"):
                parts = callback_data.split(":")
                if len(parts) != 6:
                    answer_callback_query(callback_id, "Ошибка навигации.")
                    return "ok"
                _, fidx, left_s, right_s, ms_s, me_s = parts
                filename = decode_filename(fidx)
                if not filename:
                    answer_callback_query(callback_id, "Файл не найден.")
                    return "ok"
                try:
                    left        = int(left_s)
                    right       = int(right_s)
                    match_start = int(ms_s)
                    match_end   = int(me_s)
                except ValueError:
                    answer_callback_query(callback_id, "Ошибка навигации.")
                    return "ok"

                content  = BOOKS.get(filename, "")
                chunk    = format_open_chunk(content[left:right].strip())
                prefix   = "…\n" if left > 0 else ""
                suffix   = "\n…" if right < len(content) else ""
                text_out = prefix + escape_html(chunk) + suffix

                keyboard = build_reader_keyboard(filename, left, right, match_start, match_end)
                edit_message(chat_id, message_id, text_out, keyboard)
                answer_callback_query(callback_id)
                return "ok"

            # ── Пагинация ──
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

                query    = cached["query"]
                results  = cached["results"]
                text_out, total_pages = build_page_text(query, results, page)
                keyboard = build_pagination_keyboard(results, page, total_pages)
                edit_message(chat_id, message_id, text_out, keyboard)
                answer_callback_query(callback_id)
                return "ok"

            # ── Выбор темы для репоста ──
            if callback_data.startswith("repost_pick:"):
                parts = callback_data.split(":")
                if len(parts) != 4:
                    answer_callback_query(callback_id, "Ошибка.")
                    return "ok"
                _, fidx, ms_s, me_s = parts
                try:
                    match_start = int(ms_s)
                    match_end   = int(me_s)
                except ValueError:
                    answer_callback_query(callback_id, "Ошибка позиции.")
                    return "ok"

                keyboard = build_topic_keyboard(chat_id, fidx, match_start, match_end)

                if not TOPICS:
                    edit_message(
                        chat_id, message_id,
                        "⚠️ Не удалось загрузить темы.\n"
                        "Убедись что бот — администратор группы, затем попробуй снова.",
                        {"inline_keyboard": [[{"text": "❌ Закрыть", "callback_data": "repost_cancel"}]]}
                    )
                    answer_callback_query(callback_id)
                    return "ok"

                edit_message(chat_id, message_id, "📤 Выбери тему для репоста:", keyboard)
                answer_callback_query(callback_id)
                return "ok"

            # ── Выполнить репост ──
            if callback_data.startswith("repost_do:"):
                parts = callback_data.split(":")
                if len(parts) != 5:
                    answer_callback_query(callback_id, "Ошибка.")
                    return "ok"
                _, fidx, ms_s, me_s, tid_s = parts
                filename = decode_filename(fidx)
                if not filename:
                    answer_callback_query(callback_id, "Файл не найден.")
                    return "ok"
                try:
                    match_start   = int(ms_s)
                    match_end     = int(me_s)
                    target_thread = int(tid_s)
                except ValueError:
                    answer_callback_query(callback_id, "Ошибка данных.")
                    return "ok"

                content   = BOOKS.get(filename, "")
                full_text = make_open_text_with_highlight(content, match_start, match_end)
                header    = f"📖 <i>{escape_html(filename)}</i>\n\n"

                send_message(chat_id, header + full_text, target_thread)

                topic_name = next((n for t, n in TOPICS if t == target_thread), f"тему {target_thread}")
                answer_callback_query(callback_id, f"✅ Отправлено в «{topic_name}»")

                left     = max(0, match_start - OPEN_CONTEXT)
                right    = min(len(content), match_end + OPEN_CONTEXT)
                keyboard = build_reader_keyboard(filename, left, right, match_start, match_end)
                edit_message(chat_id, message_id, header + full_text, keyboard)
                return "ok"

            # ── Отмена репоста ──
            if callback_data == "repost_cancel":
                answer_callback_query(callback_id, "Отменено.")
                return "ok"

            answer_callback_query(callback_id, "Неизвестная команда.")
            return "ok"

        # ── ОБЫЧНЫЕ СООБЩЕНИЯ ──
        message = data.get("message") or data.get("edited_message")
        if not message:
            return "ok"

        chat_id           = message.get("chat", {}).get("id")
        text              = (message.get("text") or "").strip()
        message_thread_id = message.get("message_thread_id")

        print("CHAT ID:", chat_id, flush=True)
        print("TEXT:", text, flush=True)
        print("THREAD ID:", message_thread_id, flush=True)

        if message_thread_id != ALLOWED_THREAD_ID:
            return "ok"
        if not chat_id:
            return "ok"

        session_key = (chat_id, message_thread_id)

        # ── /topics — проверка тем ──
        if text.startswith("/topics"):
            topics = fetch_forum_topics(chat_id)
            if topics:
                lines = ["<b>Темы форума:</b>"]
                for tid, name in topics:
                    lines.append(f"• {escape_html(name)} — <code>{tid}</code>")
                send_message(chat_id, "\n".join(lines), message_thread_id)
            else:
                send_message(
                    chat_id,
                    "Темы не найдены. Убедись что бот — администратор группы.",
                    message_thread_id
                )

        elif text.startswith("/help"):
            help_text = (
                "Я бот-справочник по йога-текстам.\n\n"
                "<b>Команды:</b>\n"
                "/help — инструкция\n"
                "/find &lt;запрос&gt; — найти слово или фразу\n"
                "/topics — список тем (для проверки)\n\n"
                "<b>После поиска:</b>\n"
                "— найденное слово выделено ✦ маркерами ✦\n"
                "— нажми номер — откроется фрагмент с выделенным участком\n"
                "— ⬆️ ⬇️ — читать выше/ниже\n"
                "— 📤 Репост в тему — поделиться фрагментом\n\n"
                "<b>Пример:</b> /find асана"
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
                if not TOPICS:
                    fetch_forum_topics(chat_id)

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
