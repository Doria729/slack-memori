import json
import os
import re
import uuid
from datetime import datetime

import httpx
from anthropic import Anthropic
from flask import Flask, request
from slack_sdk import WebClient


def load_dotenv(dotenv_path=".env"):
    if not os.path.exists(dotenv_path):
        return

    with open(dotenv_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value


def load_text_file(file_path):
    with open(file_path, "r", encoding="utf-8") as text_file:
        return text_file.read().strip()


load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = Flask(__name__)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai_compatible").strip().lower()
LLM_MODEL = os.environ.get("LLM_MODEL", os.environ.get("MODEL", "anthropic/claude-sonnet-4.6"))
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", os.environ.get("BASE_URL", "")).rstrip("/")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

if os.environ.get("LLM_API_KEY"):
    LLM_API_KEY = os.environ["LLM_API_KEY"]
elif "openrouter.ai" in LLM_BASE_URL:
    LLM_API_KEY = os.environ.get("openrouter_api_key", "")
else:
    LLM_API_KEY = os.environ.get("test_api_key", "")

MEMO_FILE = os.path.join(os.path.dirname(__file__), "memo.json")

slack_client = WebClient(token=SLACK_BOT_TOKEN)
SYSTEM_PROMPT = load_text_file(os.path.join(os.path.dirname(__file__), "system_prompt.md"))


def read_memo():
    if not os.path.exists(MEMO_FILE):
        return []

    with open(MEMO_FILE, "r", encoding="utf-8") as memo_file:
        return json.load(memo_file)


def write_memo(items):
    with open(MEMO_FILE, "w", encoding="utf-8") as memo_file:
        json.dump(items, memo_file, ensure_ascii=False, indent=2)


def add_memo(content):
    items = read_memo()
    items.append(
        {
            "id": str(uuid.uuid4())[:8],
            "content": content,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    )
    write_memo(items)
    return items


def delete_memo(description):
    items = read_memo()
    if not items:
        return False, []

    candidates = "\n".join([f"{item['id']}: {item['content']}" for item in items])
    prompt = (
        f"以下是备忘列表：\n{candidates}\n\n"
        f"用户说要删除：「{description}」\n"
        "请返回所有匹配的备忘 id，用空格分隔，没有匹配就返回 none"
    )

    match_raw = call_llm_raw(prompt).strip().lower()
    match_ids = [match_id for match_id in match_raw.split() if match_id != "none"]

    if not match_ids:
        return False, items

    new_items = [item for item in items if item["id"].lower() not in match_ids]
    if len(new_items) == len(items):
        return False, items

    write_memo(new_items)
    return True, new_items


def memo_to_bullets(items):
    if not items:
        return "目前没有待办～"

    return "\n".join([f"• {item['content']}  `{item['created_at']}`" for item in items])


def call_llm_raw(user_prompt, system=None):
    if LLM_PROVIDER == "anthropic":
        client = Anthropic(api_key=LLM_API_KEY or ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=LLM_MODEL or "claude-sonnet-4-20250514",
            max_tokens=200,
            system=system or "你是一个助手，只返回被要求的内容。",
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system or "你是一个助手，只返回被要求的内容。"},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 200,
        "temperature": 0,
    }
    chat_url = _build_chat_url()
    with httpx.Client(timeout=30.0) as client:
        response = client.post(chat_url, headers=_llm_headers(), json=payload)
        response.raise_for_status()
    raw_json = response.json()
    content = raw_json["choices"][0]["message"].get("content")
    return (content or "").strip()


def call_llm_with_history(history, system=None):
    if LLM_PROVIDER == "anthropic":
        client = Anthropic(api_key=LLM_API_KEY or ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=LLM_MODEL or "claude-sonnet-4-20250514",
            max_tokens=1000,
            system=system or SYSTEM_PROMPT,
            messages=history,
        )
        return response.content[0].text

    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": system or SYSTEM_PROMPT}] + history,
        "max_tokens": 1000,
        "temperature": 0.3,
    }
    chat_url = _build_chat_url()
    with httpx.Client(timeout=60.0) as client:
        response = client.post(chat_url, headers=_llm_headers(), json=payload)
        response.raise_for_status()
    raw_json = response.json()
    content = raw_json["choices"][0]["message"].get("content")
    return (content or "").strip()


def _build_chat_url():
    if LLM_BASE_URL.endswith("/chat/completions"):
        return LLM_BASE_URL
    if LLM_BASE_URL.endswith("/v1"):
        return f"{LLM_BASE_URL}/chat/completions"
    return f"{LLM_BASE_URL}/v1/chat/completions"


def _llm_headers():
    return {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }


def get_channel_history(channel, limit=10):
    result = slack_client.conversations_history(channel=channel, limit=limit)
    messages = result.get("messages", [])
    messages.reverse()

    history = []
    for msg in messages:
        text = msg.get("text", "")
        if not text:
            continue

        if msg.get("bot_id"):
            history.append({"role": "assistant", "content": text})
        else:
            history.append({"role": "user", "content": text})

    return history


def parse_llm_json(raw):
    try:
        cleaned = re.sub(r"```json|```", "", raw).strip()
        return json.loads(cleaned)
    except Exception:
        return {"intent": "chat", "content": "", "reply": raw}


def generate_reply(user_text, channel):
    intent_prompt = (
        f"用户说：「{user_text}」\n"
        "请判断意图，只返回 memo / delete / query / chat 之一。\n"
        "规则：\n"
        "- memo：要做某事、记得某事、别忘了某事\n"
        "- delete：做完了、完成了、不用做了、删掉、划掉\n"
        "- query：问有什么待办、查看待办、还有什么没做\n"
        "- chat：闲聊、提问、吐槽\n"
        "例子：「喝完啦」→ delete，「记得买菜」→ memo，「我有什么待办」→ query，「今天好累」→ chat"
    )
    raw_intent = call_llm_raw(intent_prompt).strip().lower()
    intent = raw_intent.split()[0] if raw_intent else ""
    if intent not in ("memo", "delete", "query", "chat"):
        intent = "chat"

    if intent == "chat":
        history = get_channel_history(channel, limit=10)
        if history and history[-1].get("role") == "user":
            history.pop()
        history.append({"role": "user", "content": user_text})
        raw = call_llm_with_history(history, system=SYSTEM_PROMPT)
        parsed = parse_llm_json(raw)
        return parsed.get("reply", raw)

    current_memo = read_memo()
    system_with_memo = SYSTEM_PROMPT + f"\n\n当前备忘列表：\n{memo_to_bullets(current_memo)}"
    raw = call_llm_with_history(
        [{"role": "user", "content": user_text}],
        system=system_with_memo,
    )
    parsed = parse_llm_json(raw)
    reply = parsed.get("reply", raw)

    if intent == "query":
        return reply

    if intent == "memo":
        content = parsed.get("content", "")
        if content:
            add_memo(content)
            items = read_memo()
            reply += f"\n\n📌 当前待办：\n{memo_to_bullets(items)}"
    elif intent == "delete":
        content = parsed.get("content", "")
        if content:
            success, remaining = delete_memo(content)
            if success:
                reply += f"\n\n📌 当前待办：\n{memo_to_bullets(remaining)}"
            else:
                reply += "\n\n（没找到匹配的条目，没删掉任何东西）"

    return reply


@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.json or {}

    if data.get("type") == "url_verification":
        return data.get("challenge", "")

    if request.headers.get("X-Slack-Retry-Num"):
        return "ok"

    event = data.get("event", {})
    event_type = event.get("type")

    if event_type in ("message", "app_mention"):
        if event.get("bot_id"):
            return "ok"

        user_text = event.get("text", "")
        channel = event.get("channel")
        if not user_text or not channel:
            return "ok"

        try:
            reply = generate_reply(user_text, channel)
        except Exception as exc:
            reply = f"出错了：{exc}"

        slack_client.chat_postMessage(channel=channel, text=reply)

    return "ok"


if __name__ == "__main__":
    if not SLACK_BOT_TOKEN:
        raise RuntimeError("Please set SLACK_BOT_TOKEN before starting the app.")

    app.run(port=5000)
