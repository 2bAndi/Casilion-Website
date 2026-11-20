# Diagnostics

> Placeholder documentation for the skeleton. Commands, option names and file paths are invented examples, not the real product. Replace this file with real documentation.

Good diagnostics save days of back and forth. Cassilion can describe its own state in one command.

## The diagnostics bundle

`cassilion diag --json` prints a JSON document with the version and build, the operating system, the effective configuration and the most recent warnings and errors from the log. Attach its output to a support ticket, or let your AI agent send it.

## What is left out

The bundle never contains your data. Passwords, keys and tokens in the configuration are replaced by placeholders before the bundle is printed. Support removes anything that still looks like a secret before analysis.

## Log files

The log directory is set by `storage.log_dir`. When you report a problem, include the log lines from a minute before to a minute after it happened.
