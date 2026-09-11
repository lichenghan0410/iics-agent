---
name: iics-agent
description: >
  Query, inspect, resolve, or export Takeda Japan IICS DSS, MTT, and WORKFLOW
  tasks through the project's read-only iics_cli.py. Use this skill when the
  user asks to list IICS tasks, inspect task details, resolve connection IDs,
  inspect runtime or mapping information, export a folder, export selected
  tasks, or provides an Excel/CSV/Markdown/plain-text task list to inspect or
  export. Also use it for requests referring to DSS, Mapping Tasks/MTT,
  WORKFLOW/Linear Taskflow, IICS folders, task configuration, connections,
  transformations, or IICS Excel exports.
---

# IICS Operations

Use the project's existing `iics_cli.py` capabilities to perform IICS operations.

Do not reimplement validation, FRS discovery, API routing, parsing, enrichment,
counting, or Excel generation in the Agent layer.

The project-wide architecture, safety boundary, authentication rules, critical
IICS invariants, and working-directory behavior are defined in `CLAUDE.md`.
This skill defines task-specific execution procedures.

## Environment selection (required)

Every business command runs against one explicitly selected IICS environment:

```text
python iics_cli.py --env <DEV|TEST|PROD> <subcommand> ...
```

Rules:

- Pass `--env` only with the environment the user explicitly named.
- Never default to `PROD`.
- Never infer the environment from a folder name, username, serverUrl, or org name.
- If the user has not named an environment and `IICS_ENV` is not set, ask the user
  which environment to use instead of guessing. The CLI fails closed with
  `MissingEnvironmentError`.
- Pass `--env` **exactly once** per command. It may sit before or after the
  subcommand, but never both: a repeated `--env` (even with the same value) fails
  with `DuplicateEnvironmentArgumentError` and exit code 1. There is no
  first-wins / last-wins fallback. `IICS_ENV` plus one `--env` is not a
  duplicate — the command-line value wins by design.

## First-time environment binding (must not be automated)

```text
python iics_cli.py auth-check --env <ENV>     # read-only: Login + show org identity
python iics_cli.py auth-pin   --env <ENV>     # ONLY after the user confirms in chat
```

Procedure:

1. Run `auth-check` and show the returned `org_id` / `org_uuid` / `server_host`.
   Its stdout is machine-readable JSON only; the human follow-up instruction is
   printed on stderr. When `status` is `pin_required`, stdout carries exactly
   `environment`, `status`, `org_id`, `org_uuid`, `server_host` — do not expect
   or invent a `next_step` field.
2. **Stop. Wait for the user to confirm in writing that this org is the intended
   environment.** Never confirm on the user's behalf.
3. Only in a later turn, after that explicit confirmation, run `auth-pin`.

Known `org_id` short codes for manual cross-check (not a strong identity value):

```text
000LA8 -> DEV
000LVT -> TEST
000LVU -> PROD
```

Never run `auth-pin --force`; re-binding an environment is a decision the user
makes and executes.

Never read, create, or edit `iics_credentials.json`, and never print a username,
password, session ID, or the raw Login response.

## Available commands

```text
python iics_cli.py --env <ENV> list-tasks <folder> [--type DSS|MTT|WORKFLOW|ALL] [--page-size N]

python iics_cli.py get-task <task_name> <task_type> --folder <folder> [--full] [--no-resolve]

python iics_cli.py resolve-connection <id> [<id>...]

python iics_cli.py export <folder> [--type DSS|MTT|WORKFLOW|ALL]
    [--details|--summary-only] [--format excel|json] [--filename <name>]

python iics_cli.py export-selected --input <selected_tasks.json>
    [--output-dir <directory>]
```

All three task types require `--folder` for `get-task`.

Do not invent additional CLI flags or expose internal
`task_id` / `task_id_type` arguments to the user.

---

# 1. List tasks

Use `list-tasks` when the user wants to enumerate or count tasks in a folder.

Examples of intent:

- list DSS tasks in a folder
- show all MTTs
- count workflows
- show every supported task type in a folder

Command:

```text
python iics_cli.py list-tasks <folder> --type <type>
```

`<type>` can be:

```text
DSS
MTT
WORKFLOW
ALL
```

If the user does not restrict the task type, use `ALL`.

`ALL` means the three supported task types only. Do not interpret it as every
IICS design-object type.

Use the returned JSON `count` and `tasks` as authoritative. Do not recount the
task array unless needed only for presentation.

---

# 2. Get one task

Use `get-task` when the user asks for configuration or details of one DSS,
MTT, or WORKFLOW.

Required inputs:

```text
task name
task type
folder
```

Command:

```text
python iics_cli.py get-task <task_name> <task_type> --folder <folder>
```

If the folder is missing, ask for the folder rather than omitting it or
guessing one.

Use `--no-resolve` only when the user explicitly wants raw connection IDs or
does not need connection names.

Use `--full` only when full normalized diagnostic detail is actually needed,
such as inspection of internal IDs, raw payload, transformations,
enrichment warnings, or debugging.

For normal operational queries, prefer the default output.

## DSS get-task

Normal command:

```text
python iics_cli.py get-task <name> DSS --folder <folder>
```

Default DSS output is compact and uses the DSS Task Summary business schema:

```text
{
  "task_count": 1,
  "rows": ...,
  "records": [...]
}
```

Use:

```text
--full
```

when the user explicitly needs the normalized/full detail rather than the
business-facing compact record.

The folder is required for FRS discovery. DSS detail itself remains V2
by-name, so do not claim that the folder disambiguates the V2 detail response
when duplicate names exist elsewhere in the organization.

## MTT get-task

Normal command:

```text
python iics_cli.py get-task <name> MTT --folder <folder>
```

The Python layer performs:

```text
folder + MTT name
→ folder-scoped FRS discovery
→ frs_id
→ mttask/frs/{frs_id}
→ normalization / enrichment
```

Do not replace this flow with another API route.

Default MTT output is compact:

```text
{
  "task_count": 1,
  "rows": ...,
  "records": [...]
}
```

Each record corresponds to a Source or Target transformation row.

Use `--full` only when full normalized detail is needed.

When `--full` is used, treat Python's normalized `transformations` field as
the authoritative transformation result.

Do not:

- reconstruct transformations from `raw`
- reorder transformations
- deduplicate transformations
- keep only the first or last transformation
- generate or supplement transformations from Mapping `parameters[]`

Python builds `transformations` from the MTT task's own `raw.parameters[]`
in original order.

For transformation names, use Python's returned normalized value. The Python
rule is:

```text
parameter.name with surrounding "$" removed
→ fallback to parameter.label only when name is missing or empty
```

Do not independently reinterpret the raw fields.

For Source transformations:

- use Python's returned Source Connection
- use Python's returned Source Object
- use Python's returned Custom Query
- when Custom Query is present, do not restore a hidden/dummy Source Object

For Target transformations:

- use Python's returned Target Connection
- Operation
- Target Object

Do not infer Mapping Location from Task Location. Mapping Location is currently
unresolved unless Python explicitly returns a reliable value.

## WORKFLOW get-task

Normal command:

```text
python iics_cli.py get-task <name> WORKFLOW --folder <folder>
```

WORKFLOW detail remains V2 by-name.

The folder is required for FRS discovery and obtaining the folder-scoped
`frs_id`, but it does not add folder scoping to the V2 by-name detail endpoint.

WORKFLOW `get-task` returns the workflow detail rather than an MTT/DSS-style
compact transformation record set.

---

# 3. Resolve connection IDs

Use `resolve-connection` when the user provides one or more IICS connection IDs
and wants their readable names.

Command:

```text
python iics_cli.py resolve-connection <id> [<id>...]
```

Pass multiple IDs in one command when they are available together instead of
calling the command repeatedly.

Interpret:

```text
resolved
failed
```

from the Python result.

Do not independently query or reconstruct connection names when the CLI can
resolve them.

---

# 4. Export a folder

Use `export` when the user's requested scope is an entire folder or an entire
task type inside a folder.

Examples:

- export all DSS in JAPAN_SCRM_DI
- export every supported task in a folder
- create an Excel of all MTTs in one folder

Command:

```text
python iics_cli.py export <folder> --type <type> --format excel
```

Use `ALL` when the user wants all three supported task types.

Use JSON format only when requested or when the task specifically needs a JSON
artifact.

Normal operational export should use details. Use `--summary-only` only when
the user explicitly wants a fast list-level export without detail enrichment.

Do not use folder-wide `export` when the user supplied a specific selected task
list. Use `export-selected` instead.

## Export result interpretation

Treat these fields separately:

```text
task_count
rows
failed_details_count
enrichment_warnings_count
file_path
```

Meanings:

```text
task_count
= logical number of tasks returned by list/discovery

rows
= physical business-sheet rows written by the exporter

failed_details_count
= tasks whose primary detail request failed

enrichment_warnings_count
= non-fatal mapping/runtime/connection enrichment warnings
```

Do not use:

```text
rows == task_count
```

as an export-success test.

Physical rows may exceed logical task count because:

- one MTT can produce multiple transformation rows
- one WORKFLOW can produce multiple child rows

A top-level CLI `error` is a fatal command failure.

If there is no top-level error and:

```text
failed_details_count == 0
```

the primary task-detail retrieval completed without task-detail failures.

If:

```text
failed_details_count > 0
```

the export may still have produced a workbook, but it contains one or more
failed task details. Report the failures and the output file path separately.

If:

```text
enrichment_warnings_count > 0
```

report them as warnings, not as primary-detail failures.

Do not silently convert enrichment warnings into failed tasks.

## DSS export

DSS uses its dedicated 12-column business schema.

Each DSS task produces one business row.

Source Object follows the Python product rule:

```text
sourceObjects[0].name
```

Do not expand additional source objects or generate warnings for ignored
additional elements.

Do not manually reconstruct the DSS workbook or schema.

## MTT export

MTT uses its dedicated 23-column business schema.

One valid Source or Target transformation produces one business row.

A task can therefore produce multiple rows.

Use Python-normalized transformations and values. Do not reconstruct
transformations from raw MTT or Mapping payloads.

Mapping/runtime/connection enrichment failures are warnings unless Python
reports a primary detail failure.

## WORKFLOW export

WORKFLOW uses its dedicated 9-column business schema.

The row model is:

```text
one raw.tasks[] child = one row
```

Parent workflow fields repeat on each child row.

When `tasks[]` is empty or missing, the workflow still retains one business
row with empty child fields.

Do not fetch detail for WORKFLOW child tasks.

Do not interpret a workflow child `taskId` as a V3 FRS ID.

For `--type ALL`, Python generates the task-type-specific business sheets.
Do not manually merge the three task schemas.

---

# 5. Export a selected task list

Use `export-selected` whenever the user gives a specific list of tasks.

This includes lists supplied as:

- pasted Markdown tables
- copied Excel rows
- CSV-like text
- plain-text lists
- structured JSON

Do not replace a selected export with one or more folder-wide `export`
commands.

## Required normalized input

Convert the user's selected list into a JSON array with exactly the operational
identity fields required by the CLI:

```json
[
  {
    "name": "Task_A",
    "type": "DSS",
    "folder": "JAPAN_SCRM_DI"
  },
  {
    "name": "Task_B",
    "type": "MTT",
    "folder": "JAPAN_SCRM_BAU"
  },
  {
    "name": "Task_C",
    "type": "WORKFLOW",
    "folder": "JAPAN_COMMERCIAL_DWH"
  }
]
```

For every item:

```text
name   required
type   required
folder required
```

Normalize `type` to:

```text
DSS
MTT
WORKFLOW
```

Trim surrounding whitespace from `name`, `type`, and `folder`.

Do not invent a missing folder.

If any selected row lacks a usable folder, report which item is incomplete and
obtain the folder before running the selected export.

Do not add internal fields such as:

```text
task_id
task_id_type
frs_id
```

to the selected-input contract.

Python performs final validation and deduplication.

Deduplication key is:

```text
(type, folder, name)
```

Two entries with the same `name` and `type` but different folders are distinct
tasks and must both be preserved and processed.

Do not merge or drop cross-folder same-name tasks before passing the list to
Python.

## Selected export execution

After producing the JSON file, run:

```text
python iics_cli.py export-selected --input <selected_tasks.json> --output-dir <dir>
```

Do not issue one `get-task` call per row first just to reconstruct the same
information in the LLM context.

Do not generate the Excel files yourself.

Do not manually calculate success/failure totals when Python already returns
them.

The Python orchestration layer performs:

```text
input validation
→ normalization
→ deduplication
→ grouping by type/folder
→ folder-scoped discovery
→ detail retrieval
→ per-type Excel generation
→ result summary
```

## Selected export result

Use Python's returned summary as authoritative.

For each task type, interpret:

```text
requested
success
failed
rows
enrichment_warnings_count
file_path
```

Also inspect top-level:

```text
failures
warnings
input_count
unique_count
duplicates_removed
```

Report:

- how many input rows were supplied
- how many unique tasks remained after Python deduplication
- success/failure counts by task type
- output file paths that were generated
- primary failures
- enrichment or identity warnings

If DSS or WORKFLOW warnings explain the V2 by-name duplicate-name limitation,
preserve that limitation in the user-facing explanation rather than claiming
folder-level V2 detail disambiguation.

---

# 6. Input handling rules

Interpret natural-language task type aliases conservatively:

```text
DSS / Sync Task
MTT / Mapping Task
WORKFLOW / Linear Taskflow
```

Use the canonical CLI values:

```text
DSS
MTT
WORKFLOW
```

Do not infer a task type solely from a task-name prefix when the user or input
data already specifies the type.

For a request that lacks an essential identity field such as folder, do not
guess across the three supported folders.

For tabular input, preserve each row's explicit:

```text
name
type
folder
```

before converting it to selected-task JSON.

---

# 7. Reporting rules

After a CLI operation, summarize the Python result rather than reproducing
large raw payloads unless the user asks for them.

For list operations, report:

```text
folder
type
count
```

and show task names/details only to the extent useful for the request.

For single-task inspection, present the business fields the user asked about.
Prefer default compact output for routine operation and use full detail only
when needed.

For exports, report:

```text
file_path
task_count or requested/success/failed as applicable
rows when useful
failed detail count
enrichment warning count
```

Keep the distinction between:

```text
fatal command error
primary task-detail failure
non-fatal enrichment warning
physical output row count
logical task count
```

Do not claim success from row-count equality.

Do not claim DSS/WORKFLOW folder-level V2 detail disambiguation.

Do not expose session tokens or raw authentication material.

Do not expose usernames, passwords, `icSessionId`, the account `id`, or any part
of the raw Login response (`roles`, `usergroups`, `emails`, `securityQuestion`,
`securityAnswer`, `password`).

---

# 8. Failure handling

If stdout contains a top-level:

```json
{"error": "..."}
```

treat the command as failed and report the returned `error_type` and `error`
without inventing a workaround that bypasses the CLI.

For individual task failures inside a batch/export:

- preserve the remaining successful results
- report failed task names and error types
- do not classify the whole command as a fatal failure unless Python returned
  a top-level error

For enrichment warnings:

- preserve the task result
- report the warning separately
- do not count it as a primary detail failure

Authentication-related error types and what to tell the operator:

| `error_type` | Meaning | Report to operator |
|---|---|---|
| `MissingEnvironmentError` | No `--env` and no `IICS_ENV` | Ask which environment to use. Never pick one. |
| `UnknownEnvironmentError` | Name/alias not in the credential config | Show the available environment keys. |
| `DuplicateEnvironmentArgumentError` | `--env` given more than once in one command | Re-run with a single `--env`. Never guess which one was intended. |
| `CredentialConfigError` | Config file missing or invalid JSON | Ask the operator to fix `iics_credentials.json` themselves. |
| `MissingCredentialsError` | That environment's username/password is empty | Ask the operator to fill it in themselves. Never fill it in. |
| `InvalidCredentialsError` | Login rejected (401/403) | Report it. Do not retry — repeated attempts can lock the account. |
| `EnvironmentPinRequiredError` | Environment not bound yet | Run `auth-check`, show the org identity, and wait for explicit user confirmation. |
| `EnvironmentMismatchError` | Login org differs from the pinned org | Stop. Report it. Never `--force`. |
| `PermissionDeniedError` | HTTP 403, session valid but insufficient rights | Report it. The CLI correctly does not re-authenticate. |
| `AuthenticationError` | Still 401 after one automatic re-login | Report it; the account may no longer be valid in that org. |

Session refresh is automatic and process-local. Do not ask the operator for a
browser `USER_SESSION` value on the default path.

Follow `CLAUDE.md` for working-directory errors and safety restrictions.
