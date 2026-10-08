## Code review guidelines

When reviewing a pull request, check in this order and report by severity (blocker / important / nit):
1. Bugs and logic errors, especially edge cases in PDF parsing and EPUB generation.
2. Security: unsafe file handling, path traversal, command injection, untrusted input, secrets in code.
3. Tests: new behavior covered? Existing tests still meaningful?
4. Breaking changes: CLI flags, output format, public functions, dependencies.
5. Readability: naming, duplication, error handling, typing.
6. Keep comments short, actionable, with a suggested fix. Don't nitpick formatting that a linter handles.