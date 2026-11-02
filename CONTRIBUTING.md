# Contributing

Issues and pull requests are welcome. For a new command or a change in behavior, open an issue first so we can agree on the shape before you write code.

## Rules

- **Standard library only.** No packages in `dependencies`, now or later.
- **One file.** The tool stays in `qtask.py`, so anyone can read it before they run it.
- **Tests with every change.** A fix comes with the test that fails without it.
- **Plain writing.** Short sentences, active voice, no marketing words, in docs, help text and error messages alike.

## Run the tests

```bash
python tests/test_qtask.py
```

Each test runs in a temp `QTASK_HOME`, so your own tasks stay untouched. The race tests start 20 processes at once and take about a minute. The launcher tests run when bash or Windows PowerShell is present and skip otherwise.

## Versions

Bump `__version__` in `qtask.py` and `version` in `pyproject.toml` together (a test checks they match), and add a section to `CHANGELOG.md`.
