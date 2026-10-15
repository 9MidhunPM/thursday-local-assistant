pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import QtMultimedia
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import Quickshell.Wayland

// Instantiate ONCE at the shell root, outside any per-screen Variants.
Scope {
    id: root
    property string repoRoot: ""
    property var theme: null
    // A conversation needs a quieter surface than the translucent desktop bar.
    readonly property color background: theme
        ? Qt.rgba(theme.bgPanelStrong.r, theme.bgPanelStrong.g, theme.bgPanelStrong.b, 0.97)
        : "#f7141c26"
    readonly property color foreground: theme ? theme.textMain : "#d8e9f8"
    readonly property color secondary: theme ? theme.textSoft : "#b8d0e5"
    readonly property color accent: theme ? theme.accentBlue : "#a5d3f5"
    readonly property color border: theme ? theme.borderSoft : "#48607a"
    readonly property color surface: "#1c2a38"

    property bool opened: false
    property bool connected: false
    property bool ready: false
    property bool busy: false
    property bool muted: false
    property bool speakTyped: false
    property bool reducedMotion: false
    property bool settingsOpen: false
    property bool sendPending: false
    property bool hudDismissed: true
    property int conversationId: -1
    property int pendingRow: -1
    property string pendingText: ""
    property string latestAnswer: ""
    property string lastPrompt: ""
    property string submittedDraft: ""
    property bool historyNeedsReload: false
    property bool historyLoading: false
    property int historyRequest: 0
    property bool connectionError: false
    property string errorText: ""
    property string activity: ""
    property string voiceState: "idle"
    property string voiceSession: ""
    property string transcript: ""
    property string voiceError: ""
    property var task: null
    property var confirmations: []
    property var currentScreen: Quickshell.screens.length ? Quickshell.screens[0] : null
    property var audioQueue: []
    property var playingAudio: null
    property string generation: ""
    property var retiredGenerations: []
    property bool speechPreparing: false
    property string speechNotice: ""
    property string speechError: ""
    property int playbackPosition: 0
    property string reportedPlayingAudio: ""
    property var nativeOutput: ({known: false, audible: false, node_ids: [], reason: "checking"})
    property bool outputSetupComplete: false
    readonly property var audioEnvelope: playingAudio ? playingAudio.audio_levels || null : null
    readonly property bool playbackRunning: !!playingAudio && !muted && output.volume > 0
        && player.playbackState === MediaPlayer.PlayingState && player.hasAudio
        && player.error === MediaPlayer.NoError && devices.audioOutputs.length > 0
        && [MediaPlayer.LoadedMedia, MediaPlayer.BufferingMedia, MediaPlayer.BufferedMedia].indexOf(player.mediaStatus) >= 0
    readonly property bool speaking: playbackRunning && nativeOutput.known === true && nativeOutput.audible === true
    readonly property string outputNotice: {
        if (!playingAudio || !playbackRunning || speaking || !outputSetupComplete) return ""
        if (nativeOutput.reason === "muted") return "Thursday's system audio stream is muted. Check your sound settings."
        if (nativeOutput.reason === "volume_zero") return "Thursday's system audio volume is at zero. Check your sound settings."
        if (nativeOutput.reason === "unlinked") return "Thursday's audio has no active speaker connection."
        if (nativeOutput.reason === "unavailable") return "System audio output could not be checked."
        return ""
    }
    readonly property bool recording: voiceState === "listening"
    readonly property bool voiceActive: ["listening", "transcribing"].indexOf(voiceState) >= 0
    readonly property bool taskActive: !!task && ["running", "awaiting_approval"].indexOf(task.status) >= 0
    readonly property bool resumable: !!task && ["paused", "cancelled", "interrupted", "failed"].indexOf(task.status) >= 0
    readonly property string statusText: {
        if (recording) return "Listening"
        if (voiceState === "transcribing") return "Understanding"
        if (confirmations.length) return "Your approval is needed"
        if (speaking) return "Speaking"
        if (playingAudio && playbackRunning) return outputNotice ? "Voice output needs attention" : "Connecting voice output"
        if (playingAudio || speechPreparing) return "Preparing voice"
        if (connectionError) return "Connection needs attention"
        if (!connected) return "Connecting"
        if (!ready) return "Starting up"
        if (busy || taskActive) return activity || "Working on it"
        if (errorText || voiceError || speechError) return "Needs attention"
        if (resumable) return "Task paused"
        return "Ready when you are"
    }
    readonly property color stateColor: recording ? "#ffbab2" : (errorText || voiceError || speechError ? "#ffd08a" : accent)
    readonly property bool hudVisible: !opened && !hudDismissed && (
        recording || ["transcribing", "thinking", "working", "submitted", "error", "done"].indexOf(voiceState) >= 0
        || confirmations.length || busy || speaking || latestAnswer.length || voiceError.length || speechError.length)

    function chooseScreen() {
        var focused = Hyprland.focusedMonitor
        for (var i = 0; focused && i < Quickshell.screens.length; i++) {
            var candidate = Quickshell.screens[i]
            var monitor = Hyprland.monitorFor(candidate)
            if (monitor && monitor.name === focused.name) {
                currentScreen = candidate
                return
            }
        }
        if (Quickshell.screens.length) currentScreen = Quickshell.screens[0]
    }
    function openChat() {
        chooseScreen()
        opened = true
        Qt.callLater(function() { composer.forceActiveFocus() })
    }
    function send(command) {
        if (transport.running) transport.write(JSON.stringify(command) + "\n")
        else errorText = "The desktop connection is restarting. Please try again."
    }
    function submit(text) {
        text = text.trim()
        if (!text || sendPending || busy || taskActive) return
        lastPrompt = text
        submittedDraft = text
        errorText = ""
        voiceError = ""
        sendPending = true
        activity = "Working on it"
        stopAudio()
        send({command: "message", prompt: text, conversation_id: conversationId > 0 ? conversationId : null})
    }
    function toggleMicrophone() {
        if (recording) send({command: "voice_stop", session_id: voiceSession})
        else {
            stopAudio()
            chooseScreen()
            hudDismissed = false
            send({command: "voice_start"})
        }
    }
    function stopTask() {
        stopAudio()
        if (voiceSession && ["listening", "transcribing"].indexOf(voiceState) >= 0)
            send({command: "voice_cancel", session_id: voiceSession})
        if (taskActive) send({command: "task_cancel", task_id: task.id})
    }
    function decide(approved) {
        if (confirmations.length) send({command: "confirm", id: confirmations[0].id, approved: approved})
    }
    function releaseAudio(item) {
        if (item && item.audio_id) send({command: "audio_release", audio_id: item.audio_id})
    }
    function feedback(state, generationId) {
        if (generationId) send({command: "playback", state: state, generation_id: generationId})
    }
    function audioDeviceInfo(device) {
        var bytes = new Uint8Array(device.id)
        var id = ""
        for (var i = 0; i < bytes.length; i++) id += ("0" + bytes[i].toString(16)).slice(-2)
        return {id_hex: id, description: device.description, is_default: device.isDefault}
    }
    function stopAudio() {
        if (generation && retiredGenerations.indexOf(generation) < 0)
            retiredGenerations = retiredGenerations.concat([generation]).slice(-48)
        var old = playingAudio
        playingAudio = null
        player.stop()
        player.source = ""
        releaseAudio(old)
        for (var i = 0; i < audioQueue.length; i++) releaseAudio(audioQueue[i])
        audioQueue = []
        speechPreparing = false
        speechNotice = ""
        speechError = ""
        nativeOutput = {known: false, audible: false, node_ids: [], reason: "checking"}
        outputSetupTimer.stop()
        outputSetupComplete = false
        feedback("idle", generation)
    }
    function nextAudio() {
        if (playingAudio || !audioQueue.length || muted) return
        var queue = audioQueue.slice()
        playingAudio = queue.shift()
        audioQueue = queue
        playbackPosition = 0
        reportedPlayingAudio = ""
        nativeOutput = {known: false, audible: false, node_ids: [], reason: "checking"}
        outputSetupComplete = false
        outputSetupTimer.restart()
        player.source = playingAudio.local_url
        player.play()
    }
    function reportPlaying() {
        if (!speaking || !playingAudio) return
        var identity = String(playingAudio.audio_id || playingAudio.local_url)
        if (reportedPlayingAudio === identity) return
        reportedPlayingAudio = identity
        feedback("playing", playingAudio.generation_id)
    }
    function finishAudio(failed) {
        if (!playingAudio) return
        var item = playingAudio
        playingAudio = null
        releaseAudio(item)
        if (failed) feedback("failed", item.generation_id)
        if (audioQueue.length) nextAudio()
        else {
            feedback("idle", item.generation_id)
            if (!busy && !confirmations.length) dismissTimer.restart()
        }
    }
    function setVoice(data) {
        var previous = voiceSession
        voiceSession = data.session_id || data.id || ""
        voiceState = data.state || "idle"
        transcript = data.transcript || ""
        voiceError = data.error || ""
        if (voiceState === "listening" && previous !== voiceSession) {
            stopAudio()
            chooseScreen()
            latestAnswer = ""
            errorText = ""
            hudDismissed = false
            dismissTimer.stop()
        }
        if (voiceError) {
            hudDismissed = false
            dismissTimer.restart()
        }
        if (["cancelled", "idle"].indexOf(voiceState) >= 0 && !busy) dismissTimer.restart()
    }
    function appendMessage(role, content) {
        if (!content || ["user", "assistant"].indexOf(role) < 0) return
        messages.append({speaker: role, body: content})
        Qt.callLater(function() { history.positionViewAtEnd() })
    }
    function switchConversation(id) {
        if (!id || id === conversationId) return
        conversationId = id
        historyRequest += 1
        historyNeedsReload = true
        historyLoading = false
        messages.clear()
        pendingRow = -1
        pendingText = ""
        latestAnswer = ""
        task = null
    }
    function refreshHistory() {
        if (!historyNeedsReload || busy || taskActive || conversationId <= 0) return
        historyNeedsReload = false
        historyLoading = true
        historyRequest += 1
        send({command: "conversation", conversation_id: conversationId, request_id: historyRequest})
    }
    function handle(event) {
        var data = event.data || {}
        switch (event.type) {
        case "native_settings":
            muted = data.muted === true
            speakTyped = data.speak_typed === true
            reducedMotion = data.reduced_motion === true
            if (muted) stopAudio()
            break
        case "native_connection":
            connected = data.connected === true
            if (connected && connectionError) { errorText = ""; connectionError = false }
            break
        case "native_health":
            ready = data.model_ready === true
            if (data.status === "offline") connected = false
            break
        case "init":
            historyRequest += 1
            historyNeedsReload = false
            historyLoading = false
            conversationId = data.conversation_id || -1
            busy = data.busy === true
            ready = data.model_ready === true
            task = data.task || null
            confirmations = data.pending_confirms || []
            if (data.voice_state) setVoice(data.voice_state)
            messages.clear()
            pendingRow = -1
            pendingText = ""
            var list = data.history || []
            for (var i = 0; i < list.length; i++) appendMessage(list[i].role, list[i].content)
            if (confirmations.length) hudDismissed = false
            break
        case "user_message":
            switchConversation(data.conversation_id)
            if (historyLoading) { historyNeedsReload = true; historyLoading = false }
            historyRequest += 1
            appendMessage("user", data.content)
            pendingRow = -1
            pendingText = ""
            latestAnswer = ""
            busy = true
            sendPending = false
            break
        case "token":
            pendingText += data.chunk || ""
            latestAnswer = pendingText
            if (pendingRow < 0) {
                messages.append({speaker: "assistant", body: pendingText})
                pendingRow = messages.count - 1
            } else messages.setProperty(pendingRow, "body", pendingText)
            Qt.callLater(function() { history.positionViewAtEnd() })
            break
        case "final_response":
            latestAnswer = data.content || pendingText
            if (pendingRow >= 0) messages.setProperty(pendingRow, "body", latestAnswer)
            else appendMessage("assistant", latestAnswer)
            pendingText = ""
            pendingRow = -1
            activity = ""
            busy = false
            sendPending = false
            refreshHistory()
            if (!speaking && !audioQueue.length) dismissTimer.restart()
            break
        case "status":
            busy = data.busy === true
            if (!busy) { sendPending = false; activity = "" }
            refreshHistory()
            break
        case "task_updated":
            switchConversation(data.conversation_id)
            if (historyLoading && (!task || task.id !== data.id) && data.status === "running") {
                historyNeedsReload = true
                historyLoading = false
                historyRequest += 1
            }
            task = data
            if (data.detail) activity = friendlyActivity(data.detail)
            break
        case "tool_call":
            activity = friendlyActivity(data.tool || "")
            break
        case "tool_result":
            activity = "Checking the result"
            break
        case "confirm_required":
            var remaining = confirmations.filter(function(item) { return item.id !== data.id })
            confirmations = remaining.concat([data])
            hudDismissed = false
            dismissTimer.stop()
            break
        case "confirm_resolved":
            confirmations = confirmations.filter(function(item) { return item.id !== data.id })
            break
        case "voice_state":
            setVoice(data)
            break
        case "native_output":
            nativeOutput = data
            break
        case "tts_preparing":
            if (retiredGenerations.indexOf(data.generation_id) >= 0) break
            if (data.generation_id && generation !== data.generation_id) {
                stopAudio()
                generation = data.generation_id
            }
            speechPreparing = true
            break
        case "tts_audio":
            if (data.client_id !== "native" || muted || retiredGenerations.indexOf(data.generation_id) >= 0) {
                releaseAudio(data)
                break
            }
            if (generation !== data.generation_id) {
                stopAudio()
                generation = data.generation_id
            }
            speechPreparing = false
            audioQueue = audioQueue.concat([data])
            nextAudio()
            dismissTimer.stop()
            break
        case "tts_synthesis_done":
            if (generation === data.generation_id) speechPreparing = false
            break
        case "tts_status":
            if (data.client_id && data.client_id !== "native") break
            if (data.generation_id && generation !== data.generation_id) break
            if (retiredGenerations.indexOf(data.generation_id) >= 0) break
            speechPreparing = false
            if (data.state === "unavailable") {
                speechError = data.message || "Speech is unavailable. Your response is still available here."
                speechNotice = ""
                hudDismissed = false
            } else if (data.state === "fallback") {
                speechError = ""
                speechNotice = data.message || "Using the installed offline voice."
            } else if (data.state === "ready") {
                speechError = ""
                speechNotice = ""
            }
            break
        case "tts_stop":
            if (!data.generation_id || generation === data.generation_id) stopAudio()
            break
        case "native_command_result":
            if (data.command === "message") {
                if (submittedDraft && composer.text.trim() === submittedDraft) composer.clear()
                submittedDraft = ""
                sendPending = false
            }
            // Voice snapshots come only from the ordered SSE stream (and init).
            // An HTTP acknowledgement can arrive after a newer voice_state event.
            break
        case "native_conversation":
            if (data.conversation_id !== conversationId || data.request_id !== historyRequest
                || busy || taskActive) break
            historyLoading = false
            messages.clear()
            pendingRow = -1
            pendingText = ""
            var stored = data.messages || []
            for (var j = 0; j < stored.length; j++) appendMessage(stored[j].role, stored[j].content)
            break
        case "native_error":
            errorText = data.message || "Something went wrong. Please try again."
            if (data.command === "connection") connectionError = true
            if (data.command === "conversation") { historyNeedsReload = true; historyLoading = false }
            if (data.command === "message") submittedDraft = ""
            sendPending = false
            break
        case "error":
            errorText = data.content || "Something went wrong. Please try again."
            busy = false
            sendPending = false
            break
        case "shutdown":
            connected = false
            stopAudio()
            break
        }
    }
    function friendlyActivity(tool) {
        if (/desktop|screen|window|hypruse/.test(tool)) return "Checking your desktop"
        if (/search|fetch|web/.test(tool)) return "Looking it up"
        if (/file|path|directory/.test(tool)) return "Working with your files"
        if (/calendar/.test(tool)) return "Checking your calendar"
        if (/gmail|inbox/.test(tool)) return "Working with your mail"
        if (/codex/.test(tool)) return "Working with Codex"
        if (/spotify|music/.test(tool)) return "Getting your music ready"
        return "Working on it"
    }

    ListModel { id: messages }
    Connections {
        target: Quickshell
        function onScreensChanged() { root.chooseScreen() }
    }
    IpcHandler {
        target: "thursday"
        function toggle(): void { if (root.opened) root.opened = false; else root.openChat() }
        function open(): void { root.openChat() }
        function focus(): void { root.openChat() }
        function show(): void { root.openChat() }
        function hide(): void { root.opened = false }
        function audioStatus(): string {
            var available = []
            for (var i = 0; i < devices.audioOutputs.length; i++)
                available.push(root.audioDeviceInfo(devices.audioOutputs[i]))
            return JSON.stringify({muted: root.muted, speak_typed: root.speakTyped,
                playing: root.speaking, preparing: root.speechPreparing,
                playback_running: root.playbackRunning, native_output: root.nativeOutput,
                has_audio: player.hasAudio, playback_state: player.playbackState,
                media_status: player.mediaStatus, position_ms: player.position, error_code: player.error,
                output_available: devices.audioOutputs.length > 0,
                output_volume: output.volume, output_muted: output.muted,
                selected_device: root.audioDeviceInfo(output.device),
                default_device: root.audioDeviceInfo(devices.defaultAudioOutput),
                available_devices: available, measured_levels: !!root.audioEnvelope,
                audio_level: chatSpeech.level})
        }
    }
    Process {
        id: transport
        command: [root.repoRoot + "/.venv/bin/python", "-m", "assistant.native_client"]
        workingDirectory: root.repoRoot
        stdinEnabled: true
        running: root.repoRoot.length > 0
        stdout: SplitParser {
            onRead: data => {
                try { root.handle(JSON.parse(data)) } catch (_) { }
            }
        }
        onExited: {
            root.connected = false
            root.stopAudio()
            reconnectTimer.restart()
        }
    }
    Timer { id: reconnectTimer; interval: 1500; onTriggered: transport.running = true }
    Timer {
        id: dismissTimer
        interval: 9000
        onTriggered: {
            if (!root.recording && !root.busy && !root.speaking && !root.confirmations.length)
                root.hudDismissed = true
        }
    }
    MediaDevices { id: devices }
    AudioOutput {
        id: output
        device: devices.defaultAudioOutput
        volume: 0.85
        muted: root.muted
    }
    onSpeakingChanged: {
        if (speaking) outputSetupComplete = true
        reportPlaying()
    }
    Timer { id: outputSetupTimer; interval: 1500; onTriggered: root.outputSetupComplete = true }
    Timer {
        interval: 40
        repeat: true
        running: root.speaking && (root.opened || root.hudVisible)
        onTriggered: root.playbackPosition = player.position
    }
    MediaPlayer {
        id: player
        audioOutput: output
        onPositionChanged: root.playbackPosition = position
        onPlaybackStateChanged: root.reportPlaying()
        onMediaStatusChanged: {
            if (mediaStatus === MediaPlayer.EndOfMedia) root.finishAudio(false)
            else root.reportPlaying()
        }
        onErrorOccurred: {
            root.speechError = "Audio couldn't play. Your response is available below."
            root.finishAudio(true)
        }
    }

    PanelWindow {
        id: chatWindow
        screen: root.currentScreen
        visible: root.opened
        color: "transparent"
        anchors { top: true; right: true }
        margins { top: 58; right: 18 }
        implicitWidth: Math.max(280, Math.min(470, (root.currentScreen ? root.currentScreen.width : 1024) - 36))
        implicitHeight: Math.max(260, Math.min(710, (root.currentScreen ? root.currentScreen.height : 800) - 90))
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "thursday-companion"
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand

        Rectangle {
            anchors.fill: parent
            color: root.background
            border.width: 1
            border.color: root.border
            radius: 16
            clip: true
            Keys.onEscapePressed: root.opened = false

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 14
                RowLayout {
                    Layout.fillWidth: true
                    spacing: chatWindow.width < 400 ? 8 : 12
                    Rectangle {
                        visible: chatWindow.width >= 360
                        width: 10; height: 10; radius: 5
                        color: root.stateColor
                        opacity: root.connected ? 1 : 0.45
                        SequentialAnimation on opacity {
                            running: !root.reducedMotion && root.recording
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.45; duration: 650 }
                            NumberAnimation { to: 1; duration: 650 }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        Text { text: "Thursday"; font.pixelSize: 19; font.weight: Font.DemiBold; color: root.foreground }
                        Text {
                            Layout.fillWidth: true
                            text: root.statusText; color: root.secondary; font.pixelSize: 12
                            textFormat: Text.PlainText; elide: Text.ElideRight
                        }
                    }
                    NativeButton { text: chatWindow.width < 400 ? "Web" : "Web app"; hint: "Open full history, memory, and Codex controls"; foreground: root.foreground; accent: root.accent; onClicked: root.send({command: "open_web"}) }
                    NativeButton { text: "⋯"; hint: "Voice and display settings"; foreground: root.foreground; accent: root.accent; onClicked: root.settingsOpen = !root.settingsOpen }
                    NativeButton { text: "×"; hint: "Close Thursday · Escape"; foreground: root.foreground; accent: root.accent; onClicked: root.opened = false }
                }
                SpeechVisualizer {
                    id: chatSpeech
                    Layout.fillWidth: true
                    visible: root.speaking
                    envelope: root.audioEnvelope
                    positionMs: root.playbackPosition
                    playing: root.speaking
                    reducedMotion: root.reducedMotion
                    accent: root.accent
                }
                ColumnLayout {
                    visible: root.settingsOpen
                    Layout.fillWidth: true
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        NativeButton {
                            text: root.muted ? "Voice off" : "Voice on"
                            hint: "Toggle spoken responses"
                            onClicked: root.send({command: "settings", values: {muted: !root.muted}})
                        }
                        NativeButton {
                            text: root.reducedMotion ? "Motion off" : "Motion on"
                            hint: "Reduce animation"
                            onClicked: root.send({command: "settings", values: {reduced_motion: !root.reducedMotion}})
                        }
                        Item { Layout.fillWidth: true }
                        NativeButton { text: "Full app"; hint: "Open history and memory in the web app"; onClicked: root.send({command: "open_web"}) }
                    }
                    CheckBox {
                        text: "Speak typed replies"
                        checked: root.speakTyped
                        palette.windowText: root.foreground
                        palette.text: root.foreground
                        palette.highlight: root.accent
                        font.pixelSize: 13
                        onToggled: root.send({command: "settings", values: {speak_typed: checked}})
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: root.border; opacity: 0.6 }
                ListView {
                    id: history
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 20
                    model: messages
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    Column {
                        anchors.centerIn: parent
                        width: parent.width - 30
                        visible: messages.count === 0
                        spacing: 14
                        Text {
                            width: parent.width
                            text: "A little help,\nright here."
                            font.pixelSize: 27; font.weight: Font.Medium
                            color: root.foreground; wrapMode: Text.WordWrap
                        }
                        Text {
                            width: parent.width
                            text: "Ask about your desktop, find a file, or tell me what you want to get done."
                            font.pixelSize: 14; color: root.secondary
                            wrapMode: Text.WordWrap; lineHeight: 1.35
                        }
                        Text {
                            width: parent.width
                            text: "Hold Super + Alt to talk.\nAlt + Space opens or closes this conversation."
                            font.pixelSize: 12; color: root.secondary
                            wrapMode: Text.WordWrap; lineHeight: 1.4
                        }
                    }
                    delegate: Column {
                        required property string speaker
                        required property string body
                        width: history.width - 12
                        spacing: 6
                        Text {
                            text: parent.speaker === "user" ? "You" : "Thursday"
                            color: parent.speaker === "user" ? root.secondary : root.accent
                            font.pixelSize: 12; font.weight: Font.DemiBold
                        }
                        TextEdit {
                            width: parent.width
                            text: parent.body
                            readOnly: true
                            selectByMouse: true
                            textFormat: TextEdit.PlainText
                            wrapMode: TextEdit.Wrap
                            color: root.foreground
                            selectionColor: root.accent
                            selectedTextColor: "#101b25"
                            font.pixelSize: 14
                            padding: 0
                            Accessible.name: parent.speaker + ": " + parent.body
                        }
                    }
                }
                ColumnLayout {
                    visible: root.confirmations.length > 0
                    Layout.fillWidth: true
                    spacing: 10
                    Text { text: "Review this action"; font.pixelSize: 14; font.weight: Font.DemiBold; color: root.accent }
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.min(116, approvalText.implicitHeight)
                        clip: true
                        TextArea {
                            id: approvalText
                            text: root.confirmations.length ? root.confirmations[0].prompt : ""
                            readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                            textFormat: TextEdit.PlainText; color: root.foreground; font.pixelSize: 13
                            background: null
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Item { Layout.fillWidth: true }
                        NativeButton { text: "Deny"; onClicked: root.decide(false) }
                        NativeButton { text: "Allow"; primary: true; accent: root.accent; onClicked: root.decide(true) }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.errorText.length > 0 || root.voiceError.length > 0 || root.speechError.length > 0
                    text: root.errorText || root.voiceError || root.speechError
                    wrapMode: Text.WordWrap; color: "#ffd08a"; font.pixelSize: 13
                    textFormat: Text.PlainText
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.speechNotice.length > 0
                    text: root.speechNotice
                    wrapMode: Text.WordWrap; color: root.secondary; font.pixelSize: 12
                    textFormat: Text.PlainText
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.outputNotice.length > 0
                    text: root.outputNotice; color: "#ffd08a"; font.pixelSize: 13
                    wrapMode: Text.WordWrap; textFormat: Text.PlainText
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: root.taskActive || root.voiceActive || root.resumable || !!root.playingAudio || root.errorText.length > 0
                    spacing: 8
                    NativeButton { visible: root.taskActive || root.voiceActive; text: root.taskActive ? "Stop task" : "Cancel voice"; destructive: true; onClicked: root.stopTask() }
                    NativeButton { visible: root.resumable; text: "Resume"; enabled: !root.busy; onClicked: root.send({command: "task_resume", task_id: root.task.id}) }
                    NativeButton { visible: !!root.playingAudio; text: "Stop voice"; onClicked: root.stopAudio() }
                    NativeButton {
                        visible: root.errorText.length > 0 && !root.taskActive && root.lastPrompt.length > 0
                        text: "Retry"; enabled: !root.busy && !root.sendPending
                        onClicked: root.submit(root.lastPrompt)
                    }
                    Item { Layout.fillWidth: true }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: root.border; opacity: 0.6 }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(104, Math.max(50, composer.contentHeight + 22))
                    radius: 10; color: root.surface
                    border.width: composer.activeFocus ? 2 : 0
                    border.color: root.accent
                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 4
                        TextArea {
                            id: composer
                            placeholderText: root.recording ? "Listening… release Super + Alt to send" : "Tell me what you need…"
                            placeholderTextColor: root.secondary
                            color: root.foreground; font.pixelSize: 14
                            wrapMode: TextEdit.Wrap
                            selectByMouse: true
                            background: null
                            Accessible.name: "Message Thursday"
                            Keys.onReturnPressed: event => {
                                if (!(event.modifiers & Qt.ShiftModifier)) {
                                    root.submit(text)
                                    event.accepted = true
                                } else event.accepted = false
                            }
                            Keys.onEscapePressed: root.opened = false
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    NativeButton {
                        text: root.recording ? "Finish speaking" : "Microphone"
                        hint: "Click to start or finish recording"
                        enabled: root.connected
                        onClicked: root.toggleMicrophone()
                    }
                    Item { Layout.fillWidth: true }
                    Text { visible: chatWindow.width > 390; text: "Shift + Enter for a new line"; color: root.secondary; font.pixelSize: 10 }
                    NativeButton {
                        text: root.sendPending ? "Sending…" : "Send"
                        primary: true; accent: root.accent
                        enabled: root.connected && root.ready && !root.busy && !root.taskActive && !root.sendPending && composer.text.trim().length > 0
                        onClicked: root.submit(composer.text)
                    }
                }
            }
        }
    }

    PanelWindow {
        id: hud
        screen: root.currentScreen
        visible: root.hudVisible
        color: "transparent"
        anchors { top: true }
        margins { top: 64 }
        implicitWidth: Math.max(260, Math.min(510, (root.currentScreen ? root.currentScreen.width : 1024) - 40))
        implicitHeight: Math.min((root.currentScreen ? root.currentScreen.height : 800) - 110, hudContent.implicitHeight + 36)
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "thursday-voice"
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        Rectangle {
            anchors.fill: parent
            radius: 16; color: root.background
            border.width: 1; border.color: root.border
            clip: true
            ColumnLayout {
                id: hudContent
                anchors.fill: parent
                anchors.margins: 18
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Rectangle { width: 9; height: 9; radius: 5; color: root.stateColor }
                    Text { text: "Thursday"; font.pixelSize: 14; font.weight: Font.DemiBold; color: root.foreground }
                    Text { Layout.fillWidth: true; text: root.statusText; elide: Text.ElideRight; color: root.secondary; font.pixelSize: 12 }
                    NativeButton { text: "×"; hint: "Dismiss this response"; onClicked: root.hudDismissed = true }
                }
                SpeechVisualizer {
                    Layout.fillWidth: true
                    visible: root.speaking
                    envelope: root.audioEnvelope
                    positionMs: root.playbackPosition
                    playing: root.speaking
                    reducedMotion: root.reducedMotion
                    accent: root.accent
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.recording && !root.transcript.length
                    text: "I'm listening. Release Super + Alt when you're ready."
                    font.pixelSize: 14; color: root.foreground; wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.transcript.length > 0
                    text: root.transcript; color: root.secondary; font.pixelSize: 13
                    textFormat: Text.PlainText; wrapMode: Text.WordWrap
                    maximumLineCount: 3; elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.latestAnswer.length > 0
                    text: root.latestAnswer; color: root.foreground; font.pixelSize: 15
                    textFormat: Text.PlainText; wrapMode: Text.WordWrap
                    maximumLineCount: 8; elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.voiceError.length > 0 || root.errorText.length > 0 || root.speechError.length > 0
                    text: root.voiceError || root.errorText || root.speechError; color: "#ffd08a"; font.pixelSize: 13
                    textFormat: Text.PlainText; wrapMode: Text.WordWrap; maximumLineCount: 3
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.speechNotice.length > 0
                    text: root.speechNotice; color: root.secondary; font.pixelSize: 12
                    textFormat: Text.PlainText; wrapMode: Text.WordWrap; maximumLineCount: 2
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.outputNotice.length > 0
                    text: root.outputNotice; color: "#ffd08a"; font.pixelSize: 13
                    wrapMode: Text.WordWrap; textFormat: Text.PlainText; maximumLineCount: 3
                }
                RowLayout {
                    Layout.fillWidth: true
                    NativeButton { visible: root.taskActive || root.voiceActive; text: root.taskActive ? "Stop" : "Cancel voice"; destructive: true; onClicked: root.stopTask() }
                    NativeButton { visible: !!root.playingAudio; text: "Stop voice"; onClicked: root.stopAudio() }
                    Item { Layout.fillWidth: true }
                    NativeButton {
                        text: root.confirmations.length ? "Review action" : "Open conversation"
                        primary: root.confirmations.length > 0; accent: root.accent
                        onClicked: root.openChat()
                    }
                }
            }
        }
    }
}
