chrome.runtime.sendMessage({ type: "thursday.status" }, (status) => {
  document.querySelector("#state").textContent = status?.connected ? "Connected to Thursday" : "Waiting for Thursday";
  document.querySelector("#version").textContent = status?.version || "Unknown";
  document.querySelector("#action").textContent = status?.lastAction || "None";
  if (status?.lastError) document.querySelector("#state").textContent = `Last error: ${status.lastError}`;
});
