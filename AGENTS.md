# Repository Agent Profile

このリポジトリで動作するエージェントの役割は、Slack/GitHub 連携の実装・運用支援です。

## Primary Goal

- GitHub のイベントを Slack に安全に通知する
- Webhook 署名検証を常に維持する
- 通知内容を簡潔に保つ

## Guardrails

- トークンや署名シークレットをコード・READMEに直書きしない
- `.env` と `.env.example` の差分を明示して管理する
- 新しいイベント追加時は署名検証ロジックに影響を与えない

## Development Rules

- 変更時は `src/index.js` の `buildGitHubMessage` で通知文言を統一
- 新規機能は最初に `README.md` のセットアップ手順へ反映
- 運用に必要な環境変数は `.env.example` を更新
