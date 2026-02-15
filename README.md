# Slack/GitHub Integration Agent

このリポジトリ専用の Slack/GitHub 連携エージェントです。

## セットアップ

1. 依存関係をインストール

```bash
npm install
```

2. 環境変数を作成

```bash
cp .env.example .env
```

3. `.env` を編集

- `SLACK_BOT_TOKEN`
- `SLACK_SIGNING_SECRET`
- `SLACK_NOTIFICATION_CHANNEL_ID`
- `GITHUB_WEBHOOK_SECRET`

4. 起動

```bash
npm start
```

## エンドポイント

- `GET /healthz`
- `POST /github/webhook`
- `POST /slack/events`

## GitHub Webhook 推奨イベント

- `push`
- `pull_request`
- `issues`
- `issue_comment`
- `ping`

## セキュリティ

- GitHub は `X-Hub-Signature-256` を検証
- Slack は `X-Slack-Signature` と timestamp を検証
- 実トークンは `.env` にのみ保存

## Socket Mode Bot (`app-socket.py`)

Slack でメンションを受けて Codex を起動する場合は、以下を設定してください。現在の `app-socket.py` は「ワークスペース参照して回答する専用モード」です（コード修正・コミット・PR作成は行いません）。

1. Python 依存をインストール

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install slack-bolt python-dotenv
```

2. `.env` に以下を設定

- `SLACK_BOT_TOKEN` (`xoxb-...`)
- `SLACK_APP_TOKEN` (`xapp-...`, Connections: Write 権限)
- `CODEX_ACTION_FOLDER`
- `CODEX_SESSION_SCOPE` (`channel` 推奨: チャネル単位で文脈分離)
- `CODEX_SESSION_ID`（`CODEX_SESSION_SCOPE=global` の場合のみ必須）
- `CODEX_SANDBOX_MODE` (`read-only` 推奨)
- `CODEX_ACCEPT_REACTION`（受付時に付与するリアクション名。例: `eyes`）
- `CODEX_SKIP_GIT_REPO_CHECK` (`true` 推奨)
- `CODEX_CONTEXT_DB_PATH`（チャネル文脈保存先DB）
- `CODEX_CONTEXT_LIMIT`（文脈として渡す最大件数）
- `CODEX_CONTEXT_RETENTION_DAYS`（保存日数）
- `CODEX_CATCHUP_DEFAULT_LIMIT`（手動catch-upの既定取得件数）
- `CODEX_CATCHUP_MAX_LIMIT`（手動catch-upの最大取得件数）

3. Slack App 設定

- Socket Mode を `Enabled` にする
- Event Subscriptions で Bot Event `app_mention`, `message.channels` を追加
- OAuth Scope に `app_mentions:read`, `chat:write`, `reactions:write` を追加
- チャネル投稿を文脈として常時取り込むため `channels:history` を追加（必要に応じて `groups:history` / `im:history` / `mpim:history`）
- Bot を投稿先チャンネルに招待する

4. 起動

```bash
. .venv/bin/activate
python app-socket.py
```

5. 動作確認ログ

- 標準出力に起動ログが出る
- `app-socket.log` に受信イベントとエラーが出る
- `app-socket-worker.log` に `codex` 実行ログが出る
- `slack-context.db` にチャネル文脈が蓄積される

6. 途中参加チャンネルの手動キャッチアップ

- メンションで `catchup` を送ると、そのチャンネルの過去投稿を手動で取り込みます
- 件数指定は `catchup 800` のように指定可能（上限は `CODEX_CATCHUP_MAX_LIMIT`）
- 自動実行はされません。必要時のみ明示的に実行されます
