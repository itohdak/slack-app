import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
CODEX_ACTION_FOLDER = os.getenv("CODEX_ACTION_FOLDER")
CODEX_SESSION_ID = os.getenv("CODEX_SESSION_ID")
CODEX_SKIP_GIT_REPO_CHECK = os.getenv("CODEX_SKIP_GIT_REPO_CHECK", "true").lower() == "true"

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
    user_text = re.sub(r"^(?:<@[^>]+>\\s*)+", "", text).strip()
    # メンションしか含まれない場合は空指示として扱う
    if user_text and re.fullmatch(r"(?:<@[^>]+>\\s*)+", user_text):
        user_text = ""
    if not user_text:
        say(text="指示が空です。メンションの後に依頼内容を書いてください。", thread_ts=thread_ts)
        return

    logger.info("Received app_mention channel=%s thread_ts=%s", channel, thread_ts)
    say(text="受け付けました。バックグラウンドで処理を開始します。", thread_ts=thread_ts)

    if not CODEX_ACTION_FOLDER or not CODEX_SESSION_ID:
        logger.error("Missing CODEX_ACTION_FOLDER or CODEX_SESSION_ID")
        say(
            text="環境変数 `CODEX_ACTION_FOLDER` / `CODEX_SESSION_ID` が未設定です。",
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

    prompt = (
        f"Slack channel_id={channel}, thread_ts={thread_ts} です。"
        f"Slack MCP を使ってこのスレッドに進捗と結果を投稿しながら、"
        f"GitHub MCP で必要なコード修正・テスト・コミット・PR作成まで行ってください。"
        f"ユーザーの指示: {user_text}"
    )

    try:
        # codex 実行ログを残して、無反応時の調査を容易にする
        log_file = open("app-socket-worker.log", "a", encoding="utf-8")
        cmd = ["codex", "exec", "resume", CODEX_SESSION_ID]
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


if __name__ == "__main__":
    logger.info("Starting Socket Mode handler")
    logger.info(
        "Config summary: action_folder_exists=%s codex_exists=%s",
        bool(CODEX_ACTION_FOLDER and Path(CODEX_ACTION_FOLDER).is_dir()),
        shutil.which("codex") is not None,
    )
    try:
        auth = app.client.auth_test()
        bot_user_id = auth.get("user_id")
        logger.info("Slack auth_test ok: team=%s bot_user_id=%s", auth.get("team"), bot_user_id)
        logger.info("Mention target format: <@%s> のように bot_user_id を直接メンションしてください", bot_user_id)
    except Exception:
        logger.exception("Slack auth_test failed (check bot token/scopes)")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
