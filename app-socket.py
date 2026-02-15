import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
CODEX_ACTION_FOLDER = os.getenv("CODEX_ACTION_FOLDER")
CODEX_SESSION_ID = os.getenv("CODEX_SESSION_ID")
CODEX_SESSION_SCOPE = os.getenv("CODEX_SESSION_SCOPE", "channel").lower()
CODEX_SKIP_GIT_REPO_CHECK = os.getenv("CODEX_SKIP_GIT_REPO_CHECK", "true").lower() == "true"
CODEX_SANDBOX_MODE = os.getenv("CODEX_SANDBOX_MODE", "read-only")
CODEX_ACCEPT_REACTION = os.getenv("CODEX_ACCEPT_REACTION", "eyes")
CODEX_CONTEXT_DB_PATH = os.getenv("CODEX_CONTEXT_DB_PATH", "slack-context.db")
CODEX_CONTEXT_LIMIT = int(os.getenv("CODEX_CONTEXT_LIMIT", "80"))
CODEX_CONTEXT_RETENTION_DAYS = int(os.getenv("CODEX_CONTEXT_RETENTION_DAYS", "14"))
CODEX_CATCHUP_DEFAULT_LIMIT = int(os.getenv("CODEX_CATCHUP_DEFAULT_LIMIT", "500"))
CODEX_CATCHUP_MAX_LIMIT = int(os.getenv("CODEX_CATCHUP_MAX_LIMIT", "2000"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app-socket")
file_handler = logging.FileHandler("app-socket.log", encoding="utf-8")
file_handler.setLevel(os.getenv("LOG_LEVEL", "INFO"))
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logger.addHandler(file_handler)
logger.propagate = True

if os.getenv("SLACK_DEBUG", "false").lower() == "true":
    logging.getLogger("slack_bolt").setLevel(logging.DEBUG)
    logging.getLogger("slack_sdk").setLevel(logging.DEBUG)

if not SLACK_BOT_TOKEN:
    raise RuntimeError("SLACK_BOT_TOKEN is missing")
if not SLACK_APP_TOKEN:
    raise RuntimeError("SLACK_APP_TOKEN is missing")

app = App(token=SLACK_BOT_TOKEN)
db_lock = threading.Lock()
BOT_USER_ID = None


def with_db():
    return sqlite3.connect(CODEX_CONTEXT_DB_PATH)


def init_context_db():
    with db_lock:
        with with_db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_messages (
                    channel TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    thread_ts TEXT,
                    user_id TEXT,
                    text TEXT,
                    subtype TEXT,
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (channel, ts)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_channel_messages_channel_ts ON channel_messages(channel, ts)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_channel_messages_thread ON channel_messages(channel, thread_ts, ts)"
            )
            conn.commit()


def sanitize_text(text):
    if not text:
        return ""
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    text = text.replace("\r\n", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    cleaned = "\n".join([line for line in lines if line])
    return cleaned[:2000]


def prune_old_messages(conn):
    cutoff = int(time.time()) - (CODEX_CONTEXT_RETENTION_DAYS * 24 * 60 * 60)
    conn.execute("DELETE FROM channel_messages WHERE updated_at < ?", (cutoff,))


def upsert_channel_message(channel, ts, thread_ts, user_id, text, subtype, is_deleted):
    now = int(time.time())
    with db_lock:
        with with_db() as conn:
            conn.execute(
                """
                INSERT INTO channel_messages (channel, ts, thread_ts, user_id, text, subtype, is_deleted, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, ts) DO UPDATE SET
                    thread_ts=excluded.thread_ts,
                    user_id=excluded.user_id,
                    text=excluded.text,
                    subtype=excluded.subtype,
                    is_deleted=excluded.is_deleted,
                    updated_at=excluded.updated_at
                """,
                (channel, ts, thread_ts, user_id, sanitize_text(text), subtype, 1 if is_deleted else 0, now),
            )
            prune_old_messages(conn)
            conn.commit()


def store_message_event(event):
    channel = event.get("channel")
    if not channel:
        return
    subtype = event.get("subtype")

    if subtype == "message_changed":
        msg = event.get("message", {})
        upsert_channel_message(
            channel=channel,
            ts=msg.get("ts", ""),
            thread_ts=msg.get("thread_ts") or msg.get("ts"),
            user_id=msg.get("user"),
            text=msg.get("text", ""),
            subtype=subtype,
            is_deleted=False,
        )
        return

    if subtype == "message_deleted":
        deleted_ts = event.get("deleted_ts")
        if deleted_ts:
            upsert_channel_message(
                channel=channel,
                ts=deleted_ts,
                thread_ts=event.get("previous_message", {}).get("thread_ts") or deleted_ts,
                user_id=event.get("previous_message", {}).get("user"),
                text="(deleted)",
                subtype=subtype,
                is_deleted=True,
            )
        return

    is_own_bot_message = bool(BOT_USER_ID and event.get("user") == BOT_USER_ID)

    # 通常投稿と自身の bot_message だけ取り込む
    if subtype not in (None, "bot_message"):
        return
    # 他ボットの投稿は除外
    if event.get("bot_id") and not is_own_bot_message:
        return
    if not event.get("user") and not is_own_bot_message:
        return

    ts = event.get("ts")
    if not ts:
        return
    upsert_channel_message(
        channel=channel,
        ts=ts,
        thread_ts=event.get("thread_ts") or ts,
        user_id=event.get("user"),
        text=event.get("text", ""),
        subtype=subtype or "message",
        is_deleted=False,
    )


def fetch_context_lines(channel, thread_ts):
    with db_lock:
        with with_db() as conn:
            thread_rows = conn.execute(
                """
                SELECT ts, user_id, text, is_deleted
                FROM channel_messages
                WHERE channel = ? AND thread_ts = ?
                ORDER BY CAST(ts AS REAL) DESC
                LIMIT ?
                """,
                (channel, thread_ts, CODEX_CONTEXT_LIMIT),
            ).fetchall()
            channel_rows = conn.execute(
                """
                SELECT ts, user_id, text, is_deleted
                FROM channel_messages
                WHERE channel = ?
                ORDER BY CAST(ts AS REAL) DESC
                LIMIT ?
                """,
                (channel, CODEX_CONTEXT_LIMIT),
            ).fetchall()

    lines = []
    seen_ts = set()
    for ts, user_id, text, is_deleted in reversed(thread_rows):
        seen_ts.add(ts)
        if is_deleted:
            lines.append(f"[thread ts={ts}] (deleted)")
        else:
            lines.append(f"[thread ts={ts} user={user_id}] {text}")

    for ts, user_id, text, is_deleted in reversed(channel_rows):
        if ts in seen_ts:
            continue
        if is_deleted:
            lines.append(f"[channel ts={ts}] (deleted)")
        else:
            lines.append(f"[channel ts={ts} user={user_id}] {text}")

    return lines[-CODEX_CONTEXT_LIMIT:]


def parse_control_command(user_text):
    m = re.fullmatch(r"(?:/)?catchup(?:\s+(\d+))?", user_text.strip(), flags=re.IGNORECASE)
    if not m:
        return None
    req_limit = int(m.group(1)) if m.group(1) else CODEX_CATCHUP_DEFAULT_LIMIT
    req_limit = max(1, min(req_limit, CODEX_CATCHUP_MAX_LIMIT))
    return {"name": "catchup", "limit": req_limit}


def store_history_message(channel, message):
    subtype = message.get("subtype")
    is_own_bot_message = bool(BOT_USER_ID and message.get("user") == BOT_USER_ID)

    if subtype not in (None, "bot_message"):
        return False
    if message.get("bot_id") and not is_own_bot_message:
        return False
    if not message.get("user") and not is_own_bot_message:
        return False

    ts = message.get("ts")
    if not ts:
        return False
    upsert_channel_message(
        channel=channel,
        ts=ts,
        thread_ts=message.get("thread_ts") or ts,
        user_id=message.get("user"),
        text=message.get("text", ""),
        subtype=subtype or "message",
        is_deleted=False,
    )
    return True


def catchup_channel_history(channel, limit):
    fetched = 0
    stored = 0
    cursor = None

    while fetched < limit:
        page_limit = min(200, limit - fetched)
        resp = app.client.conversations_history(channel=channel, limit=page_limit, cursor=cursor)
        messages = resp.get("messages", [])
        if not messages:
            break
        fetched += len(messages)

        for msg in reversed(messages):
            if store_history_message(channel, msg):
                stored += 1

        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    return {"fetched": fetched, "stored": stored}


def resolve_session_target(channel, thread_ts):
    # セッションは既定でチャネル単位に分離する
    if CODEX_SESSION_SCOPE == "channel":
        return f"slack-channel-{channel}"
    if CODEX_SESSION_SCOPE == "thread":
        return f"slack-thread-{channel}-{str(thread_ts).replace('.', '-')}"
    if CODEX_SESSION_SCOPE == "global":
        return CODEX_SESSION_ID
    return f"slack-channel-{channel}"


@app.middleware
def log_incoming_events(logger, body, next):
    event_type = body.get("event", {}).get("type")
    logger.info("Incoming event type=%s", event_type)
    next()


@app.error
def handle_bolt_errors(error, body, logger):
    logger.exception("Unhandled Bolt error: %s body=%s", error, body)


@app.event("app_mention")
def handle_app_mention(body, say):
    event = body["event"]
    text = event.get("text", "")
    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event.get("ts")

    # 先頭のメンション群を除去して実際の依頼文を取り出す
    user_text = re.sub(r"^(?:<@[^>]+>\s*)+", "", text).strip()
    # メンションしか含まれない場合は空指示として扱う
    if user_text and re.fullmatch(r"(?:<@[^>]+>\s*)+", user_text):
        user_text = ""
    if not user_text:
        logger.info("Empty instruction on app_mention channel=%s thread_ts=%s", channel, thread_ts)
        say(text="指示が空です。メンションの後に依頼内容を書いてください。", thread_ts=thread_ts)
        return

    command = parse_control_command(user_text)
    if command and command["name"] == "catchup":
        limit = command["limit"]
        logger.info("Manual catchup triggered channel=%s limit=%s", channel, limit)
        say(text=f"キャッチアップを開始します（最大 {limit} 件）。", thread_ts=thread_ts)
        try:
            result = catchup_channel_history(channel=channel, limit=limit)
            say(
                text=(
                    f"キャッチアップ完了: 取得 {result['fetched']} 件 / 保存 {result['stored']} 件。"
                    f"（channel={channel}）"
                ),
                thread_ts=thread_ts,
            )
        except Exception:
            logger.exception("Catchup failed channel=%s limit=%s", channel, limit)
            say(
                text="キャッチアップに失敗しました。`channels:history` スコープやBot参加状態を確認してください。",
                thread_ts=thread_ts,
            )
        return

    logger.info("Received app_mention channel=%s thread_ts=%s", channel, thread_ts)
    try:
        app.client.reactions_add(
            channel=channel,
            timestamp=event.get("ts"),
            name=CODEX_ACCEPT_REACTION,
        )
    except Exception:
        logger.exception("Failed to add reaction name=%s", CODEX_ACCEPT_REACTION)

    if not CODEX_ACTION_FOLDER:
        logger.error("Missing CODEX_ACTION_FOLDER")
        say(
            text="環境変数 `CODEX_ACTION_FOLDER` が未設定です。",
            thread_ts=thread_ts,
        )
        return
    if not Path(CODEX_ACTION_FOLDER).is_dir():
        logger.error("CODEX_ACTION_FOLDER is not a directory: %s", CODEX_ACTION_FOLDER)
        say(
            text=f"`CODEX_ACTION_FOLDER` が無効です: `{CODEX_ACTION_FOLDER}`",
            thread_ts=thread_ts,
        )
        return
    if shutil.which("codex") is None:
        logger.error("`codex` command not found in PATH")
        say(text="`codex` コマンドが見つかりません。PATH を確認してください。", thread_ts=thread_ts)
        return

    session_target = resolve_session_target(channel, thread_ts)
    if CODEX_SESSION_SCOPE == "global" and not session_target:
        logger.error("CODEX_SESSION_SCOPE=global but CODEX_SESSION_ID is missing")
        say(
            text="`CODEX_SESSION_SCOPE=global` の場合は `CODEX_SESSION_ID` が必要です。",
            thread_ts=thread_ts,
        )
        return

    context_lines = fetch_context_lines(channel, thread_ts)
    context_block = "\n".join(context_lines) if context_lines else "(no stored context)"

    prompt = (
        f"Slack channel_id={channel}, thread_ts={thread_ts} の依頼です。"
        f"このワークスペース内の関連ファイルを読み取り、質問に回答してください。"
        f"回答するにあたって参照できる情報は、世の中一般的とされる知識に加え、Slack MCPを使って検索できる範囲に限ります。"
        f"インターネット上の情報は、必要に応じて検索してください。"
        f"ファイル作成・編集・削除、git操作、コミット、PR作成は禁止です。"
        f"ローカルファイルシステムの内容は参照・操作は一切できません。"
        f"回答にあたって参照した投稿（かつ、回答内容に直接関連があるもの）があればそのリンクも明記し、Slack MCP で同一スレッドに投稿してください。"
        f"最終投稿は JSON 文字列を貼らず、通常のプレーンテキストで作成してください。"
        f"改行は文字列 '\\n' ではなく実際の改行として出力してください。"
        f"以下はこのチャネルで保存している最近の文脈です。\n{context_block}\n"
        f"ユーザーの指示: {user_text}"
    )

    try:
        # codex 実行ログを残して、無反応時の調査を容易にする
        log_file = open("app-socket-worker.log", "a", encoding="utf-8")
        cmd = ["codex", "exec", "-s", CODEX_SANDBOX_MODE, "resume", session_target]
        if CODEX_SKIP_GIT_REPO_CHECK:
            cmd.append("--skip-git-repo-check")
        cmd.append(prompt)
        subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=CODEX_ACTION_FOLDER,
        )
        log_file.close()
    except Exception:
        logger.exception("Failed to launch codex process")
        say(text="処理の起動に失敗しました。サーバーログを確認してください。", thread_ts=thread_ts)


@app.event("message")
def handle_message_events(body):
    event = body.get("event", {})
    try:
        store_message_event(event)
    except Exception:
        logger.exception("Failed to store message event: %s", event)


if __name__ == "__main__":
    init_context_db()
    logger.info("Starting Socket Mode handler")
    logger.info(
        "Config summary: action_folder_exists=%s codex_exists=%s session_scope=%s",
        bool(CODEX_ACTION_FOLDER and Path(CODEX_ACTION_FOLDER).is_dir()),
        shutil.which("codex") is not None,
        CODEX_SESSION_SCOPE,
    )
    try:
        auth = app.client.auth_test()
        globals()["BOT_USER_ID"] = auth.get("user_id")
        logger.info("Slack auth_test ok: team=%s bot_user_id=%s", auth.get("team"), BOT_USER_ID)
        logger.info("Mention target format: <@%s> のように bot_user_id を直接メンションしてください", BOT_USER_ID)
    except Exception:
        logger.exception("Slack auth_test failed (check bot token/scopes)")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
