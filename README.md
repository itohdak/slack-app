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

Slack でメンションを受けて Codex を起動する場合は、以下を設定してください。

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
- `CODEX_SESSION_ID`
- `CODEX_SKIP_GIT_REPO_CHECK` (`true` 推奨)

3. Slack App 設定

- Socket Mode を `Enabled` にする
- Event Subscriptions で Bot Event `app_mention` を追加
- OAuth Scope に `app_mentions:read` と `chat:write` を追加（`chat:read` ではなく投稿権限の `chat:write` が必要）
- スレッド本文の取得が必要な運用では `channels:history`（必要に応じて `groups:history` / `im:history` / `mpim:history`）も追加
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
