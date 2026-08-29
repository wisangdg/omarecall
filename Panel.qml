import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui as Ui

Item {
  id: root

  readonly property string selfId: "wdg.omarecall"
  readonly property string pluginDir: {
    var url = String(Qt.resolvedUrl("."))
    return decodeURIComponent(url.replace(/^file:\/\//, "")).replace(/\/$/, "")
  }
  readonly property string cli: pluginDir + "/bin/omarecall"
  readonly property string defaultProjectDirectory: {
    var configured = String(Quickshell.env("OMARECALL_DEFAULT_PROJECT") || "").trim()
    if (configured !== "") return configured
    return String(Quickshell.env("HOME") || "") + "/development/projects"
  }

  property var shell: null
  property bool opened: false
  property var sessions: []
  property int selectedIndex: -1
  property var detail: null
  property var preview: null
  property string view: "history"
  property string launchMode: "clean"
  property string selectedAgent: "codex"
  property bool busy: false
  property string errorText: ""
  property string noticeText: ""
  property string deleteConfirmId: ""
  property bool reopenAfterPicker: false

  readonly property var selectedSession: selectedIndex >= 0 && selectedIndex < sessions.length
    ? sessions[selectedIndex] : null
  readonly property string selectedSessionId: selectedSession
    ? String(selectedSession.id) : ""
  readonly property string selectedSessionStatus: selectedSession
    ? String(selectedSession.status) : ""
  readonly property color background: Color.menu.background
  readonly property color foreground: Color.menu.text
  readonly property color border: Color.menu.border
  readonly property color scrim: Color.menu.scrim
  readonly property color dim: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.62)
  readonly property color hairline: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.16)
  readonly property color urgent: Color.urgent
  readonly property string fontFamily: Style.font.menuFamily

  function commandError(text, fallback) {
    try {
      var payload = JSON.parse(String(text || ""))
      if (payload.error && payload.error.message) return String(payload.error.message)
    } catch (error) {}
    var cleaned = String(text || "").trim()
    return cleaned !== "" ? cleaned.split("\n").pop() : fallback
  }

  function open(payloadJson) {
    if (root.opened) return
    root.opened = true
    root.errorText = ""
    root.noticeText = ""
    root.refreshSessions()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function close() {
    if (!root.opened && !root.reopenAfterPicker) return
    root.reopenAfterPicker = false
    root.opened = false
    root.deleteConfirmId = ""
    if (root.shell && typeof root.shell.hide === "function")
      root.shell.hide(root.selfId)
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open("{}")
  }

  function backOrClose() {
    if (root.deleteConfirmId !== "") {
      root.deleteConfirmId = ""
      return
    }
    if (root.view === "preview") {
      root.view = "history"
      root.preview = null
      Qt.callLater(function() { keyCatcher.forceActiveFocus() })
      return
    }
    root.close()
  }

  function refreshSessions() {
    root.busy = true
    root.errorText = ""
    listProcess.running = false
    listProcess.running = true
  }

  function selectSession(index) {
    if (index < 0 || index >= root.sessions.length) return
    root.selectedIndex = index
    sessionList.positionViewAtIndex(index, ListView.Contain)
    var session = root.sessions[index]
    projectInput.text = String(session.project_path || "")
    if (goalInput.text.trim() === "")
      goalInput.text = "Continue: " + String(session.title || "project work")
    root.busy = true
    root.errorText = ""
    showProcess.command = [root.cli, "session", "show", String(session.id),
                           "--max-note-chars", "200000"]
    showProcess.running = false
    showProcess.running = true
  }

  function browseProjectDirectory() {
    if (directoryPicker.running || directoryPickerLaunchTimer.running) return
    root.errorText = ""
    root.reopenAfterPicker = true
    root.opened = false
    directoryPickerLaunchTimer.restart()
  }

  function beginPreview(mode) {
    root.errorText = ""
    root.noticeText = ""
    if (projectInput.text.trim() === "") {
      root.errorText = "Enter a project directory before starting an agent."
      projectInput.forceActiveFocus()
      return
    }
    if (goalInput.text.trim() === "") {
      root.errorText = "Describe the goal for the new agent session."
      goalInput.forceActiveFocus()
      return
    }
    if (mode !== "clean" && !root.selectedSession) {
      root.errorText = "Select a previous session first."
      return
    }
    root.launchMode = mode
    var args = [root.cli, "context", "build", "--mode", mode,
                "--max-tokens", "8000"]
    if (mode === "session")
      args.push("--session-id", String(root.selectedSession.id))
    else if (mode === "relevant")
      args.push("--project-id", String(root.selectedSession.project_id))
    root.busy = true
    contextProcess.command = args
    contextProcess.running = false
    contextProcess.running = true
  }

  function launchPreviewed() {
    if (!root.preview || root.busy) return
    var args = [root.cli, "launch",
                "--agent", root.selectedAgent,
                "--project", projectInput.text.trim(),
                "--goal", goalInput.text.trim(),
                "--mode", root.launchMode,
                "--max-tokens", "8000",
                "--expect-context", String(root.preview.fingerprint || "")]
    if (root.launchMode === "session" && root.selectedSession)
      args.push("--session-id", String(root.selectedSession.id))
    root.busy = true
    root.errorText = ""
    launchProcess.command = args
    launchProcess.running = false
    launchProcess.running = true
  }

  function togglePin() {
    if (!root.selectedSession || root.busy) return
    root.busy = true
    pinProcess.command = [root.cli, "pin", String(root.selectedSession.id),
                          "--value", root.selectedSession.pinned ? "false" : "true"]
    pinProcess.running = false
    pinProcess.running = true
  }

  function archiveSelected() {
    if (!root.selectedSession || root.busy) return
    root.busy = true
    archiveProcess.command = [root.cli, "archive", String(root.selectedSession.id)]
    archiveProcess.running = false
    archiveProcess.running = true
  }

  function completeSelected() {
    if (!root.selectedSession || root.busy) return
    root.busy = true
    completeProcess.command = [root.cli, "checkpoint", String(root.selectedSession.id),
                               "--status", "completed"]
    completeProcess.running = false
    completeProcess.running = true
  }

  function deleteSelected() {
    if (!root.selectedSession || root.busy) return
    var id = String(root.selectedSession.id)
    if (root.deleteConfirmId !== id) {
      root.deleteConfirmId = id
      return
    }
    root.busy = true
    deleteProcess.command = [root.cli, "delete", id, "--confirm", id]
    deleteProcess.running = false
    deleteProcess.running = true
  }

  function selectRelative(delta) {
    if (root.sessions.length === 0) return
    var next = Math.max(0, Math.min(root.sessions.length - 1, root.selectedIndex + delta))
    root.selectSession(next)
  }

  onShellChanged: {
    if (!root.opened && root.shell && root.shell.openPanelIds
        && root.shell.openPanelIds[root.selfId] === true)
      root.open("{}")
  }

  Process {
    id: listProcess
    command: [root.cli, "session", "list", "--limit", "100"]
    stdout: StdioCollector { id: listOut; waitForEnd: true }
    stderr: StdioCollector { id: listErr; waitForEnd: true }
    onExited: function(code) {
      root.busy = false
      if (code !== 0) {
        root.errorText = root.commandError(listErr.text, "Could not load session history.")
        return
      }
      try {
        var payload = JSON.parse(listOut.text)
        root.sessions = Array.isArray(payload.sessions) ? payload.sessions : []
        if (root.sessions.length === 0) {
          root.selectedIndex = -1
          root.detail = null
        } else {
          root.selectSession(Math.max(0, Math.min(root.selectedIndex, root.sessions.length - 1)))
        }
      } catch (error) {
        root.errorText = "OmaRecall returned invalid session data."
      }
    }
  }

  Process {
    id: directoryPicker
    command: []
    stdout: StdioCollector { id: directoryOut; waitForEnd: true }
    stderr: StdioCollector { id: directoryErr; waitForEnd: true }
    onExited: function(code) {
      var shouldReopen = root.reopenAfterPicker
      root.reopenAfterPicker = false
      if (shouldReopen) {
        root.opened = true
        Qt.callLater(function() { keyCatcher.forceActiveFocus() })
      }
      if (code === 1) return
      if (code !== 0) {
        root.errorText = root.commandError(directoryErr.text,
                                           "Could not open the project directory picker.")
        return
      }
      var selectedPath = String(directoryOut.text || "").trim()
      if (selectedPath === "") return
      projectInput.text = selectedPath
      goalInput.forceActiveFocus()
    }
  }

  Timer {
    id: directoryPickerLaunchTimer
    interval: 100
    repeat: false
    onTriggered: {
      directoryPicker.command = ["omarchy-file-select", "--title",
                                 "Choose an OmaRecall project", "--directory"]
      directoryPicker.running = true
    }
  }

  Process {
    id: showProcess
    stdout: StdioCollector { id: showOut; waitForEnd: true }
    stderr: StdioCollector { id: showErr; waitForEnd: true }
    onExited: function(code) {
      root.busy = false
      if (code !== 0) {
        root.errorText = root.commandError(showErr.text, "Could not load the selected session.")
        return
      }
      try { root.detail = JSON.parse(showOut.text).session }
      catch (error) { root.errorText = "OmaRecall returned invalid session detail." }
    }
  }

  Process {
    id: contextProcess
    stdout: StdioCollector { id: contextOut; waitForEnd: true }
    stderr: StdioCollector { id: contextErr; waitForEnd: true }
    onExited: function(code) {
      root.busy = false
      if (code !== 0) {
        root.errorText = root.commandError(contextErr.text, "Could not build context preview.")
        return
      }
      try {
        root.preview = JSON.parse(contextOut.text).context
        root.view = "preview"
      } catch (error) {
        root.errorText = "OmaRecall returned an invalid context preview."
      }
    }
  }

  Process {
    id: launchProcess
    stdout: StdioCollector { id: launchOut; waitForEnd: true }
    stderr: StdioCollector { id: launchErr; waitForEnd: true }
    onExited: function(code) {
      root.busy = false
      if (code !== 0) {
        root.errorText = root.commandError(launchErr.text, "Could not start the agent.")
        return
      }
      root.noticeText = "Agent started. Its OmaRecall session is now active."
      root.view = "history"
      root.preview = null
      root.refreshSessions()
    }
  }

  Process {
    id: pinProcess
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector { id: pinErr; waitForEnd: true }
    onExited: function(code) {
      root.busy = false
      if (code !== 0) root.errorText = root.commandError(pinErr.text, "Could not update pin.")
      else root.refreshSessions()
    }
  }

  Process {
    id: archiveProcess
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector { id: archiveErr; waitForEnd: true }
    onExited: function(code) {
      root.busy = false
      if (code !== 0) root.errorText = root.commandError(archiveErr.text, "Could not archive session.")
      else root.refreshSessions()
    }
  }

  Process {
    id: completeProcess
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector { id: completeErr; waitForEnd: true }
    onExited: function(code) {
      root.busy = false
      if (code !== 0) root.errorText = root.commandError(completeErr.text, "Could not complete session.")
      else root.refreshSessions()
    }
  }

  Process {
    id: deleteProcess
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector { id: deleteErr; waitForEnd: true }
    onExited: function(code) {
      root.busy = false
      if (code !== 0) root.errorText = root.commandError(deleteErr.text, "Could not delete session.")
      else {
        root.deleteConfirmId = ""
        root.noticeText = "Session deleted."
        root.refreshSessions()
      }
    }
  }

  PanelWindow {
    id: panel
    visible: root.opened
    color: "transparent"
    anchors { top: true; bottom: true; left: true; right: true }
    WlrLayershell.namespace: "omarecall"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.opened
      ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None

    Rectangle {
      anchors.fill: parent
      color: root.scrim
      MouseArea { anchors.fill: parent; onClicked: root.close() }
    }

    Item {
      id: keyCatcher
      anchors.fill: parent
      focus: true
      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Escape) {
          root.backOrClose(); event.accepted = true; return
        }
        if (root.view !== "history") return
        if (event.key === Qt.Key_Down) {
          root.selectRelative(1); event.accepted = true
        } else if (event.key === Qt.Key_Up) {
          root.selectRelative(-1); event.accepted = true
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
          if (root.selectedIndex >= 0) root.selectSession(root.selectedIndex)
          event.accepted = true
        } else if (event.key === Qt.Key_R) {
          root.refreshSessions(); event.accepted = true
        }
      }
    }

    Shortcut {
      sequence: "Escape"
      enabled: root.opened
      onActivated: root.backOrClose()
    }

    Rectangle {
      id: card
      width: Math.min(Style.space(980), panel.width - Style.space(32))
      height: Math.min(Style.space(690), panel.height - Style.space(32))
      anchors.centerIn: parent
      color: root.background
      radius: Style.cornerRadius
      border.color: root.border
      border.width: Math.max(1, Style.space(1))

      Item {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Style.space(18)
        height: Style.space(42)

        Text {
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          text: root.view === "preview" ? "OmaRecall · Context preview" : "󰑌  OmaRecall"
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.heading
          font.bold: true
        }

        Row {
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(8)

          Ui.Button {
            visible: root.view === "preview"
            text: "Back"
            focusable: true
            bordered: true
            foreground: root.foreground
            Accessible.role: Accessible.Button
            Accessible.name: "Back to session history"
            onClicked: root.backOrClose()
          }
          Ui.Button {
            text: "Refresh"
            visible: root.view === "history"
            enabled: !root.busy
            focusable: true
            foreground: root.foreground
            Accessible.role: Accessible.Button
            Accessible.name: "Refresh session history"
            onClicked: root.refreshSessions()
          }
          Ui.Button {
            text: "Close"
            focusable: true
            foreground: root.foreground
            Accessible.role: Accessible.Button
            Accessible.name: "Close OmaRecall"
            onClicked: root.close()
          }
        }
      }

      Row {
        id: historyView
        visible: root.view === "history"
        anchors.top: header.bottom
        anchors.bottom: statusArea.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Style.space(18)
        anchors.rightMargin: Style.space(18)
        anchors.topMargin: Style.space(8)
        anchors.bottomMargin: Style.space(10)
        spacing: Style.space(16)

        Rectangle {
          width: Style.space(318)
          height: parent.height
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.035)
          radius: Style.cornerRadius
          border.color: root.hairline

          Text {
            id: historyLabel
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.margins: Style.space(12)
            text: "Session history · " + root.sessions.length
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true
          }

          Text {
            visible: root.sessions.length === 0 && !root.busy
            anchors.centerIn: parent
            width: parent.width - Style.space(40)
            text: "No sessions yet. Enter a project and goal on the right, then start clean."
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          ListView {
            id: sessionList
            anchors.top: historyLabel.bottom
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: Style.space(8)
            clip: true
            spacing: Style.space(5)
            model: root.sessions
            currentIndex: root.selectedIndex
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}

            delegate: Rectangle {
              required property int index
              required property var modelData
              width: sessionList.width - Style.space(8)
              height: Style.space(64)
              radius: Style.cornerRadius
              color: index === root.selectedIndex
                ? Style.selectedFillFor(root.foreground, Color.accent)
                : (rowMouse.containsMouse
                   ? Style.hoverFillFor(root.foreground, Color.accent)
                   : "transparent")
              border.color: index === root.selectedIndex ? Color.accent : "transparent"
              border.width: index === root.selectedIndex ? Math.max(1, Style.space(1)) : 0
              Accessible.role: Accessible.ListItem
              Accessible.name: (modelData.pinned ? "Pinned, " : "")
                + String(modelData.title) + ", " + String(modelData.agent)
                + ", status " + String(modelData.status)
              Accessible.description: "Press Enter to inspect this AI session"
              Accessible.onPressAction: root.selectSession(index)

              Column {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: Style.space(10)
                spacing: Style.space(3)
                Text {
                  width: parent.width
                  text: (modelData.pinned ? "★ " : "") + String(modelData.title)
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: index === root.selectedIndex
                }
                Text {
                  width: parent.width
                  text: String(modelData.agent) + " · " + String(modelData.status)
                    + " · " + String(modelData.project_name)
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              MouseArea {
                id: rowMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.selectSession(index)
              }
            }
          }
        }

        Item {
          width: parent.width - Style.space(318) - parent.spacing
          height: parent.height

          Column {
            anchors.fill: parent
            spacing: Style.space(8)

            Text {
              width: parent.width
              text: root.detail ? String(root.detail.title) : "Start a new remembered session"
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }

            Text {
              width: parent.width
              text: root.detail
                ? String(root.detail.agent) + " · " + String(root.detail.status)
                  + " · " + String(root.detail.updated_at)
                : "Use a prior session or begin clean. Sessions started elsewhere are not captured automatically in this MVP."
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            ScrollView {
              width: parent.width
              height: Style.space(210)
              clip: true
              TextArea {
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                text: root.detail ? String(root.detail.note || "")
                  : "Select a session to inspect its goal, decisions, pending work, files, and warnings."
                textFormat: TextEdit.PlainText
                color: root.foreground
                selectionColor: Color.accent
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                background: Rectangle {
                  color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.035)
                  radius: Style.cornerRadius
                  border.color: root.hairline
                }
                Accessible.name: "Selected session note"
              }
            }

            Text {
              text: "Project directory"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
            Row {
              width: parent.width
              spacing: Style.space(7)

              Ui.TextField {
                id: projectInput
                width: parent.width - browseButton.width - parent.spacing
                text: root.defaultProjectDirectory
                placeholderText: "/path/to/project"
                foreground: root.foreground
                Accessible.name: "Project directory"
                Accessible.description: "Directory where the new agent will start"
              }

              Ui.Button {
                id: browseButton
                width: Style.space(92)
                text: directoryPicker.running ? "Opening…" : "Browse…"
                enabled: !directoryPicker.running
                focusable: true
                bordered: true
                foreground: root.foreground
                Accessible.role: Accessible.Button
                Accessible.name: "Browse project directory"
                Accessible.description: "Choose the project folder using the system file picker"
                onClicked: root.browseProjectDirectory()
              }
            }

            Text {
              text: "New session goal"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
            Ui.TextField {
              id: goalInput
              width: parent.width
              placeholderText: "What should the agent continue or accomplish?"
              foreground: root.foreground
              Accessible.name: "New agent session goal"
            }

            Row {
              width: parent.width
              spacing: Style.space(7)
              Text {
                width: Style.space(62)
                anchors.verticalCenter: parent.verticalCenter
                text: "Agent"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Repeater {
                model: ["codex", "claude", "opencode"]
                Ui.Button {
                  required property string modelData
                  text: modelData
                  selected: root.selectedAgent === modelData
                  focusable: true
                  bordered: true
                  foreground: root.foreground
                  Accessible.role: Accessible.RadioButton
                  Accessible.name: "Use " + modelData
                  Accessible.checked: root.selectedAgent === modelData
                  onClicked: root.selectedAgent = modelData
                }
              }
            }

            Row {
              width: parent.width
              spacing: Style.space(7)
              Ui.Button {
                width: (parent.width - parent.spacing * 2) / 3
                height: Style.space(40)
                text: "Relevant project"
                enabled: !!root.selectedSession && !root.busy
                focusable: true
                bordered: true
                foreground: root.foreground
                Accessible.role: Accessible.Button
                Accessible.name: "Preview all relevant project context"
                onClicked: root.beginPreview("relevant")
              }
              Ui.Button {
                width: (parent.width - parent.spacing * 2) / 3
                height: Style.space(40)
                text: "This session"
                enabled: !!root.selectedSession && !root.busy
                focusable: true
                bordered: true
                foreground: root.foreground
                Accessible.role: Accessible.Button
                Accessible.name: "Preview context from this session only"
                onClicked: root.beginPreview("session")
              }
              Ui.Button {
                width: (parent.width - parent.spacing * 2) / 3
                height: Style.space(40)
                text: "Start clean"
                enabled: !root.busy
                focusable: true
                bordered: true
                foreground: root.foreground
                Accessible.role: Accessible.Button
                Accessible.name: "Preview a clean session without recalled memory"
                onClicked: root.beginPreview("clean")
              }
            }

            Row {
              visible: !!root.selectedSession
              width: parent.width
              spacing: Style.space(7)
              Ui.Button {
                text: root.selectedSession && root.selectedSession.pinned ? "Unpin" : "Pin"
                enabled: !root.busy
                focusable: true
                foreground: root.foreground
                Accessible.role: Accessible.Button
                Accessible.name: text + " selected session"
                onClicked: root.togglePin()
              }
              Ui.Button {
                text: "Complete"
                enabled: !root.busy && root.selectedSessionStatus === "active"
                focusable: true
                foreground: root.foreground
                Accessible.role: Accessible.Button
                Accessible.name: "Mark selected session completed"
                onClicked: root.completeSelected()
              }
              Ui.Button {
                text: "Archive"
                enabled: !root.busy && root.selectedSessionStatus !== ""
                  && root.selectedSessionStatus !== "archived"
                focusable: true
                foreground: root.foreground
                Accessible.role: Accessible.Button
                Accessible.name: "Archive selected session"
                onClicked: root.archiveSelected()
              }
              Ui.Button {
                text: root.deleteConfirmId === root.selectedSessionId
                  ? "Confirm delete" : "Delete"
                enabled: !root.busy
                focusable: true
                bordered: root.deleteConfirmId === root.selectedSessionId
                foreground: root.deleteConfirmId === root.selectedSessionId
                  ? root.urgent : root.foreground
                Accessible.role: Accessible.Button
                Accessible.name: text + " selected session"
                Accessible.description: root.deleteConfirmId === root.selectedSessionId
                  ? "Permanent deletion. Press again to confirm." : "Requires a second confirmation"
                onClicked: root.deleteSelected()
              }
            }
          }
        }
      }

      Item {
        id: previewView
        visible: root.view === "preview"
        anchors.top: header.bottom
        anchors.bottom: statusArea.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Style.space(18)

        Text {
          id: previewMeta
          anchors.top: parent.top
          anchors.left: parent.left
          anchors.right: parent.right
          text: root.preview
            ? (root.launchMode + " · " + root.preview.estimated_tokens + " estimated tokens · "
               + root.preview.redaction_count + " redactions"
               + (root.preview.truncated ? " · truncated" : ""))
            : "Building preview…"
          textFormat: Text.PlainText
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          Accessible.name: text
        }

        ScrollView {
          anchors.top: previewMeta.bottom
          anchors.bottom: launchButton.top
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.topMargin: Style.space(10)
          anchors.bottomMargin: Style.space(12)
          clip: true

          TextArea {
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            text: root.preview && String(root.preview.packet || "") !== ""
              ? String(root.preview.packet)
              : "No previous memory will be inserted. A new OmaRecall session and checkpoint contract will still be created."
            textFormat: TextEdit.PlainText
            color: root.foreground
            selectionColor: Color.accent
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            background: Rectangle {
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.035)
              radius: Style.cornerRadius
              border.color: root.hairline
            }
            Accessible.name: "Exact context that will be made available to the agent"
          }
        }

        Ui.Button {
          id: launchButton
          anchors.bottom: parent.bottom
          anchors.right: parent.right
          width: Style.space(190)
          height: Style.space(42)
          text: root.busy ? "Starting…" : "Start " + root.selectedAgent
          enabled: !!root.preview && !root.busy
          focusable: true
          bordered: true
          selected: true
          foreground: root.foreground
          Accessible.role: Accessible.Button
          Accessible.name: "Start " + root.selectedAgent + " with the previewed context"
          onClicked: root.launchPreviewed()
        }
      }

      Item {
        id: statusArea
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Style.space(18)
        anchors.rightMargin: Style.space(18)
        anchors.bottomMargin: Style.space(10)
        height: Style.space(24)

        Text {
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: root.errorText !== "" ? "Error: " + root.errorText
            : root.noticeText !== "" ? root.noticeText
            : root.busy ? "Working…"
            : "↑/↓ select · Enter inspect · R refresh · Esc back/close"
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: root.errorText !== "" ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          Accessible.name: text
          Accessible.description: root.errorText !== "" ? "OmaRecall error message" : "OmaRecall status"
        }
      }
    }
  }
}
