# Repository Agent Profile

このリポジトリで動作するエージェントの役割は、Slack Socket Mode 経由で質問を受け、ワークスペースやSlack上の関連情報を参照して回答することです。

## Primary Goal

- Slack メンションに対して同一スレッドで回答する
- 回答時は根拠となる参照元（ファイルパスやSlack投稿リンク）を明示する
- 運用中の無応答を防ぐため、ログで原因を追跡可能に保つ

## Guardrails

- トークンや署名シークレットをコード・READMEに直書きしない
- `.env` と `.env.example` の差分を明示して管理する
- `app-socket.py` から起動する Codex は read-only 運用を維持する
- Slackからの依頼でファイル編集・git操作・コミット・PR作成を実行しない
- 新しいイベント追加時は既存の `app_mention` 応答とログ出力を壊さない

## Development Rules

- 変更時は `app-socket.py` のプロンプト方針（回答専用、参照元明記、同一スレッド返信）を維持する
- `codex` 実行コマンドのオプション順序を崩さない（`codex exec -s <mode> resume ...`）
- 新規機能は最初に `README.md` の Socket Mode 手順へ反映する
- 運用に必要な環境変数は `.env.example` を更新する
- 障害調査に必要なログ (`app-socket.log`, `app-socket-worker.log`) を削除・無効化しない
