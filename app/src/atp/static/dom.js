// Shared DOM functions render external strings only as text or form values.
export function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}
export function append(parent, ...nodes) {
  nodes
    .flat()
    .filter((n) => n !== null && n !== undefined)
    .forEach((n) => parent.append(n));
  return parent;
}
export function button(text, fn, cls = "button", type = "button") {
  const b = el("button", cls, text);
  b.type = type;
  if (fn) b.addEventListener("click", fn);
  return b;
}

export function textValue(v) {
  if (v === null || v === undefined || v === "") return "Unbekannt";
  if (typeof v === "boolean") return v ? "Ja" : "Nein";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
export function date(v) {
  if (!v) return "Kein Termin angegeben";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return (
    new Intl.DateTimeFormat("de-DE", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "Europe/Berlin",
    }).format(d) + " Uhr"
  );
}
export function dateInput(v) {
  if (!v) return "";
  const d = new Date(v);
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Europe/Berlin",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
    .format(d)
    .replace(" ", "T");
}
export function berlinToISO(value) {
  const [year, month, day, hour, minute] = value.split(/[-T:]/).map(Number);
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  let timestamp = wall;
  for (let i = 0; i < 3; i++) {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-GB", {
        timeZone: "Europe/Berlin",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hourCycle: "h23",
      })
        .formatToParts(timestamp)
        .map((p) => [p.type, p.value]),
    );
    const displayed = Date.UTC(
      Number(parts.year),
      Number(parts.month) - 1,
      Number(parts.day),
      Number(parts.hour),
      Number(parts.minute),
      Number(parts.second),
    );
    timestamp = wall - (displayed - timestamp);
  }
  const result = new Date(timestamp).toISOString();
  if (dateInput(result) !== value)
    throw new Error(
      "Diese Uhrzeit existiert wegen der Zeitumstellung nicht. Bitte einen anderen Termin wählen.",
    );
  return result;
}
export function tag(text, cls = "") {
  return el("span", "tag " + cls, text);
}
export function notice(text, kind = "") {
  return el("div", "notice " + kind, text);
}
export function safeUrl(value) {
  if (/[\u0000-\u001f\\]/.test(value)) return null;
  try {
    const u = new URL(value);
    return ["https:", "http:"].includes(u.protocol) &&
      !u.username &&
      !u.password
      ? u.href
      : null;
  } catch {
    return null;
  }
}
export function externalLink(text, url, cls = "") {
  const safe = safeUrl(url);
  if (!safe) return el("span", cls, text);
  const a = el("a", cls, text);
  a.href = safe;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

export function notify(text, error = false) {
  const n = document.getElementById("notification");
  n.textContent = text;
  n.className = "notification" + (error ? " error" : "");
  n.hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => {
    n.hidden = true;
  }, 7000);
}

export function heading(eyebrow, title, subtitle) {
  const n = el("div", "intro");
  append(n, el("div", "eyebrow", eyebrow), el("h1", null, title));
  if (subtitle) append(n, el("p", "muted", subtitle));
  return n;
}

export function field(label, name, value = "", options = {}) {
  const wrap = el("div", "field");
  const id = "field-" + name;
  const l = el("label", null, label);
  l.htmlFor = id;
  if (options.optional) append(l, el("span", "optional", " · optional"));
  const input = el(options.area ? "textarea" : "input");
  input.id = id;
  input.name = name;
  if (!options.area) input.type = options.type || "text";
  input.value = value || "";
  if (options.required) input.required = true;
  if (options.max) input.maxLength = options.max;
  if (options.placeholder) input.placeholder = options.placeholder;
  if (options.auto) input.autocomplete = options.auto;
  append(wrap, l, input);
  if (options.hint) append(wrap, el("small", null, options.hint));
  return { wrap, input };
}
export function selectField(label, name, values, value, placeholder) {
  const wrap = el("div", "field");
  const l = el("label", null, label);
  l.htmlFor = "field-" + name;
  const input = el("select");
  input.id = "field-" + name;
  input.name = name;
  if (placeholder) {
    const o = el("option", null, placeholder);
    o.value = "";
    append(input, o);
    input.required = true;
  }
  Object.entries(values).forEach(([key, label]) => {
    const o = el("option", null, label);
    o.value = key;
    append(input, o);
  });
  input.value = value || "";
  append(wrap, l, input);
  return { wrap, input };
}
export function check(text, checked = false) {
  const label = el("label", "check");
  const input = el("input");
  input.type = "checkbox";
  input.checked = checked;
  append(label, input, el("span", null, text));
  return { wrap: label, input };
}

export function previewRow(dl, label, value) {
  const row = el("div", "preview-row");
  append(
    row,
    el("dt", null, label),
    el("dd", null, value || "Nicht angegeben"),
  );
  dl.append(row);
}

export function reportSection(code, title) {
  const n = el("section", "report-section");
  n.id = code;
  append(n, el("div", "eyebrow", code), el("h2", null, title));
  return n;
}
export function bulletList(values, empty) {
  if (!values?.length)
    return el("p", "muted", empty || "Keine Angaben vorhanden.");
  const ul = el("ul", "plain-list");
  values.forEach((t) => ul.append(el("li", null, t)));
  return ul;
}
