# Current Overview of Full Research (COFR)

COFR helps AI coding agents maintain the live state of a long-running research project: what the project believes, what evidence supports or opposes it, what has been decided, what remains open, and what is at risk.

It is a concrete implementation of the [LLM Research Manager pattern](https://gist.github.com/djemec/990a26d214c94261fcf10c0506bfa156). You direct the research, your agent interprets the source material, and COFR preserves the resulting structured state across files and sessions.

## Install

COFR requires Python 3.12 or newer. Install the current version directly from GitHub:

```bash
pip install git+https://github.com/GPTomics/cofr.git
```

Then open your preferred terminal-capable coding agent in the root of the research project. You describe the research task; the agent operates COFR.

## Quick start

### Set up a project

Paste this prompt into the agent:

```text
Run `cofr --help` to learn how it works and remember it. Then inspect this research project, propose its initial COFR state, and ask for my approval before saving anything.
```

Review or correct the proposal, then paste:

```text
Save the approved initial project state using COFR.
```

### Return to an existing project

Paste this prompt at the beginning of a later session:

```text
Use COFR to catch me up on this project and tell me what needs attention next.
```

### Ingest new work

Replace the bracketed text and paste:

```text
Ingest [the new paper, result, note, or changed files] using COFR and tell me what it changes.
```

### Change the project's position

When your interpretation changes, paste:

```text
Use COFR to reflect this change in the project's belief: [the new conclusion and why].
```

### Create a project overview

Paste this prompt:

```text
Based on COFR's outline of the project, create an HTML overview of the current project state.
```

## Optional agent shortcuts

If you repeat the same prompts often, you can create—or work with your agent to create—COFR-based agent skills that capture your preferred shortcuts and project conventions. COFR itself does not ship or require agent skills; they are optional user-created helpers, and COFR's CLI help remains the source of truth.

## Technical documentation

Most users should not need to operate COFR directly. The complete CLI, schemas, file layout, authoring format, and development instructions are in the [Technical reference](https://github.com/GPTomics/cofr/blob/main/tech_docs.md).

## License

MIT
