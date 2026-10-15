pragma ComponentBehavior: Bound
import QtQuick
import "SpeechLevels.js" as SpeechLevels

Item {
    id: meter
    property var envelope: null
    property int positionMs: 0
    property bool playing: false
    property bool reducedMotion: false
    property color accent: "#a5d3f5"
    readonly property var measured: SpeechLevels.reading(envelope, positionMs, playing, 29)
    readonly property real level: measured.level
    readonly property real displayLevel: Math.min(1, Math.sqrt(level) * 2.4)
    implicitHeight: 30
    implicitWidth: 180
    Accessible.role: Accessible.Graphic
    Accessible.name: "Speech playback level"
    Accessible.description: "Measured response audio at the current playback position"

    Row {
        anchors.centerIn: parent
        width: parent.width
        height: parent.height
        spacing: 4
        visible: !meter.reducedMotion
        Repeater {
            model: 29
            Rectangle {
                required property int index
                readonly property real amplitude: meter.measured.bars[index] || 0
                width: Math.max(2, (meter.width - 28 * 4) / 29)
                height: 2 + Math.min(1, Math.sqrt(amplitude) * 2.4) * (meter.height - 2)
                anchors.verticalCenter: parent.verticalCenter
                radius: width / 2
                color: meter.accent
                opacity: amplitude > 0 ? 0.9 : 0.3
            }
        }
    }
    // Reduced motion keeps the current measured level without a moving history.
    Rectangle {
        visible: meter.reducedMotion
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width
        height: 3
        radius: 1.5
        color: Qt.rgba(meter.accent.r, meter.accent.g, meter.accent.b, 0.2)
        Rectangle {
            width: parent.width * meter.displayLevel
            height: parent.height
            radius: parent.radius
            color: meter.accent
        }
    }
}
