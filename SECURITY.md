# Security

Report a security problem privately through a GitHub advisory: https://github.com/Abubakr-Alsheikh/qtask/security/advisories/new. Please do not open a public issue for it.

qtask makes no network calls and stores everything in `QTASK_HOME` on your machine. The reports that matter most: a way to run commands or write files outside `QTASK_HOME` through qtask's input, or a way for one local user to read another user's tasks.
