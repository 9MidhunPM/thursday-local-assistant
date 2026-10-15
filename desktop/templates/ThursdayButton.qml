import QtQuick
import QtQuick.Layouts
import Quickshell
import "root:/config"

Item {
    id: root
    // Preserve the bar component's public properties; the singleton owns state.
    property bool open: false
    property bool launching: false
    property bool ttsActive: false
    property bool recordingActive: false
    property bool pendingFocus: false
    property bool voiceOverlayActive: false
    implicitHeight: 28
    implicitWidth: 30
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: "Open Thursday"
    function toggle(): void {
        Quickshell.execDetached(["qs", "ipc", "call", "thursday", "toggle"])
    }
    function focusCompanion(): void {
        Quickshell.execDetached(["qs", "ipc", "call", "thursday", "focus"])
    }
    Keys.onReturnPressed: toggle()
    Keys.onSpacePressed: toggle()
    Rectangle {
        anchors.fill: parent
        radius: 8
        color: clickArea.containsMouse || root.activeFocus ? Theme.borderSoft : "transparent"
    }
    Text {
        anchors.centerIn: parent
        text: "󰚩"
        font.family: Theme.font
        font.pixelSize: 14
        color: clickArea.containsMouse || root.activeFocus ? Theme.accentCyan : Theme.textMain
        renderType: Text.NativeRendering
    }
    MouseArea {
        id: clickArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggle()
    }
}
