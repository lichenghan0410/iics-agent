[日本語](README.md) | [简体中文](README.zh-CN.md)

# IICS Agent 利用マニュアル

Takeda Japan 向け IICS 運用ツールです。自然言語（Claude Code CLI）または手動コマンドで、folder 配下の IICS task 設定を検索・確認・エクスポートできます。

**現在のバージョン：v0.1 Read-only MVP** — 参照専用で、書き込み操作はサポートしません。

本マニュアルは **Windows + PowerShell** を主な利用環境として説明します。

---

## 概要

IICS（Informatica Intelligent Cloud Services）には多数の task があり、Web UI で一件ずつ開いて設定を確認するのは非効率です。本ツールは以下の read-only 操作を共通 CLI にまとめています。

- task の列挙
- task 詳細の確認
- connection ID の名前解決
- folder 全体のエクスポート
- ユーザーが指定した task list のエクスポート

操作方法は 2 種類あり、どちらも同じ Python 実装を呼び出すため、結果は同一です。

- **Mode A — Claude Code**：自然言語で要件を伝えると、Claude Code が自動的に適切な `iics_cli.py` を選択・実行します
- **Mode C — Manual CLI**：利用者が `iics_cli.py` を直接実行します

### 対象範囲

| 項目 | 値 |
|---|---|
| IICS platform host | `use4.dm-us.informaticacloud.com`（Informatica IICS 米国 pod の共通 API endpoint。Takeda の dev/test/prod 各 org は同一 host を利用し、実際にどの org にアクセスするかは現在使用中の `IICS_SESSION_TOKEN` によって決まり、host 自体には依存しません） |
| 検証済み folder | `JAPAN_COMMERCIAL_DWH`、`JAPAN_SCRM_BAU`、`JAPAN_SCRM_DI` など |
| Task type | `DSS`（Sync Task）、`MTT`（Mapping Task）、`WORKFLOW`（Linear Taskflow） |

ツール自体は folder を限定していません。現在の IICS ユーザーアカウントがアクセス権を持つ folder であれば検索・エクスポートできます。上記 3 folder は現時点で実際に検証済みのものです。それ以外の folder も理論上利用可能で、実際の利用可否は現在のアカウント権限に依存します。

## できること / できないこと

| できること | できないこと |
|---|---|
| folder 配下の task 一覧を取得 | task runtime / connection の変更 |
| task 設定詳細の確認 | IICS 設定の一括変更 |
| connection ID を読みやすい名前に解決 | browser SSO token の自動更新 |
| folder 全体を Excel / JSON にエクスポート | CLI から POST / PUT / PATCH / DELETE を実行 |
| 指定した task list のみをエクスポート | DSS / WORKFLOW の V2 by-name detail を folder 単位で強制的に disambiguate |

v0.1 の書き込み操作は「コードはあるが権限で止める」という構造ではありません。Python 実装自体が GET request のみを許可し、それ以外の method を試すと直接エラーになります。

---

## プロジェクト構成と Claude Code 設定

```text
IICS-Agent/
├── CLAUDE.md
├── iics_cli.py
├── iics_client.py
├── tools/
├── cache/
└── .claude/
    ├── settings.json
    ├── settings.local.json
    └── skills/
        └── iics-agent/
            └── SKILL.md
```

各部分の役割：

| ファイル / レイヤー | 役割 |
|---|---|
| Python（`iics_cli.py` / `iics_client.py` / `tools/`） | IICS API 呼び出し、データ解析、エクスポート、GET-only enforcement |
| `.claude/settings.json` | プロジェクト共有の Claude Code runtime permission。Git 管理対象で、チーム共通 |
| `.claude/settings.local.json` | 現在のユーザー端末固有の permission override。Git 管理対象外 |
| `CLAUDE.md` | Agent の project-level behavior rule：認証方式、安全境界、working directory rule |
| `.claude/skills/iics-agent/SKILL.md` | IICS 操作の具体的な procedure：command selection、parameter、result interpretation |

### Claude Code permission 設定

現在の `.claude/settings.json`：

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash(python iics_cli.py *)",
      "Bash(python smoke_test.py)",
      "Read"
    ],
    "deny": [
      "Edit",
      "Write",
      "Bash(curl *)"
    ]
  }
}
```

意味：

- `allow`：`iics_cli.py`、`smoke_test.py` の実行および project file の読み取りを毎回手動確認せず許可
- `deny`：Claude Code による file modification（`Edit` / `Write`）を禁止し、`curl` で official CLI を bypass して IICS へ直接アクセスすることも禁止

> **重要**：この設定が制御するのは **Claude Code がどの tool を呼び出せるか** であり、IICS 自体の write protection ではありません。v0.1 の本当の read-only guarantee は Python 実装にあります。`IICSClient._request()` は `GET` method のみを受け付け、それ以外の method は直接 exception になります。permission 設定が誤って変更されても、Python は実際の write request を送信しません。
>
> 今後さらに Claude Code から見える tool を制限したい場合（例：network access 系 tool を無効化）、`deny` に tool name を追加できます。

`.claude/settings.local.json` は個人端末での追加 permission 用です（例：特定 command の “do not ask again” 記録）。他の team member には影響せず、Git に commit しません。

---

## 初回利用：ゼロからセットアップ

### 1. ローカル環境を確認

PowerShell を開いて実行：

```powershell
py --version
py -m pip --version
```

正常に version が表示される場合（例：`Python 3.11.x`）、次へ進みます。

`py 不是内部或外部命令` のように表示される場合は Python が未導入です。[python.org/downloads](https://www.python.org/downloads/) から 3.11 または 3.12 をインストールし、インストール時に **"Add python.exe to PATH"** を必ず有効にしてください。インストール後、PowerShell を開き直して再実行します。

### 2. Project dependency をインストール

```powershell
Set-Location -LiteralPath "実際のプロジェクトパスに変更"
py -m pip install -r requirements.txt
```

#### 任意：virtual environment を利用

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

PowerShell execution policy のため virtual environment を activate できない場合、activate せず直接 virtual environment の Python を使えます。

```powershell
.\.venv\Scripts\python.exe .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
```

### 3. “Token 未設定” の事前確認

token をまだ設定せず、まず program 自体が起動できることを確認します。

```powershell
python .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
```

想定される出力：

```json
{
  "error": "IICS_SESSION_TOKEN 未设置",
  "error_type": "ValueError"
}
```

この結果が出れば、Python、dependency、CLI entrypoint は正常で、token だけが未設定です。

### 4. Session Token を取得

現在は企業 SSO（Okta）を利用しており、独立 service account はありません。token は browser から手動取得します。

1. Chrome / Edge で IICS Web UI にログインし、SSO 認証を完了
2. `F12` で developer tools を開き、**Network** tab へ移動
3. IICS page を refresh
4. 正常な IICS API request を 1 つ開く
5. **Request Headers → Cookie** から `USER_SESSION=<長い値>` を探す
6. `USER_SESSION=` の直後から、次の `;` の直前までをコピー

### 5. Token を設定

```powershell
$env:IICS_SESSION_TOKEN = "<コピーした USER_SESSION value>"
```

この value は**現在の terminal window**の環境変数にのみ置いてください。

**禁止事項：**

- `.env` やその他 config file へ保存
- print / echo
- Git へ commit
- Claude Code conversation、メール、chat tool、ticket へ貼り付ける（troubleshooting 目的でも不可。必要なら mask した screenshot か文字説明を使用）

Token が期限切れになった場合（command が 401 または session expired を返す）、browser から再取得し、上記 command を再実行します。terminal window を閉じた場合も token は再設定が必要です。

### 6. 最初の実コマンドを実行

```powershell
python .\iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
echo $LASTEXITCODE
```

| `$LASTEXITCODE` | 意味 |
|---|---|
| `0` | command 正常完了。stdout の JSON result を確認 |
| `1` | top-level fatal error。JSON の `error` field を確認 |

正常な JSON output が表示されれば（`count` が 0 でも可）、環境は利用可能です。

---

## 日常利用：Mode A / Mode C

| | Mode C：Manual CLI | Mode A：Claude Code |
|---|---|---|
| 使い方 | `python iics_cli.py ...` を直接実行 | `claude` session で自然言語入力 |
| command selection | 利用者 | Claude Code が intent に応じて自動選択 |
| Claude Code のインストール | 不要 | 必要 |
| 向いている用途 | command に慣れている、script batch、troubleshooting | 日常検索、command format を覚えたくない、selected task list の整理 |

2 つの mode はいつでも切り替え可能で、競合しません。

### Mode C：手動実行

```powershell
$env:IICS_SESSION_TOKEN = "<USER_SESSION 値>"

python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI
python iics_cli.py resolve-connection 000LVU0B000000000015
python iics_cli.py export JAPAN_COMMERCIAL_DWH --type ALL
```

Mode C は Claude Code を経由しないため、`.claude/settings.json` の permission 設定には影響されず、Claude Code のインストールも不要です。

### Mode A：Claude Code

project root に移動して起動：

```powershell
Set-Location -LiteralPath "実際のプロジェクトパスに変更"
claude
```

project の共有 permission（`.claude/settings.json`）と operation rule（`.claude/skills/iics-agent/SKILL.md`）は project と一緒に自動 load されるため、追加設定は不要です。

新しい PC でこの project を初めて開くと、Claude Code が workspace trust の確認を求める場合があります。会社の security rule と実際の project situation に基づいて確認してください。project-level `allow` rule は trust 後に auto-approval rule として有効になります。`deny` rule はこれに影響されず、常に即時有効です。

session 内では自然言語で直接依頼できます。

| 入力例 | 対応操作 |
|---|---|
| "JAPAN_SCRM_DI 配下の DSS task を一覧にして" | task を列挙 |
| "DI_FACT_SALES_DAILY の DSS 詳細を見たい。folder は JAPAN_SCRM_DI" | 単一 task 詳細 |
| "この connection ID を名前に解決して" | connection を resolve |
| "JAPAN_COMMERCIAL_DWH の全 task を export して" | folder 全体 export |
| "この task だけ export して"（Excel / table をそのまま貼り付け可） | selected list export |

**`--dangerously-skip-permissions` で起動しないでください。**

---

## 機能詳細（Mode A / Mode C 共通）

v0.1 では 5 個の read-only subcommand を公開しています。command syntax は両 mode で同じです。

### 1. `list-tasks` — folder 配下の task を列挙

```powershell
python iics_cli.py list-tasks <folder> [--type DSS|MTT|WORKFLOW|ALL] [--page-size N]
```

| parameter | 必須 | 説明 |
|---|---|---|
| `<folder>` | はい | 現在の account がアクセス権を持つ IICS folder（`JAPAN_*` 3 folder は検証済み） |
| `--type` | いいえ | default `ALL`、DSS + MTT + WORKFLOW の 3 種類（その他 IICS object type は含まない） |
| `--page-size` | いいえ | 1 page の query 件数。通常は変更不要 |

例：

```powershell
python iics_cli.py list-tasks JAPAN_SCRM_DI --type DSS
```

返却された `count` と `tasks` は Python 側で集計済みなので、そのまま使用します。自分で再集計する必要はありません。

---

### 2. `get-task` — 単一 task 詳細を確認

```powershell
python iics_cli.py get-task <task_name> <task_type> --folder <folder> [--full] [--no-resolve]
```

| parameter | 必須 | 説明 |
|---|---|---|
| `<task_name>` | はい | task name |
| `<task_type>` | はい | `DSS` / `MTT` / `WORKFLOW` |
| `--folder` | **はい** | 3 type すべてで必須。対象 task object を正確に特定するために使用 |
| `--full` | いいえ | 完全 detail を返す。diagnostic 用で、日常利用では不要 |
| `--no-resolve` | いいえ | connection ID を name に解決せず、Connection 列は raw ID のみ表示。MTT の Connection Parameter と Last Run は通常どおり出力（`export` にはこの option はなく、常に解決する） |

#### DSS

```powershell
python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI
```

default は簡潔な business field を返し、Excel export の column と一致します。

```json
{
  "task_count": 1,
  "rows": 1,
  "records": [
    {"Task Name": "..."}
  ]
}
```

より完全な diagnostic 情報が必要な場合は `--full`：

```powershell
python iics_cli.py get-task DI_FACT_SALES_DAILY DSS --folder JAPAN_SCRM_DI --full
```

**注意**：organization 内の別 folder に同名 DSS task がある場合、`--folder` により内部的には指定 folder の object を正確に特定できますが、底層の detail query は name-based なので、指定 folder の設定を 100% 取得できるとは限りません。結果に関連 warning が出た場合は内容を確認してください。

#### MTT

```powershell
python iics_cli.py get-task TASK_NAME MTT --folder JAPAN_SCRM_BAU
```

default も簡潔な business field を返します。**1 Source または Target transformation が 1 record** に対応し、1 task から複数 record が返る場合があります。

完全 detail が必要な場合は `--full`。full detail の `transformations` field が authoritative result です。

- 対象 MTT task 自身の raw parameter のみを source とし、所属 Mapping の parameter では補完しない
- original order を保持し、deduplicate せず、first / last のみを切り出さない

MTT detail query は具体的な object を正確に特定するため、**異なる folder にある同名 MTT を確実に区別できます**。DSS のような same-name ambiguity はありません。

MTT record は 26 列で、うち 3 列が Amendment 11 で追加されました。

| 列 | 意味 |
|---|---|
| `Last Run` | task の last run time。常に UTC（`yyyy-MM-ddTHH:mm:ss.SSSZ`、例：`2026-11-12T06:58:54.000Z` = 日本時間 15:58:54）。同一 task の全 row で同じ値を繰り返す。空欄は「値がない」ことのみを意味し、**未実行を意味しない**。「最後に成功した実行」とも解釈しない |
| `Source Connection Parameter` / `Target Connection Parameter` | その側の connection parameter 名。`""` = 明示的に parameter 化されていない、`null`（Excel では空欄）= parameter 化済みだが名前なし、または状態不明。反対側は常に `""` |

`Source Connection` / `Target Connection` の値の優先順位：解決された connection name → task 設定の default connection（`connDefault`、parameter 化が確認された connection のみ）→ connection ID。`--no-resolve` では常に ID。

注意：

- 出力されるのは **task の設定値** であり、ある実行で最終的に使われた connection であることは保証しません（parameter file で上書きされる可能性があり、本 tool は parameter file を読みません）。
- default の compact mode では、MTT の enrichment warning（runtime / mapping / connection / Last Run / connection parameter）を 1 件 1 行で **stderr** に出力します（`[IICS CLI] WARNING {kind}: {error}`）。stdout は引き続き `task_count` / `rows` / `records` のみ。構造化 warning が必要な場合は `--full`。

#### WORKFLOW

```powershell
python iics_cli.py get-task WORKFLOW_NAME WORKFLOW --folder JAPAN_SCRM_DI
```

完全 detail を返します（DSS / MTT のように default compact record ではありません）。folder の役割は DSS と同様で、内部的な object 特定には使えますが、organization 内の別 folder に同名 WORKFLOW がある場合、取得した detail が必ず指定 folder のものとは保証できません。

---

### 3. `resolve-connection` — connection ID を解決

```powershell
python iics_cli.py resolve-connection <id> [<id>...]
```

複数 ID を一度に指定可能：

```powershell
python iics_cli.py resolve-connection 000LVU0B000000000015 000LVU0B00000000009O
```

結果は `resolved`（解決成功）と `failed`（解決失敗）に分かれます。同一 run 中に同じ connection ID が再登場した場合、既に取得した result を再利用し、重複 request は行いません。

---

### 4. `export` — folder 全体をエクスポート

```powershell
python iics_cli.py export <folder> [--type DSS|MTT|WORKFLOW|ALL] [--details|--summary-only] [--format excel|json] [--filename NAME]
```

| parameter | 必須 | 説明 |
|---|---|---|
| `<folder>` | はい | 現在の account がアクセス権を持つ IICS folder（`JAPAN_*` 3 folder は検証済み） |
| `--type` | いいえ | default `ALL` |
| `--details` | いいえ | default behavior。full detail を取得してから export |
| `--summary-only` | いいえ | list-level information のみ export。detail は取得せず高速 |
| `--format` | いいえ | `excel`（default）または `json` |
| `--filename` | いいえ | custom output file name |

task type ごとに Excel column structure が異なります。

| type | 列数 | 1 row の意味 |
|---|---:|---|
| DSS | 12 | 1 task = 1 row |
| MTT | 26 | 1 Source または Target transformation = 1 row |
| WORKFLOW | 9 | 1 child task = 1 row。child がなくても 1 row を保持 |

`--type ALL` では固定順で複数 business sheet を生成します：`MTT Task → DSS Tasks → Workflow Tasks → Export Errors`。

**MTT 列番号の変更**：Amendment 11 で MTT sheet に `Last Run`（`Task Updated On` の後）、`Source Connection Parameter`（`Source Connection` の後）、`Target Connection Parameter`（`Target Connection` の後）を挿入したため、以降の列番号が後ろにずれます。固定列番号で Excel を読む script や数式は調整が必要です。`--summary-only` は detail を取得しないため、この 3 列は空欄です。

#### Export が完全成功したか判断する方法

command が返す JSON には以下が含まれます。

| field | 意味 |
|---|---|
| `task_count` | logical task count |
| `rows` | Excel に実際に書き込まれた row 数 |
| `failed_details_count` | detail query が失敗した task 数 |
| `enrichment_warnings_count` | non-fatal enrichment warning 数（export success 自体には影響しない） |
| `file_path` | output file path |

**重要**：1 つの MTT または WORKFLOW が複数 row に展開されることがあるため、`rows` は `task_count` より大きくなることがよくあります。**これは正常で、error ではありません。** `rows == task_count` を export completeness 判定に使わないでください。正しい見方は：

1. `failed_details_count` が 0 か
2. 0 でなければ、どの task が失敗したか確認し、その他 success 分はそのまま有効
3. `enrichment_warnings_count` は warning であり failure ではない

---

### 5. `export-selected` — 指定 task list のみをエクスポート

明確な task list が手元にあり、folder 全体を export したいわけではない場合に使用します。

```powershell
python iics_cli.py export-selected --input .\selected_tasks.json --output-dir .\exports
```

Python が受け付ける input は以下形式の JSON のみです。

```json
[
  {"name": "Task_A", "type": "DSS", "folder": "JAPAN_SCRM_DI"},
  {"name": "Task_B", "type": "MTT", "folder": "JAPAN_SCRM_BAU"},
  {"name": "Workflow_A", "type": "WORKFLOW", "folder": "JAPAN_COMMERCIAL_DWH"}
]
```

各 entry は `name`、`type`、`folder` の 3 field が必須です（3 task type 共通）。

> Mode A では、Excel table、CSV、Markdown table、plain text を Claude Code に貼り付けて上記 JSON に整理させることができます。自分で JSON を手書きする必要はありません。

**duplicate 判定**：2 record が重複かどうかは `type` + `folder` + `name` の 3 項目が完全一致するかで決まります。同じ task name / type でも folder が異なれば別 task として扱い、**両方処理され、上書きされません**。

export result は type ごとに個別 Excel file を生成します。例：`selected_DSS_20260909.xlsx`、`selected_MTT_20260909.xlsx`。今回対象のない type は file を生成しません。

result JSON には以下が含まれます。

| field | 意味 |
|---|---|
| `input_count` | raw input 件数 |
| `unique_count` | dedup 後の件数 |
| `duplicates_removed` | 完全 duplicate として除外された件数 |
| `requested` / `success` / `failed` | type ごとの request / success / failure 数 |
| `rows` | type ごとの実出力 row 数 |
| `file_path` | type ごとの output path |
| `failures` / `warnings` | failure detail / warning detail |

単一 task の failure は command 全体の failure を意味しません。その他の success task は正常に file 出力されます。

---

## 環境変数リファレンス

| variable | 必須 | 説明 |
|---|---|---|
| `IICS_SESSION_TOKEN` | **はい** | browser `USER_SESSION` Cookie value。「Session Token を取得」参照 |

以下 variable は framework 将来用に予約されており、v0.1 の現在の SSO 利用方式では設定不要です。

| variable | 説明 |
|---|---|
| `IICS_LOGIN_URL` | default value あり。通常は override 不要 |
| `IICS_USER` / `IICS_PASS` | 現行 version では未使用 |

---

## Command output format

すべての command は共通 output contract に従います。

- **stdout**：machine-readable JSON result — parse / use すべき内容
- **stderr**：progress log と diagnostic warning（MTT compact mode の `WARNING ...` 行など）。human-readable で、business data は含まない
- command normal completion：exit code `0`
- top-level `{"error": ...}`：exit code `1`。command 全体の failure を意味する

batch operation（`export` / `export-selected`）では command 自体の exit code が `0` でも、一部 task failure や warning が result 内に含まれる場合があります。「file が生成されたか」だけで全件 success と判断せず、上記 `failed_details_count`（または `failed`）field を確認してください。

---

## 既知の制限（v0.1）

- **strict read-only**：底層は GET request のみ許可し、変更操作は一切サポートしない。
- **Token は手動取得で、自動更新しない**：期限切れ後は browser から再コピーが必要。
- **DSS / WORKFLOW は cross-folder same-name で ambiguity があり得る**：detail query は name-based のため、指定 folder のものを 100% 保証できない。該当時は warning で通知。
- **MTT はこの制限を受けない**：cross-folder same-name でも正確に区別できる。
- **Mapping Location field は現時点で reliable data source がない**ため、推測値は表示しない。
- **`rows` と `task_count` は異なる統計単位**であり、`rows` が大きくなるのは正常。
- **MTT の connection / connection parameter は task の設定値**であり、ある実行で実際に使われた connection とは限らない（parameter file による上書きの可能性）。
- **MTT transformation の展開は `sourceConnectionId` / `targetConnectionId` で判断し、type で厳密に filter していない**：同名 field を持つ他の type（Lookup など）が条件次第で Source/Target row に誤分類される可能性がある。parameter 化済みで connection ID がない項目は warning のみで row は生成しない。
- **DSS の Last Run は提供しない**（要件は取り消し済み）。

---

## Troubleshooting

| 症状 | 想定原因 | 対応 |
|---|---|---|
| `py` / `python` が見つからない | Python 未導入、または PATH 未設定 | Python を再インストールし PowerShell を再起動 |
| `ModuleNotFoundError` | dependency が現在の Python environment に未導入 | `py -m pip install -r requirements.txt` を再実行 |
| `IICS_SESSION_TOKEN 未设置` | 現在の terminal window に token 未設定 | `$env:IICS_SESSION_TOKEN = "..."` を再実行 |
| 401 または session expired | Token 期限切れ | browser から `USER_SESSION` を再取得して再設定 |
| `get-task` で folder missing error | 3 task type すべて detail query に folder 必須 | `--folder <folder名>` を追加 |
| DSS / WORKFLOW result に same-name warning | organization 内の別 folder に同名 task が存在 | warning 内容を確認し、必要に応じて追加情報で確認 |
| Claude Code が許可済み command でも確認を出す | project workspace が未 trust、または local environment 差異 | workspace trust 済みか確認 |
| Claude Code が IICS request を期待通り解釈しない | Skill 未 load | `.claude/skills/iics-agent/SKILL.md` の存在と path を確認 |
| Excel file が生成されない | dependency 不足または output directory 書込失敗 | `openpyxl`、output directory、JSON の `error` を確認 |

---

## セキュリティ注意事項

- `IICS_SESSION_TOKEN` は現在の terminal window の環境変数にのみ置き、window を閉じたら再設定する。
- token を file に保存、print、Git commit、chat / mail / ticket に貼り付けない。
- `curl` 等で official CLI を bypass して IICS endpoint へ直接アクセスしない。
- Claude Code を `--dangerously-skip-permissions` で起動しない。
- `.claude/settings.json` が制御するのは Claude Code が呼び出せる tool であり、IICS 自体の write protection ではない。本当の read-only guarantee は Python 実装にある。
- `.claude/settings.local.json` は個人端末の local config で、project Git repository には commit しない。

---

## 参考ドキュメント

日常利用では本マニュアルと以下 2 file だけ確認すれば十分です。

```text
CLAUDE.md                              — Agent behavior rule
.claude/skills/iics-agent/SKILL.md     — IICS operation procedure
```

design background や historical decision rationale が必要な場合は、project 内の architecture decision record を参照してください。日常操作では読む必要はありません。
