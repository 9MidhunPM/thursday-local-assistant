# Thursday native companion

Instantiate this module once at the root of a Quickshell configuration, outside
per-monitor `Variants`. The installer supplies the checkout path:

```qml
import "/path/to/ThursdayV3/desktop/quickshell" as ThursdayNative

ThursdayNative.Thursday {
    repoRoot: "/path/to/ThursdayV3"
    // Optional: theme: Theme
}
```

The optional `theme` object uses `bgPanelStrong`, `textMain`, `textSoft`,
`accentBlue`, and `borderSoft`. The default palette matches the native blue shell.
The chat and voice HUD work independently of the bar. `qs ipc call thursday open`
opens the conversation; `toggle`, `focus`, `show`, and `hide` are also available.

One Python helper owns the authenticated event connection and audio downloads.
It loads the checkout's local `.env`; tokens never enter QML, process arguments,
event URLs, or screenshots. Commands and events use newline-delimited JSON over
the helper's standard input and output. Audio is stored privately in the session
runtime directory and removed after playback or expiry.

Typed replies are silent by default. Enable **Speak typed replies** in the local
settings menu to hear them; the voice toggle mutes all native playback. Voice,
typed-reply, and reduced-motion preferences are local to
`$XDG_CONFIG_HOME/thursday/native.json` (default `~/.config/thursday/native.json`).
The **Web app** button opens detailed history and memory review; **Full app** in
the settings menu opens the same window. The installed **Thursday Web** launcher
and `./run_web.sh` also open it. Repeated launches focus the existing dedicated
Brave window for that local server.

The speech trace follows measured audio at the current playback position. It
stays flat during silence, loading, stopped playback, or a muted/zero-volume
PipeWire route. **Motion off** uses a static level bar. The helper observes only
the Quickshell output route while downloaded speech is retained, and never
changes volume. A successful output-route check cannot measure the physical
speaker or headphones.
