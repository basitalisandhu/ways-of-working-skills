# Security policy

This repository ships skills and scripts that run inside people's Claude Code sessions over their own files: Terraform plans, meeting notes, exception registers, RFCs and decision records. The skill scripts read the file or folder you point them at and print a report or write it to the path you name; nothing here makes a network call, stores a credential or reports usage anywhere.

## Supported versions

Only the latest release on `main` is supported. Pin a tag if you need stability, and update when a fix is announced in [CHANGELOG.md](CHANGELOG.md).

## Reporting a vulnerability

Please do not open a public issue for a security problem.

1. Use GitHub's private vulnerability reporting on this repository ("Security" tab, "Report a vulnerability").
2. If that is unavailable, open an issue titled "Security contact request" with no details, and the maintainer will reply with a private channel.

Include what you found, how to reproduce it, and what you think the impact is. You will get an acknowledgement within 5 working days and a fix or a mitigation plan within 30 days for confirmed issues.

## What counts

- A skill script that opens a network connection, starts a subprocess, or writes anywhere other than the `--out` or `--index` path it was given.
- A way for the content of an input file (a plan, a note, a register row) to change what a script computes beyond its documented rules, or to make a script read files outside the input it was given.
- Text in any file of this repository that addresses the model rather than the reader.
- A committed file holding real names, real infrastructure details or a secret-shaped string.

Rule mistakes (a resource type that should be flagged, an action line format that is misread, a date counted wrongly) are welcome as ordinary issues or pull requests with a test that shows them.

## What this plugin does and does not do

- No telemetry and no network access in the skill scripts.
- Scripts are standard-library Python, read the input you name, and print to standard output or write to the output path you name.
- `scripts/cli.py` starts the chosen skill script as a child process with the same Python and passes the arguments unchanged.
- Skill text tells Claude to treat the content of input files as untrusted data, never as instructions.
