# Thursday Codex workspace

Thursday launches Codex-built projects inside this directory. Each project receives a validated,
dedicated subdirectory so a build session does not edit Thursday itself or an unrelated project.

## How it works

1. Open **Codex Project** in Thursday's web UI.
2. Enter a lowercase workspace name using letters, numbers, and hyphens.
3. Choose the Codex default or an explicit model, then provide a concrete brief.
4. Refine the brief with Thursday or launch the interactive session in Kitty.
5. Follow progress in the terminal; Thursday only reports whether the session launched.

Codex receives `workspace-write` access to the selected project, approval expansion is disabled,
and Thursday's OpenAI provider credentials are removed from the child process. Personal generated
projects are ignored by Git by default; deliberately allowlist only artifacts suitable for public
source control.

## Included example

[Ritual](todo-app/README.md) is the public demonstration artifact: a responsive, dependency-free
habit tracker with mood check-ins, streaks, weekly insights, theme switching, and local persistence.

For the complete presentation flow, see [Thursday's demo playbook](../docs/DEMO.md).
