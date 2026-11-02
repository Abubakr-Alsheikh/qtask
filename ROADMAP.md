# Roadmap

Plans, not promises. Open an issue to argue for or against any of them.

## 0.2: share work from local tasks

- **`promote`**: turn a local task into a GitHub issue, or into a plan file inside the repo, and link the two. Today `--gh-issue` and `--visibility public` only record that you did it by hand.
- **`gh sync`**: keep the status of promoted tasks and their issues in step, both ways.
- **HTML board**: one read-only page with columns by status, written to a file you open in a browser. No server.

## 0.3: easier to install and find

- **PyPI**: publish as `qtask-cli` (the name `qtask` is taken), so `pipx install qtask-cli` works.
- **Claude Code plugin**: package the skill as a plugin, so installing it is one command.
- **Shell completion** for bash, zsh and PowerShell.

## Later ideas

- Other issue trackers for `promote` and `sync`, such as GitLab.
- An MCP server, for agents that cannot run shell commands.

## Won't do

- A hosted service or accounts. qtask stays local.
- Dependencies outside the Python standard library.
