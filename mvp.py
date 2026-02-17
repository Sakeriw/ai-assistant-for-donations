import requests
import time
import asyncio
import edge_tts
from playsound import playsound
import os
import json
import threading
import uuid
from queue import PriorityQueue
from dotenv import load_dotenv
import os

load_dotenv()


# =========================
# НАСТРОЙКИ
# =========================

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
VOICE = os.getenv("VOICE", "ru-RU-SvetlanaNeural")
PERSONALITY_MODE = os.getenv("PERSONALITY_MODE", "troll")
# calm | troll | toxic | philosopher
TOKENS_FILE = "tokens.json"

if not CLIENT_ID or not CLIENT_SECRET:
    raise Exception("CLIENT_ID или CLIENT_SECRET не найдены в .env")

print("Бот запущен.")

# =========================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# =========================

donation_queue = PriorityQueue()
processed_ids = set()
last_donation_id = None
tts_queue = PriorityQueue()

# =========================
# TOKENS
# =========================


def tts_worker():
    while True:
        try:
            priority, text = tts_queue.get()

            file_name = f"voice_{uuid.uuid4().hex}.mp3"

            # Пишем overlay ПЕРЕД озвучкой
            with open("overlay.txt", "w", encoding="utf-8") as f:
                f.write(text)

            async def speak_async():
                communicate = edge_tts.Communicate(text, VOICE)
                await communicate.save(file_name)

            asyncio.run(speak_async())

            playsound(file_name)

            # Чистим overlay ТОЛЬКО после окончания звука
            with open("overlay.txt", "w", encoding="utf-8") as f:
                f.write("")

            if os.path.exists(file_name):
                os.remove(file_name)

            tts_queue.task_done()

        except Exception as e:
            print("Ошибка TTS worker:", e)


threading.Thread(target=tts_worker, daemon=True).start()


def load_tokens():
    with open(TOKENS_FILE, "r") as f:
        return json.load(f)


def save_tokens(data):
    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f, indent=4)


def refresh_access_token():
    tokens = load_tokens()

    response = requests.post(
        "https://www.donationalerts.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }
    )

    if response.status_code != 200:
        print("Ошибка обновления токена:", response.text)
        return False

    new_data = response.json()

    tokens["access_token"] = new_data["access_token"]
    tokens["refresh_token"] = new_data["refresh_token"]
    tokens["expires_at"] = int(time.time()) + new_data["expires_in"] - 60

    save_tokens(tokens)
    print("Токен обновлён.")
    return True


def get_valid_access_token():
    tokens = load_tokens()

    if time.time() > tokens.get("expires_at", 0):
        refresh_access_token()
        tokens = load_tokens()

    return tokens["access_token"]

# =========================
# TTS (ПАРАЛЛЕЛЬНЫЙ)
# =========================


async def speak_async(text):
    file_name = f"voice_{uuid.uuid4().hex}.mp3"

    try:
        communicate = edge_tts.Communicate(text, VOICE)
        await communicate.save(file_name)

        playsound(file_name)

    finally:
        if os.path.exists(file_name):
            try:
                os.remove(file_name)
            except:
                pass


# def speak_threaded(text):
#     def runner():
#         try:
#             asyncio.run(speak_async(text))
#         except Exception as e:
#             print("Ошибка TTS:", e)

#     threading.Thread(target=runner, daemon=True).start()

# =========================
# ЛИЧНОСТИ
# =========================


def build_prompt(username, text, amount):

    if amount >= 1000:
        mode = "rage"
    elif amount >= 200:
        mode = "toxic"
    elif amount >= 5:
        mode = "troll"
    else:
        mode = "calm"

    personalities = {
        "calm": "Спокойный и харизматичный ассистент стримера.",
        "troll": "Интеллектуальный тролль с тонким сарказмом.",
        "toxic": "Язвительный и доминирующий, но без мата.",
        "philosopher": "Ироничный философ с насмешкой.",
        "rage": "Доминирующий альфа, уверенный и жёсткий."
    }

    style = personalities.get(mode, personalities["troll"])

    return f"""
Ты — {style}

Правила:
- 1-3 предложения
- Не повторяй текст
- Реагируй конкретно
- Разговорный на русском

Донатер: {username}
Сумма: {amount}
Сообщение: {text}

Ответ:
"""

# =========================
# ГЕНЕРАЦИЯ
# =========================


def generate_reply(text, username, amount):

    prompt = build_prompt(username, text, amount)

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=60
        )

        return response.json()["response"].strip()

    except Exception:
        return "Мой интеллект сейчас перезагружается."

# =========================
# ЛОГИРОВАНИЕ
# =========================


def log_donation(username, text, amount, reply):
    with open("donation_log.txt", "a", encoding="utf-8") as f:
        f.write(f"{time.ctime()} | {username} | {amount} | {text} | {reply}\n")

# =========================
# ПОЛУЧЕНИЕ ДОНАТОВ
# =========================


def get_donations():
    global last_donation_id

    access_token = get_valid_access_token()

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        "https://www.donationalerts.com/api/v1/alerts/donations",
        headers=headers
    )

    if response.status_code == 401:
        if refresh_access_token():
            return get_donations()
        return []

    if response.status_code != 200:
        print("Ошибка API:", response.text)
        return []

    data = response.json()["data"]

    new_donations = []

    for donation in data:
        donation_id = donation["id"]

        if donation_id in processed_ids:
            continue

        if last_donation_id is None:
            last_donation_id = donation_id
            continue

        if donation_id > last_donation_id:
            new_donations.append(donation)

    if data:
        last_donation_id = data[0]["id"]

    return new_donations

# =========================
# WORKER ОЧЕРЕДИ
# =========================


def donation_worker():
    while True:
        try:
            priority, donation_id, username, text, amount = donation_queue.get()

            reply = generate_reply(text, username, amount)

            print(f"\n[{amount}] {username}: {text}")
            print("Ответ:", reply)

            tts_queue.put((priority, reply))
            log_donation(username, text, amount, reply)

        except Exception as e:
            print("Ошибка worker:", e)

# =========================
# ЗАПУСК WORKER
# =========================


threading.Thread(target=donation_worker, daemon=True).start()

# =========================
# ОСНОВНОЙ ЦИКЛ
# =========================

print("Отслеживание донатов запущено.")


def test_mode():
    test_donations = [
        (1, "TestUser1", "Привет, ты вообще шаришь?", 3),
        (2, "RichBoy", "Объясни квантовую физику за 10 секунд", 50),
        (3, "BigDonater", "Я твой батя теперь", 1200),
    ]

    for donation_id, username, message, amount in test_donations:
        donation_queue.put((-amount, donation_id, username, message, amount))


while True:
    try:
        donations = get_donations()

        for donation in donations:
            donation_id = donation["id"]

            if donation_id in processed_ids:
                continue

            processed_ids.add(donation_id)

            username = donation.get("username", "Аноним")
            message = donation.get("message", "")
            amount = donation.get("amount", 0)

            if message:
                donation_queue.put(
                    (-amount, donation_id, username, message, amount))

        time.sleep(5)

    except Exception as e:
        print("Глобальная ошибка:", e)
        time.sleep(5)
