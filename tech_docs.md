# COFR technical reference

This document describes COFR's command surface, storage model, structured authoring format, and development workflow. For the normal agent-driven workflow, start with the [README](README.md).

## Architecture and ownership

COFR is the deterministic bookkeeping layer beneath an AI agent. It walks a project, classifies files, ingests user-authored structured records, validates references and enums, materializes state in `.cofr/state.json`, and generates review artifacts. Text mechanically extracted from unstructured text files, PDFs, DOCX, and XLSX is used only for object-ID mention scanning; image ingestion records deterministic dimensions, format, and EXIF metadata.

COFR never infers new claims from prose, runs experiments, calls an LLM, or writes strategic narrative. The human directs and corrects judgment, the agent interprets sources and authors durable records, and COFR validates and tracks those records. Canonical state changes only from structured Markdown or YAML-pack inputs.

The CLI is the agent API. `cofr --help` documents the workflow, exit codes, and JSON envelope, and every subcommand has self-contained help. Parse, state-update, and artifact-rendering operations are deterministic for unchanged inputs, apart from documented timestamps and time-windowed timeline retention.

## Installation

The distribution metadata name is `cofr-research`; the installed console command and Python package are both named `cofr`. There is no published package-index release yet, so install from GitHub or a local clone.

```bash
# Install directly from GitHub
pip install git+https://github.com/GPTomics/cofr.git

# Clone, build, and install a wheel
git clone https://github.com/GPTomics/cofr.git
cd cofr
python -m pip install build
python -m build --wheel
python -m pip install dist/cofr_research-0.4.0-py3-none-any.whl

# Editable contributor install
pip install -e ".[dev]"
```

Runtime dependencies are PyYAML, python-frontmatter, MarkItDown with its PDF extra, Pillow, python-docx, and openpyxl. COFR requires Python 3.12 or newer.

## Commands

| Command | Purpose |
|---|---|
| `cofr init [<path>]` | Create `.cofr/` and `artifacts/` in the project. Idempotent. |
| `cofr add [--at <p>] [--dry-run] [--force] [<path>]` | Read one frontmatter-and-body Markdown record from stdin, route it to the appropriate YAML pack, and refresh. `--dry-run` previews the normalized record without writing. |
| `cofr refresh [--json] [--rebuild-timelines] [--rebuild-renames-log] [<path>]` | Rescan the project, update state, and regenerate all four artifacts. Repair flags rebuild derived data from history. |
| `cofr rename <old_id> <new_id> [<path>]` | Rename a record and cascade typed pointers across packs. |
| `cofr migrate --rollback [--from-history <snapshot> --yes-i-know-what-im-doing] [<path>]` | Roll back the v1-to-v2 layout migration. Restoring a completed migration requires the explicit history flags. |
| `cofr show state [--json] [<path>]` | Print the full materialized project state. |
| `cofr show claims [--json] [--summary] [--all] [<path>]` | List live claims; `--all` includes retired claims and `--summary` removes prose. |
| `cofr show questions [--json] [--summary] [--all] [<path>]` | List open or in-progress questions; `--all` includes inactive states. |
| `cofr show diff [--json] [<path>]` | Print the most recent refresh diff. |
| `cofr show overview [--json] [<path>]` | Print counts, trends, staleness, top questions, and top risks. |
| `cofr show risks [--json] [--summary] [--all] [<path>]` | List user-authored and transient computed risks. |
| `cofr show contradictions [--json] [<path>]` | Run the five computed contradiction rules and expose the agent-judged falsification review surface. |
| `cofr show review [--json] [<path>]` | Rank the next decision from open questions and decisions with eroded bases. |

Use `cofr <command> --help` for the complete input format, enum values, output shape, and examples for a particular command.

## Object types

Six object types are user-authored:

- `claim`: a belief or working hypothesis
- `evidence`: a source-linked observation or result that supports or opposes claims
- `experiment`: a run, analysis, or test; informational until represented as Evidence
- `decision`: a project-level call and its rationale, evidence, and claim dependencies
- `question`: a materially unresolved question blocking progress
- `risk`: a user-authored tension or risk

Generated Artifact records are the seventh domain type. Computed contradiction risks are transient analysis output and are not persisted as user state.

IDs are globally unique. An explicit `id` has highest authority; otherwise `cofr add` derives a stable slug from the title or type-specific primary field and falls back to a short UUID. Every persisted object also carries system-managed source and timing fields. Evidence owns the claim-evidence relationship through `claim_links`; Claims do not store reverse evidence-ID lists.

Live records are the default query surface. Claims are live unless retired; Evidence, Decisions, and Experiments are live only when active; Questions are live when open or in progress; Risks are live when open or accepted.

## Structured authoring

`cofr add` accepts one Markdown document containing YAML frontmatter and H2 body sections. It appends the normalized record to `claims.yaml`, `decisions.yaml`, `questions.yaml`, `risks.yaml`, or `experiments.yaml`, or to a per-source pack under `evidences/`.

### Adding evidence linked to a claim

Evidence links use `supports` or `opposes` polarity:

```bash
cat << 'EOF' | cofr add ~/my-project
---
type: evidence
id: ev_v5_holdout
strength: high
data_source: notebooks/v5_eval.ipynb
---

## Summary

Held-out evaluation at v5 scale shows no measurable improvement from action-conditioning.

## Affects claims

- claim_action_conditioning: opposes
EOF
```

After refresh, the linked Claim reports `counter_evidence_count: 1`.

### Letting COFR derive an ID

```bash
cat << 'EOF' | cofr add ~/my-project
---
type: question
title: Drift vs sampling temperature
priority: high
---

## Question

Does drift correlate with sampling temperature?

## Blocking impact

Blocks the v6 rollout plan.
EOF
```

This produces the stable ID `question_drift_vs_sampling_temperature` in `questions.yaml`.

Claims should retain the depth present in the sources: equations in Markdown/LaTeX, symbol definitions, quantitative predictions or bounds, assumptions, scope, and boundary conditions when available. Observations and measured results belong in Evidence. Do not invent precision, but do not flatten a technically specific source into generic prose. `cofr add --help` contains full examples for every type.

You may also edit YAML packs directly and run `cofr refresh`. Unknown user fields are preserved where practical. Malformed records warn and skip without partially rewriting a pack.

## JSON envelope

Every `--json` response has the same top-level structure:

```json
{
  "schema_version": 1,
  "cofr_version": "0.4.0",
  "generated_at": "2026-05-13T18:32:00Z",
  "project_path": "/absolute/project/path",
  "data": {}
}
```

Empty query results include a fixed `_note` signal rather than only an empty array. Load-time warnings appear in `_warnings`. `show state --json` is the complete agent view; summary modes provide bounded records without narrative fields.

Claims in query output include mechanically computed `supporting_evidence_count`, `counter_evidence_count`, and `mentioned_in` fields. The state response also includes index summaries and per-file ID mentions.

## Project files

COFR writes only within the target project:

| Path | Purpose |
|---|---|
| `.cofr/state.json` | Materialized canonical state with system timestamps and timelines. |
| `.cofr/index.json` | File inventory, extracted metadata, and object-ID mentions. |
| `.cofr/config.yaml` | Project metadata, exclusions, and timeline-retention settings. |
| `.cofr/history/{timestamp}.json` | Pre-refresh snapshots used for diffs and timeline recovery. |
| `.cofr/last_diff.json` | Cached latest refresh diff. |
| `.cofr/renames.json` | Confirmed rename log. |
| `.cofr/pending_renames.json` | Crash-recovery anchor for an in-flight rename. |
| `.cofr/legacy_markdown/` | Structured Markdown preserved during v1-to-v2 migration. |
| `claims.yaml`, `decisions.yaml`, `questions.yaml`, `risks.yaml`, `experiments.yaml` | User-authored per-type record packs. |
| `evidences/<source_slug>.yaml` | User-authored per-source Evidence packs. |
| `artifacts/current_state.md` | Deterministic current-state view. |
| `artifacts/what_changed.md` | Deterministic latest-diff view. |
| `artifacts/contradictions.md` | Computed contradictions, falsification pairs, and risks. |
| `artifacts/next_decision.md` | Deterministically ranked next-decision surface. |

Generated files use atomic replacement. Artifact Markdown begins with a generated-file warning and is overwritten on refresh. Durable truth belongs in structured records, not generated artifacts or chat prose.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success without warnings. |
| `1` | Success with soft validation or reference warnings; output was still produced. |
| `2` | Usage or authoring error. |
| `3` | Project not initialized. |
| `4` | Corrupt canonical or derived state requiring recovery. |

Forward references are retained with actionable warnings. Invalid enum values normalize to documented defaults where safe. Malformed YAML, unknown types, extraction failures, duplicate IDs, and broken references are reported without crashing a refresh or silently overwriting user-authored records.

## Development and testing

Install the development dependencies and run the suite from the repository root:

```bash
pip install -e ".[dev]"
pytest -v
```

The package uses stdlib `argparse`, dataclasses, JSON persistence, and deterministic Markdown generation. Runtime code lives under `src/cofr/`; tests and fixtures live under `tests/`.

## License

MIT
