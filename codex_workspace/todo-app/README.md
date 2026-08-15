# Ritual

Ritual is a warm, focused habit tracker included as the demonstration artifact for Thursday's Codex
project workflow. It is intentionally small enough to understand in minutes and polished enough to
show the result of a real agent-assisted build.

## Features

- Daily mood check-ins with immediate, supportive feedback.
- Habit creation, completion, deletion, per-habit streaks, and an overall daily progress score.
- A seven-day completion chart and adaptive insight copy.
- Responsive light and dark themes.
- Browser-local persistence through `localStorage`.
- Semantic controls, labels, live regions, keyboard-friendly dialogs, and responsive layout.
- No framework, package manager, account, database, or build step.

## Run locally

```bash
python3 -m http.server 4173
```

Open `http://127.0.0.1:4173`. Use **Reset demo data** before a presentation to restore the seeded
habits and completion history.

## Implementation

- `index.html` defines the accessible application structure and reusable habit template.
- `styles.css` owns the responsive visual system, themes, cards, charts, and dialog states.
- `app.js` owns state, date/streak calculations, rendering, event handlers, and local persistence.

Ritual never sends data to Thursday or a remote backend. The only network requests are the optional
Google Font files referenced by the page; the core application behavior is fully local.
