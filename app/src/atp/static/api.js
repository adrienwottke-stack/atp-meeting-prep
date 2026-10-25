import { el, append, notice, notify } from "./dom.js?v=focused-questions-1";
import { loginPath } from "./auth-navigation.js?v=auth-navigation-1";

export function createApi({ main, navigate, onAuthentication }) {
async function api(path, options = {}) {
  const objectBody = options.body && (Object.getPrototypeOf(options.body) === Object.prototype || Array.isArray(options.body));
  const body = objectBody ? JSON.stringify(options.body) : options.body;
  const response = await fetch(path, {
    ...options,
    body,
    credentials: "same-origin",
    headers: {
      ...(typeof body === "string" ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {}
  if (!response.ok) {
    if (response.status === 401) {
      onAuthentication(false);
    }
    const error = new Error(
      payload?.message ||
        (typeof payload?.detail === "string" ? payload.detail : null) ||
        "Die Anfrage konnte nicht abgeschlossen werden.",
    );
    error.status = response.status;
    error.fields = payload?.fields;
    error.code = payload?.code;
    error.requestId = response.headers.get("X-Request-ID") || payload?.request_id;
    throw error;
  }
  onAuthentication(true);
  return payload;
}
function showError(error, target) {
  if (error.status === 401) {
    navigate(loginPath(window.location));
    notify("Bitte anmelden. Deine Eingabe bleibt in diesem Fenster erhalten.");
    return;
  }
  const n = notice(error.message, "error");
  n.setAttribute("role", "alert");
  if (error.fields)
    append(
      n,
      el(
        "p",
        "small",
        typeof error.fields === "string"
          ? error.fields
          : JSON.stringify(error.fields),
      ),
    );
  if (error.requestId) n.append(el("p", "small", "Request-ID: " + error.requestId));
  (target || main).append(n);
}
async function busy(b, fn) {
  const old = b.textContent;
  b.disabled = true;
  b.textContent = "Bitte warten …";
  try {
    await fn();
  } catch (e) {
    notify(e.message, true);
    if (e.status === 401) navigate("/login");
  } finally {
    b.disabled = false;
    b.textContent = old;
  }
}

return { api, showError, busy };
}
