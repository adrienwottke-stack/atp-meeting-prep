// Per-view estimates keep their anchor through repeated server responses.
const messages = {
  queued: "Wartet auf freien Verarbeitungsslot",
  paused: "Wartet auf deine Antwort",
  interrupted: "Verarbeitung unterbrochen – wartet auf Wiederaufnahme",
  unavailable: "Restzeit derzeit nicht schätzbar",
  finished: "",
};
export function historyPhase(run) {
  return ["queued", "needs_clarification", "completed", "partial", "failed", "cancelled"].includes(run.status)
    ? run.status : run.phase || run.status;
}
export function createHistoryTiming(clock = () => Date.now()) {
  const anchors = new Map();
  return function timing(run) {
    const id = run.run_id || run.id, now = clock();
    const state = run.timing_state || "unavailable";
    if (state !== "running" || !Number.isFinite(run.estimated_remaining_seconds)) {
      anchors.delete(id);
      return messages[state] ?? messages.unavailable;
    }
    const phase = run.phase || run.status;
    let anchor = anchors.get(id);
    const serverEnd = now + Math.max(0, run.estimated_remaining_seconds) * 1000;
    if (!anchor || anchor.phase !== phase) {
      anchor = { phase, end: serverEnd };
      anchors.set(id, anchor);
    } else anchor.end = Math.min(anchor.end, serverEnd);
    const seconds = (anchor.end - now) / 1000;
    if (seconds <= 0) return "Dauert länger als geschätzt";
    const duration = seconds < 60 ? "Voraussichtlich weniger als 1 Minute"
      : `Voraussichtlich noch ca. ${Math.ceil(seconds / 60)} ${Math.ceil(seconds / 60) === 1 ? "Minute" : "Minuten"}`;
    const time = new Intl.DateTimeFormat("de-DE", {
      hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin",
    }).format(new Date(anchor.end));
    return `${duration} · fertig gegen ${time} Uhr`;
  };
}

// Retain matching nodes so details and keyboard focus survive ordinary polls.
function patch(previous, next) {
  if (previous.nodeType !== next.nodeType || previous.nodeName !== next.nodeName ||
      (previous.nodeName === "A" && previous.getAttribute("href") !== next.getAttribute("href"))) {
    previous.replaceWith(next);
    return;
  }
  if (previous.nodeType === Node.TEXT_NODE) {
    if (previous.textContent !== next.textContent) previous.textContent = next.textContent;
    return;
  }
  for (const attribute of [...previous.attributes]) {
    if (attribute.name !== "open" && !next.hasAttribute(attribute.name)) previous.removeAttribute(attribute.name);
  }
  for (const attribute of next.attributes) {
    if (previous.getAttribute(attribute.name) !== attribute.value) previous.setAttribute(attribute.name, attribute.value);
  }
  const oldChildren = [...previous.childNodes], newChildren = [...next.childNodes];
  oldChildren.forEach((child, index) => newChildren[index] ? patch(child, newChildren[index]) : child.remove());
  newChildren.slice(oldChildren.length).forEach(child => previous.append(child));
}
export function patchHistoryCard(previous, next) {
  patch(previous.firstElementChild, next.firstElementChild);
  if (previous.dataset.actionKey !== next.dataset.actionKey) {
    previous.lastElementChild.replaceWith(next.lastElementChild);
    previous.dataset.actionKey = next.dataset.actionKey;
  }
}
