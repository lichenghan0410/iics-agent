[日本語](README.md) | [简体中文](README.zh-CN.md)

# IICS Agent 利用マニュアル

Takeda Japan IICS 運用ツール：自然言語（Claude Code CLI）または手動コマンドで、3 つの `JAPAN_*` フォルダ配下の task 構成を照会し、Excel に出力します。

**現在のバージョン：v0.1.1 Read-only MVP** —— 読み取り専用の照会のみ。書き込み操作は一切サポートしていません。

本マニュアルは **Windows + PowerShell** を主な前提環境として記載します（実際の利用環境）。Git Bash / WSL / macOS で操作する場合は、該当箇所で差分を注記します。

---

## これは何か

IICS（Informatica Intelligent Cloud Services）には数千の task が 3 つのプロジェクトフォルダに分散しており、Web UI で 1 件ずつ開いて構成を確認するのは非効率です。本ツールは「task の列挙 / 詳細の確認 / connection ID の解決 / Excel 出力」の 4 つをコマンド化したもので、自然言語での操作（Mode A）と手動実行（Mode C）の両方に対応します。今後は task の改修（変更）にも対応予定です。

適用範囲：

| 項目 | 値 |
|----|------|
| IICS Org | `use4.dm-us.informaticacloud.com`（Takeda Prod） |
| フォルダ | `JAPAN_COMMERCIAL_DWH`、`JAPAN_SCRM_BAU`、`JAPAN_SCRM_DI` |
| Task 種別 | `DSS`（Sync Task）、`MTT`（Mapping Task）、`WORKFLOW`（Linear Taskflow） |

## できること / できないこと

| できること | できないこと |
|----|------|
| フォルダ配下の task 一覧を照会 | task の runtime / connection の変更（v0.1 には書き込み経路がなく、遮断ではなくコードに存在しない） |
| task の完全な構成詳細を確認 | 一括変更 |
| connection ID を可読名に解決 | token の自動更新（失効時は手動で再取得が必要） |
| Excel 出力（Task Summary sheet、source/target オブジェクト列を含む） | フォルダをまたいだ同名 task の確実な区別（IICS API の制約、後述） |

---

## 初回利用：ゼロからのセットアップ

### 1. 端末環境の確認

PowerShell を開き（スタートメニューで "PowerShell" を検索）、順に実行します：

```powershell
py --version
py -m pip --version
```

いずれもバージョン番号（例：`Python 3.11.x`）が表示されれば、手順 2 へ進みます。

`py は内部コマンドまたは外部コマンド...ではありません` と表示される場合、Python が未インストールです。[python.org/downloads](https://www.python.org/downloads/) から 3.11 または 3.12 をダウンロードし、インストール時に **必ず "Add python.exe to PATH" にチェック**を入れてください。完了後、PowerShell を開き直して上記 2 コマンドを再実行します。

### 2. 依存パッケージのインストール

```powershell
Set-Location -LiteralPath "実際のプロジェクトパスに変更してください"
py -m pip install -r requirements.txt
```

裸の `pip` ではなく `py -m pip` を使うことで、企業端末で複数の Python バージョンがある場合の誤インストールを回避できます。

`requirements.txt` は 2 パッケージのみで、Anthropic SDK は不要です：

```
requests>=2.31.0
openpyxl>=3.1.0
```

> **任意：仮想環境で依存を分離**
>
> システム Python に入れたくない場合は仮想環境を利用できます（必須ではなく、スキップしても動作します）：
>
> ```powershell
> py -m venv .venv
> .\.venv\Scripts\Activate.ps1
> python -m pip install -r requirements.txt
> ```
>
> アクティベート時に `running scripts is disabled on this system` と出る場合は、先に一度実行します：
>
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
>
> またはアクティベートせず、仮想環境内の Python を直接呼び出します：
>
> ```powershell
> .\.venv\Scripts\python.exe -m pip install -r requirements.txt
> .\.venv\Scripts\python.exe .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
> ```

### 3. 「token なし事前チェック」を一度実行

token を設定せずにコマンドを 1 本実行し、Python 環境とスクリプト自体に問題がないことを確認します：

```powershell
python .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
```

期待される出力：

```json
{
  "error": "IICS_SESSION_TOKEN 未設定",
  "error_type": "ValueError"
}
```

この結果が出れば、Python・依存・スクリプトのエントリはすべて正常で、あとは token の設定を残すのみです。別のエラー（例：`ModuleNotFoundError`）が出る場合は、手順 2 に戻って依存インストールを確認してください。

### 4. Session Token の取得

IICS は現在、企業 SSO（Okta）を使用しており、専用サービスアカウントがないため、token はブラウザから手動で取得します：

1. ブラウザ（Chrome / Edge）で IICS Web UI に SSO ログインし、Okta 認証を完了する。
2. `F12` で開発者ツールを開き、上部の **Network** タブをクリックする。
3. 現在の IICS ページを再読み込みし、リクエストを再送させる。
4. リクエスト一覧で `objects`、`connection`、`api` のような名前のリクエストを 1 つ開く。
5. 右側パネルで **Headers** → **Request Headers** までスクロールし、`Cookie` の行を見つける。
6. 長い Cookie 文字列の中から `USER_SESSION=` を探し、**等号の後ろから次のセミコロン `;` の前まで**の値だけをコピーする。

### 5. Token の設定

```powershell
$env:IICS_SESSION_TOKEN = "<コピーした USER_SESSION の値>"
```

この設定は現在の PowerShell ウィンドウ内でのみ有効です。**ファイルに書かない・スクリーンショットで共有しない**でください。ウィンドウを閉じると次回は再設定が必要です。

### 6. 最初の本番コマンドを実行

```powershell
python .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
echo $LASTEXITCODE
```

成否の判定：

| `$LASTEXITCODE` | 意味 |
|---|---|
| `0` | 成功。上に表示された JSON の `tasks` 一覧を確認 |
| `1` | 失敗。JSON の `error` フィールドを確認し、後述の「トラブルシューティング」を参照 |

ここで task 一覧が表示されれば環境構築は完了で、日常利用に進めます。

---

## 日常利用：2 つのモードの選び方

| | Mode C：手動実行 | Mode A：Claude Code CLI |
|---|---|---|
| 使い方 | PowerShell で直接 `python iics_cli.py ...` を入力 | `claude` セッション内で自然言語で指示 |
| コマンド生成 | 自分で入力 | Claude Code CLI が自動生成 |
| Claude Code の要否 | 不要 | 必要 |
| 向いている場面 | すでに検証済みの方法をそのまま継続。スクリプト化・cron 定期実行・`claude` が使えない時 | 日常照会、コマンド書式を覚えたくない、自然言語で要求を伝えたい時 |

両モードとも呼び出す**エントリは完全に同一**の `iics_cli.py` で、コード差分はなく、出力も同じく JSON です。「初回利用」で実行したのが Mode C です。自然言語で操作したい場合は Claude Code を導入して Mode A に切り替えられ、両者はいつでも切替可能で競合しません。

### Mode C：手動実行（上記の方法をそのまま継続）

```powershell
$env:IICS_SESSION_TOKEN = "<USER_SESSION の値>"

python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI
python iics_cli.py resolve-connection 000LVU0B000000000015
python iics_cli.py export JAPAN_COMMERCIAL_DWH --type ALL
```

AI 層の介在がなく、セキュリティ境界は単純です。権限設定は不要で、token の取り扱いルールは Mode A と全く同じです。

### Mode A：Claude Code CLI

起動方法（PowerShell では継続行にバッククォート `` ` `` を使用。バックスラッシュではありません）：

```powershell
claude `
  --tools "Bash,Read" `
  --allowedTools "Bash(python iics_cli.py *)" "Bash(python smoke_test.py)" "Read" `
  --disallowedTools "Edit" "Write" "Bash(curl *)"
```

> Git Bash / WSL / macOS のターミナルで起動する場合、継続行はバックスラッシュ `\` に変えます：
> ```bash
> claude \
>   --tools "Bash,Read" \
>   --allowedTools "Bash(python iics_cli.py *)" "Bash(python smoke_test.py)" "Read" \
>   --disallowedTools "Edit" "Write" "Bash(curl *)"
> ```

3 つの flag の役割：

- `--tools "Bash,Read"`：Claude Code から見えるツール集合を制限。`Edit`/`Write` などは含まれません。
- `--allowedTools`：`iics_cli.py`、`smoke_test.py` の 2 コマンドを確認なしで自動実行させる自動承認ルール（可視性の制限ではありません）。
- `--disallowedTools`：Bash 内の危険な呼び出しパターン（例：`curl`）を拒否。Bash 全体が見えていても遮断されます。

`--dangerously-skip-permissions` は**使用しないでください**。

セッション開始後は自然言語で要求を伝えるだけで、Claude Code CLI が対応するコマンドを自動生成します：

| 指示例 | Claude Code CLI が実行するコマンド |
|------|--------------------------|
| 「JAPAN_SCRM_DI の DSS task を一覧して」 | `python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS` |
| 「DI_FACT_SALES_DAILY という DSS の詳細を見せて」 | `python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI` |
| 「この connection id を名前に解決して」 | `python iics_cli.py resolve-connection 000LVU...` |
| 「JAPAN_COMMERCIAL_DWH の全 task を Excel に出力して」 | `python iics_cli.py export JAPAN_COMMERCIAL_DWH --type ALL` |

---

## 機能詳細（Mode A / Mode C 共通）

以下の 4 つのサブコマンドが v0.1 の全機能です。両モードとも同じコマンド書式で直接これらを呼び出します。

### 1. `list-tasks` — フォルダ配下の task を列挙

```powershell
python iics_cli.py list-tasks <folder> [--type DSS|MTT|WORKFLOW|ALL] [--page-size N]
```

| 引数 | 必須 | 説明 |
|------|------|------|
| `<folder>` | 必須 | 3 つの `JAPAN_*` フォルダのいずれか |
| `--type` | 任意 | 既定 `ALL`。`ALL` は DSS/MTT/WORKFLOW の 3 種を個別に照会し統合（`MAPPING` 等の非 task 設計オブジェクトは自動除外） |
| `--page-size` | 任意 | 既定 200（IICS V3 の 1 ページ上限も 200。超過分は 200 に切り詰め） |

例：

```powershell
python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
```

戻り値：

```json
{
  "folder": "JAPAN_SCRM_DI",
  "count": 847,
  "tasks": [
    {"name": "DI_FACT_SALES_DAILY", "type": "DSS", "id": "...", "status": ""},
    ...
  ]
}
```

> `status` フィールドは現在常に空です。V3 のオブジェクト一覧自体が status 情報を持たないためで、API のデータ制約であり不具合ではありません。

### 2. `get-task` — 単一 task の詳細確認

```powershell
python iics_cli.py get-task <task_name> <task_type> [--folder PATH] [--no-resolve]
```

| 引数 | 必須 | 説明 |
|------|------|------|
| `<task_name>` | 必須 | task 名 |
| `<task_type>` | 必須 | `DSS` / `MTT` / `WORKFLOW` |
| `--folder` | **強く推奨** | 下記「同名 task」を参照 |
| `--no-resolve` | 任意 | connection ID → 名前の解決をスキップし、ID のみ表示 |

例：

```powershell
python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI
```

戻り値：

```json
{
  "name": "DI_FACT_SALES_DAILY",
  "type": "DSS",
  "folder": "JAPAN_SCRM_DI",
  "runtime_agent": "",
  "source_connection": {"id": "000LVU0B000000000015", "name": "SCRM_ORA_STDB"},
  "target_connection": {"id": "000LVU0B00000000009O", "name": "mysql_scrm_hnn_api"},
  "raw": { ... }
}
```

> **重要な制約**：`runtime_agent` は DSS 種別では常に空文字列です。IICS API の DSS 詳細エンドポイント自体が runtime environment フィールドを返さないためで、プラットフォームのデータ可得性の制約であり、ツールのバグではありません。MTT / WORKFLOW の runtime フィールドは未検証で、次バージョンで補完します。
>
> **同名 task への対応**：v0.1 にはフォルダをまたいだ確実な区別機能がありません（IICS V3 照会は名前フィルタに非対応）。そのため `--folder` は任意引数ですが毎回指定することを強く推奨します。指定しないと、同名 task が別フォルダに存在する場合、意図しない方が返る可能性があります。

### 3. `resolve-connection` — connection ID の解決

```powershell
python iics_cli.py resolve-connection <id> [<id>...]
```

複数 ID を一度に渡せます（スペース区切り）：

```powershell
python iics_cli.py resolve-connection 000LVU0B000000000015 000LVU0B00000000009O
```

戻り値：

```json
{
  "resolved": {
    "000LVU0B000000000015": "SCRM_ORA_STDB",
    "000LVU0B00000000009O": "mysql_scrm_hnn_api"
  },
  "failed": []
}
```

同一セッション内で同じ ID を再度解決するとメモリキャッシュに命中し、リクエストは再送されません。

### 4. `export` — Excel / JSON 出力

```powershell
python iics_cli.py export <folder> [--type ALL] [--details|--summary-only] [--format excel|json] [--filename NAME]
```

| 引数 | 必須 | 説明 |
|------|------|------|
| `<folder>` | 必須 | 3 つの `JAPAN_*` フォルダのいずれか |
| `--type` | 任意 | 既定 `ALL` |
| `--details` | 任意 | **既定動作**。task ごとに詳細を取得し、Excel に runtime/connection 情報を含める |
| `--summary-only` | 任意 | 詳細取得をスキップし task 一覧のみ出力。高速だが runtime/connection 列は空 |
| `--format` | 任意 | 既定 `excel` |
| `--filename` | 任意 | 既定はタイムスタンプで自動生成 |

例：

```powershell
python iics_cli.py export JAPAN_COMMERCIAL_DWH --type DSS
```

戻り値：

```json
{
  "file_path": "/path/to/iics_export_20260630_103000.xlsx",
  "format": "excel",
  "rows": 83,
  "summary": "83 件を .../iics_export_20260630_103000.xlsx に出力しました"
}
```

Excel は v0.1.1 以降、2 つの sheet を含みます（旧 `Field Mapping` / `Truncation Risk` は削除）：

| Sheet | 列 | データ保証 |
|-------|------|--------------|
| Task Summary | name / type / folder / runtime_agent / source_conn / target_conn / source_objects / target_object / status | 必ず出力。`source_objects` / `target_object` は best-effort で、取得不可時は空 |
| Export Errors | task_name / task_type / folder / error_type / error | エラーがない場合は 1 行 `No export errors` を記載 |

> フォルダには数千の task が存在し得ます。`--details` モードは task ごとに詳細エンドポイントを 1 回ずつ呼ぶため、大きなフォルダでは時間がかかります。どの task があるかだけを素早く見たい場合は、まず `--summary-only` を使ってください。

### v0.1.1 安定性（長時間・大フォルダ出力のネットワーク変動耐性）

- **`_request()` に timeout + 一時エラーの再試行**：既定 timeout `(connect=10s, read=60s)`。`Timeout` / `ConnectionError` / HTTP `429/502/503/504` に対して最大 3 回まで再試行（backoff 1s→2s）。再試行ログは stderr のみで、token は含みません。`400/403/404` 等の業務エラーは再試行しません。
- **`export --details` は単一 task 失敗で中断しない**：失敗した task は `failed_details` に記録され（stdout の JSON と JSON 出力の両方に含む）、Excel の `Export Errors` sheet に列挙されます。`Task Summary` にも当該 task の行（name/type/folder/status、他は空）を残します。
- **`rows` = 今回の task 総数**（成功詳細数ではありません）。`list-tasks` の `count` と比較できます：`list-tasks count == export rows` なら task の取りこぼしなし。`failed_details_count > 0` は取得できなかった詳細があることを示し、再実行または `Export Errors` の確認が必要です。

#### 出力判定基準（v0.1.1 以降）

| 判定 | 意味 |
|------|------|
| `rows` | 今回出力した task 総数。同一引数の `list-tasks` の `count` と一致すべき |
| `rows == list-tasks count` | `Task Summary` が task を**取りこぼしていない**ことのみを示す。**全 task の詳細成功を意味しない** |
| **完全成功** | `rows == list-tasks count` **かつ** `failed_details_count == 0` の両方を満たすこと |
| `failed_details_count > 0` | Excel は生成済みだが**一部 task の詳細取得に失敗**。`Export Errors` sheet で失敗 task（および `error_type`/`error`）を確認し、必要に応じて**当該種別の出力を再実行** |

> 推奨の突合手順：先に `list-tasks --type X` で `count` を控え、次に `export --type X` を実行し、stdout の `rows` と `count` が一致するか、`failed_details_count` が 0 かを確認します。両方を満たして初めて当該種別の出力が完全成功です。

---

## 環境変数とパラメータ値

### 環境変数（必須は 1 つのみ）

| 変数 | 必須 | 説明 |
|------|------|------|
| `IICS_SESSION_TOKEN` | **必須** | ブラウザの `USER_SESSION` Cookie 値。上記「Session Token の取得」を参照 |
| `IICS_LOGIN_URL` / `IICS_USER` / `IICS_PASS` | 任意 | v0.1 では不要・未使用。将来の専用サービスアカウント方式に備えた枠組みで、現状は設定してもしなくても影響なし |

### コマンド引数（環境変数ではなく、都度指定）

フォルダと task 種別は環境変数ではなく、`iics_cli.py` 実行時にコマンド引数として直接渡します。照会ごとに異なるフォルダ／種別が必要になるためです：

| 引数 | 有効値 |
|------|---------|
| folder | `JAPAN_COMMERCIAL_DWH`、`JAPAN_SCRM_BAU`、`JAPAN_SCRM_DI` |
| task type | `DSS`、`MTT`、`WORKFLOW`、`ALL`（`ALL` は `list-tasks` / `export` のみ対応。`get-task` は具体的な種別の指定が必須） |

### スモークテスト専用の環境変数（日常利用とは無関係）

以下は `smoke_test.py` の受入テスト実行時のみ使用します。**日常の `iics_cli.py` 照会では設定不要です**：

```powershell
$env:IICS_TEST_FOLDER    = "JAPAN_SCRM_DI"      # 既定値
$env:IICS_TEST_TASK_TYPE = "DSS"                # 既定値
$env:IICS_TEST_TASK_NAME = "<実在の task 名>"     # 既定は空。自動的に先頭を使用
python smoke_test.py
```

---

## 出力フォーマット

- **stdout は JSON のみ**を出力します。そのままスクリプトで解析できます。
- **stderr は人間可読のログ**（進捗、`[Auth]` 情報、警告）を出力し、JSON には混入しません。
- **終了コード**：成功は `0`、エラー（`IICS_SESSION_TOKEN` 未設定、API エラー、ツール関数が `{"error": ...}` を返した場合を含む）は `1`。PowerShell では `echo $LASTEXITCODE` で確認できます。スクリプト化時はこの値を確認すればよく、成否判定に JSON 内容を解析する必要はありません。

---

## 既知の制約（v0.1 の境界）

- **読み取り専用**：書き込み操作のサブコマンドは一切なく、コード上も `update`/`batch`/`safety` 関連ファイルは存在しません（権限による遮断ではありません）。
- **DSS の `runtime_agent` は常に空**：API のデータ制約。上記 `get-task` の説明を参照。
- **同名 task をフォルダ間で確実に区別できない**：必ず `--folder` を付けてください。
- **MTT / WORKFLOW の runtime / connection フィールド構造は未検証**：v0.1 は主に DSS で完全なフィールド検証を実施。
- **Token は自動更新されない**：失効後はブラウザから手動で再取得が必要。
- **Excel は v0.1.1 以降 `Task Summary` + `Export Errors` の 2 sheet を出力**（旧 `Field Mapping` / `Truncation Risk` は削除）。`source_objects` / `target_object` は best-effort で、取得不可時は空・エラーなし。

---

## トラブルシューティング

| 現象 | 考えられる原因 | 対処 |
|------|----------|------|
| `py は内部コマンド...ではありません` | Python 未インストール、または PATH 追加を未チェック | Python を再インストールし、"Add python.exe to PATH" を必ずチェック |
| `python -m pip install` がエラー / 誤った場所に導入 | 企業端末に複数の Python バージョン | `py -m pip install -r requirements.txt` に統一 |
| 仮想環境の有効化で `running scripts is disabled` | PowerShell の実行ポリシー制限 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`、または有効化せず `.\.venv\Scripts\python.exe` を直接使用 |
| `{"error":"IICS_SESSION_TOKEN 未設定"}`、終了コード 1 | 環境変数が未設定、または新しいウィンドウ | `$env:IICS_SESSION_TOKEN = "..."` を再設定 |
| `RuntimeError: IICS session expired...` | Token 失効（401） | ブラウザで `USER_SESSION` を再取得し設定し直す |
| `list-tasks` が `{"error":...}` を返す | フォルダ名の誤り | 3 つの `JAPAN_*` のいずれかか確認 |
| `get-task` の結果が期待した task と異なる | 同名 task のフォルダ間曖昧さ | `--folder` を付与 |
| Excel が開けない / sheet 欠落 | `openpyxl` の導入不良、または書き込み失敗 | `py -m pip install -r requirements.txt` を再実行し、ディレクトリの書き込み権限を確認 |
| Claude Code 起動コマンドが構文エラー | PowerShell でバックスラッシュ `\` を継続行に使用 | PowerShell はバッククォート `` ` `` を使用。バックスラッシュ継続は Bash/WSL のみ有効 |
| stdout に非 JSON が混入 | 発生してはならない不具合 | 完全なコマンドと出力を添えて開発者へ報告 |

---

## セキュリティ上の注意

- `IICS_SESSION_TOKEN` は現在のターミナルの環境変数にのみ存在します。ファイルに書かない・出力しない・git にコミットしない。
- **token を Claude Code の対話、チャットツール、メール、チケットに貼らない**——エラー相談のためでも不可。問題の説明はマスキングしたスクリーンショットや文章で代替してください。
- `curl` その他の方法で IICS に直接 POST/PUT/PATCH/DELETE を送らない——v0.1 のコード自体に書き込み経路はなく、迂回は無意味かつリスクがあります。
- Mode A で Claude Code を起動する際、`--dangerously-skip-permissions` を使わない。

---

## 参考

本 README には日常利用に必要な全手順が含まれています。開発者向けの内部設計ドキュメントは、一般運用の利用前提ではありません。
