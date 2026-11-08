<p align="center"><img src="../../packages/alcor-secretary/src/assets/alcor-avatar.svg" alt="Alcor" width="112" /></p>

# Alcor

[English](../../README.md) · [中文](../zh/README.md) · [繁體中文](../zh/README-Hant.md) · [日本語](README.md)

Alcor は DeepSeek Harness（DSH）で動作するパーソナルアシスタントです。ネイティブプラグインを使ってタスク、予定、家計、ブリーフィング、定期実行を管理できます。Alcor と DSH は同じデスクトップウィンドウ、モデル、会話、ワークスペースを共有します。

Alcor の各ページでクマにポインターを合わせるかクリックすると、現在のワークスペースで会話できます。ドラッグで移動でき、表示とホバーの設定は **設定 → Alcor** にあります。

## デスクトップへのインストール

デスクトップ版は Apple Silicon 搭載の Mac に対応しています。[対応バージョン一覧](../../product/desktop-release.lock.json)に掲載されている公式 DSH クライアントを使用してください。

1. 初期化済みの DSH プロファイルでプラグインマネージャーを開き、`.tgz` ファイルから `@alcor/secretary` をインストールします。
2. 必要に応じて、以下のオプションのプラグインを追加します。
3. DSH を終了して開き直し、ウィンドウ上部のモードメニューで **Alcor** を選びます。

旧 Stella プロファイルは、名称変更後のプラグインを追加する前に移行が必要です。手順は[ネイティブ製品ガイド](../../product/README.md)を参照してください。

| プラグイン | 機能 |
| --- | --- |
| `@alcor/secretary` | タスクとサブタスク、予定、家計、ブリーフィングと定期実行を管理します。 |
| `@alcor/apple-sync` | 同期元を選択して許可すると、Apple カレンダーやリマインダーと同期します。 |
| `@alcor/elearning` | 復旦大学の eLearning アカウントから授業、課題、お知らせを読み取ります。 |
| `@alcor/mail` | IMAP メールを読み取り専用で検索し、ブリーフィングの情報源にできます。 |
| `@alcor/agent-hub` | DSH ワークベンチで開発タスクやエージェントの実行履歴を確認できます。 |

## ローカル開発

Node.js 24.9 以上と Python 3.9 以上を使用します。macOS 用ヘルパーのビルドには Xcode Command Line Tools も必要です。リポジトリのルートで次のコマンドを実行してください。

```sh
npm ci --ignore-scripts
npm run product:build
npm test
npm run dev
```

開発プレビューには独立した DSH プロファイルを使用します。`npm run product:pack` で、インストール可能な秘書プラグインを `.local/artifacts/` に生成できます。大学・メール用プラグインのビルドには、隣接する MCP リポジトリと指定バージョンのビルド依存関係も必要です。[MCP 接続ガイド](../../product/mcp-connections.md)

ネイティブプラグインは `packages/`、ビルドスクリプトは `scripts/product/`、統合テストは `product/tests/` にあります。以前のスタンドアロン版 Python/Electron アプリは[旧版ガイド](guide.md)を参照してください。

## 出典とライセンス

Alcor は zhayujie とコントリビューターによる [CowAgent](https://github.com/zhayujie/CowAgent)（旧 `chatgpt-on-wechat`）を基に独自開発されています。上流の Agent、モデル、ツール、Skill、記憶、メッセージ連携を利用し、デスクトップ操作とタスク、カレンダー、請求、ブリーフィング、権限管理の共通サービスを追加しています。

出典と著作権は[第三者表示](../../THIRD_PARTY_NOTICES.md)、ライセンス条項は [MIT ライセンス](../../LICENSE) を参照してください。

## 作者より

これは私が初めて一人で完成させたオープンソースプロジェクトです。開発中の Codex と Cursor の助けに感謝します。まだ改善の余地がありますので、[Issue](https://github.com/starpickerr/Alcor/issues) から提案や不具合報告を歓迎します。
