import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Ui.BarWidget {
  id: root
  moduleName: "wdg.omarecall"

  readonly property string pluginDir: {
    var url = String(Qt.resolvedUrl("."))
    return decodeURIComponent(url.replace(/^file:\/\//, "")).replace(/\/$/, "")
  }
  property int activeSessions: 0
  readonly property bool opened: false

  function toggle() {
    if (root.bar)
      root.bar.run("omarchy-shell shell toggle wdg.omarecall")
  }

  function refreshCount() {
    countProcess.running = false
    countProcess.running = true
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰑌"
    tooltipText: root.activeSessions > 0
      ? "OmaRecall · " + root.activeSessions + " active sessions"
      : "OmaRecall · AI session memory"
    Accessible.role: Accessible.Button
    Accessible.name: "Open OmaRecall session memory"
    onPressed: function() { root.toggle() }
  }

  Rectangle {
    visible: root.activeSessions > 0
    anchors.right: parent.right
    anchors.top: parent.top
    width: Math.max(Style.space(13), countText.implicitWidth + Style.space(5))
    height: Style.space(13)
    radius: height / 2
    color: Color.accent

    Text {
      id: countText
      anchors.centerIn: parent
      text: root.activeSessions > 9 ? "9+" : String(root.activeSessions)
      color: Color.background
      font.pixelSize: Style.font.caption - 2
      font.bold: true
    }
  }

  Process {
    id: countProcess
    command: [root.pluginDir + "/bin/omarecall", "session", "list",
              "--status", "active", "--limit", "100"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var result = JSON.parse(text)
          root.activeSessions = result.sessions ? result.sessions.length : 0
        } catch (error) {
          root.activeSessions = 0
        }
      }
    }
  }

  Timer {
    interval: 60000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refreshCount()
  }
}
