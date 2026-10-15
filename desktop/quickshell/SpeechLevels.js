.pragma library

// Show recent measured audio, never invented frequency bars or future samples.
function reading(envelope, positionMs, active, count) {
    var bars = []
    count = Math.max(1, Math.min(64, Math.floor(Number(count) || 21)))
    for (var i = 0; i < count; i++) bars.push(0)
    var empty = {level: 0, bars: bars, available: false}
    if (!active || !envelope || !Array.isArray(envelope.levels)) return empty
    var interval = Number(envelope.interval_ms)
    var position = Number(positionMs)
    if (!isFinite(interval) || interval <= 0 || !isFinite(position) || position < 0) return empty
    var index = Math.floor(position / interval)
    if (index >= envelope.levels.length) return empty
    function measured(at) {
        if (at < 0 || at >= envelope.levels.length) return 0
        var value = envelope.levels[at]
        return typeof value === "number" && isFinite(value) ? Math.max(0, Math.min(1, value)) : 0
    }
    var level = measured(index)
    // Silence clears the whole trace immediately, including its recent history.
    if (level > 0) {
        for (var bar = 0; bar < count; bar++) bars[bar] = measured(index - count + 1 + bar)
    }
    return {level: level, bars: bars, available: true}
}
