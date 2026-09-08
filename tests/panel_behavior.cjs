// Execute the panel's JavaScript handlers with minimal QML object substitutes.
const fs = require("node:fs");
const assert = require("node:assert/strict");
const panel = fs.readFileSync(process.argv[2], "utf8");

const previewBody = panel.split("  function beginPreview(mode) {")[1]
  .split("\n  function launchPreviewed")[0].replace(/\n  }\s*$/, "");
const preview = new Function("root", "projectInput", "goalInput", "contextProcess", "mode", previewBody);
for (const selectedSession of [{ id: "old-session", project_id: "old-project" }, null]) {
  const root = { cli: "/plugin/bin/omarecall", selectedSession };
  const process = {};
  preview(root, { text: "/chosen project" }, { text: "Continue" }, process, "relevant");
  assert.equal(root.errorText, "");
  assert.deepEqual(process.command.slice(-2), ["--project", "/chosen project"]);
  assert.equal(process.running, true);
}

const refreshBody = panel.split("  function refreshSessions(preferredId) {")[1]
  .split("\n  function continuationGoal")[0].replace(/\n  }\s*$/, "");
const refresh = new Function("root", "listProcess", "preferredId", refreshBody);
const block = panel.split("id: listProcess")[1].split("id: directoryPicker")[0];
const loadedBody = block.slice(block.indexOf("root.busy = false"), block.lastIndexOf("\n    }"));
const loaded = new Function("root", "listOut", "listErr", "goalInput", "code", loadedBody);
for (const [ids, selectedId, preferredId, oldIndex, expected] of [
  [["b", "a"], "a", undefined, 0, 1],
  [["new", "b", "a"], "a", "new", 2, 0],
  [["a"], "deleted", undefined, 1, 0],
  [[], "deleted", undefined, 0, -1],
]) {
  const root = {
    selectedSessionId: selectedId, selectedIndex: oldIndex,
    selectSession(index) { this.selectedIndex = index; },
  };
  refresh(root, {}, preferredId);
  loaded(root, { text: JSON.stringify({ sessions: ids.map(id => ({ id })) }) }, {}, {}, 0);
  assert.equal(root.errorText, "");
  assert.equal(root.selectedIndex, expected);
}
