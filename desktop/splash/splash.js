// Start-up page of the desktop app. The shell (desktop/src-tauri/src/lib.rs) opens it again with
// ?reason=...&logs=... when the background service could not start or stopped.
const query = new URLSearchParams(window.location.search);
const reason = query.get("reason");
if (reason !== null) {
  document.getElementById("starting").hidden = true;
  document.getElementById("failed").hidden = false;
  document.getElementById("reason").textContent = reason;
  document.getElementById("logs").textContent = query.get("logs") ?? "";
}
