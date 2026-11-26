# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/kartalc23/hushtype/security/advisories/new)
instead. You can expect a first response within a week.

## Scope and design notes

hushtype handles sensitive input (your voice and, through evdev, keyboard
events), so these properties are treated as security guarantees:

- Audio is processed locally and never sent over the network.
- The only network access is the one-time model download from Hugging Face.
- Dictated text is never written to disk or to the log.
- The evdev listener only tracks key state to detect the configured combo;
  it does not record or log keystrokes.

A bug that breaks any of these is a security issue.
