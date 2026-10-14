import QtQuick
import QtQuick.Controls

Button {
    id: control
    property color foreground: "#d8e9f8"
    property color accent: "#a5d3f5"
    property color surface: "#1c2a38"
    property bool primary: false
    property bool destructive: false
    property string hint: ""
    implicitHeight: 36
    implicitWidth: Math.max(36, contentItem.implicitWidth + 24)
    hoverEnabled: true
    font.family: "sans-serif"
    font.pixelSize: 13
    focusPolicy: Qt.StrongFocus
    Accessible.name: text
    Accessible.description: hint
    ToolTip.visible: hovered && hint.length > 0
    ToolTip.text: hint
    ToolTip.delay: 650
    contentItem: Text {
        text: control.text
        font: control.font
        color: control.primary ? "#101b25" : (control.destructive ? "#ffbab2" : control.foreground)
        opacity: control.enabled ? 1 : 0.42
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        textFormat: Text.PlainText
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 9
        color: control.primary ? control.accent : control.surface
        opacity: control.down ? 0.65 : (control.hovered ? 1 : 0.84)
        border.width: control.activeFocus ? 2 : 0
        border.color: control.accent
    }
}
