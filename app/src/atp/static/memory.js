import {
  el,
  append,
  button,
  tag,
  date,
  field,
  selectField,
  check,
  heading,
  notice,
  notify,
  bulletList,
  berlinToISO,
} from "./dom.js?v=focused-questions-1";
import { config, main, state, types, statuses, uid } from "./state.js?v=person-research-1";

// Application services are explicit; no dependency on classic-script globals.
export function createMemoryViews({ api, showError, busy, navigate, link, route, setBack }) {
// All stored content is rendered as text.
const memoryKinds = { company: "Unternehmen", project: "Projekt", general: "Allgemeine Learnings" };
const entryKinds = { fact: "Fakt", decision: "Entscheidung", goal: "Ziel", question: "Offener Punkt", learning: "Learning" };
const feedbackKinds = { correction: "Sachliche Korrektur", helpful: "Hilfreicher Vorschlag", unhelpful: "Unpassender Vorschlag", outcome: "Gesprächsergebnis" };

function memoryPageRoot() {
  // Async responses belong to their original page, even after navigation detaches it.
  const root = el("div", "memory-page");
  main.append(root);
  return root;
}

function memoryCard(title, description) {
  return append(el("section", "surface memory-card"), el("h2", null, title), description ? el("p", "muted", description) : null);
}
function memoryEvidence(memory) {
  const box = memoryCard("Berücksichtigtes Wissen", "Bestätigte Einträge mit Herkunft. Sie ersetzen keine öffentliche Recherche oder ICP-Regel.");
  (memory?.entries || []).forEach(e => append(box, append(el("article", "memory-entry"), tag(entryKinds[e.kind] || e.kind), el("p", null, e.text), el("small", null, `${e.context_name} · ${e.source} · Stand ${e.information_as_of || date(e.confirmed_at)} · Version ${e.version}`))));
  if (!memory?.entries?.length) box.append(el("p", "hint", "Keine bestätigten Wissenseinträge ausgewählt."));
  return box;
}
function memoryDownload(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const a = document.createElement("a"); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
}
function entryForm(context, entry, done) {
  const form = el("form", "memory-form");
  const prefix = "memory-" + (entry?.id || "new") + "-";
  const text = field("Erkenntnis", prefix + "text", entry?.text, { area: true, max: 2000, required: true });
  const kind = selectField("Art", prefix + "kind", entryKinds, entry?.kind || (context.kind === "general" ? "learning" : "fact"));
  const source = field("Herkunft", prefix + "source", entry?.source || "Manuelle Eingabe", { max: 1000, required: true });
  const asof = field("Fachlicher Stand", prefix + "asof", entry?.information_as_of || "", { max: 40, optional: true, placeholder: "z. B. Gespräch vom 15.10.2026" });
  const confirmed = check("Ich bestätige diese Erkenntnis zur Wiederverwendung.", entry?.status === "confirmed");
  const applies = el("fieldset"); applies.append(el("legend", null, "Gilt für diese Gesprächsarten (leer = alle)"));
  const checks = Object.entries(types).map(([value, label]) => { const c = check(label, entry?.applies_to?.includes(value)); applies.append(c.wrap); return { value, input: c.input }; });
  const submit = button(entry ? "Änderung speichern" : "Erkenntnis speichern", null, "button", "submit");
  append(form, text.wrap, kind.wrap, source.wrap, asof.wrap, applies, confirmed.wrap, submit);
  form.addEventListener("submit", e => { e.preventDefault(); busy(submit, async () => {
    await api(`/v1/contexts/${context.id}/entries${entry ? "/" + entry.id : ""}`, { method: entry ? "PATCH" : "POST", body: { text: text.input.value, kind: kind.input.value, source: source.input.value, information_as_of: asof.input.value, status: confirmed.input.checked ? "confirmed" : "draft", applies_to: checks.filter(c => c.input.checked).map(c => c.value), expected_version: entry?.version || null } });
    notify("Erkenntnis gespeichert."); done();
  }); });
  return form;
}
async function renderKnowledge(id) {
  const main = memoryPageRoot();
  try {
    if (id) return await renderMemoryContext(id, main);
    const contexts = (await api("/v1/contexts")).items;
    append(main, heading("Wissen & Learnings", "Was beim nächsten Gespräch zählt.", "Bestätigtes Firmen- und Projektwissen. Getrennt nach Kontext, mit nachvollziehbarer Herkunft."));
    const layout = el("div", "memory-grid");
    const list = memoryCard("Deine Kontexte", "Ein Kontext bündelt Wissen zu einem Unternehmen, Projekt oder allgemeine Learnings.");
    contexts.forEach(c => append(list, append(el("article", "memory-entry"), tag(memoryKinds[c.kind]), link(c.name, "/knowledge/" + c.id, "memory-context-link"), el("p", "muted", c.description), el("small", null, `Aufbewahrung bis ${date(c.expires_at)} · ${c.owned ? "Eigener Kontext" : "Freigegeben"}`))));
    if (!contexts.length) list.append(el("p", "hint", "Lege deinen ersten Kontext an und ergänze bestätigtes Wissen."));
    const add = memoryCard("Kontext anlegen");
    const form = el("form");
    const name = field("Name", "context-name", "", { required: true, max: 200 });
    const kind = selectField("Art", "context-kind", memoryKinds, "project");
    const description = field("Beschreibung", "context-description", "", { area: true, max: 3000, optional: true });
    const ext = field("Externe Zuordnung", "context-external", "", { optional: true, max: 200, hint: "Optional: spätere Kunden- oder Projekt-ID im Zielsystem." });
    const save = button("Kontext anlegen", null, "button", "submit");
    append(form, name.wrap, kind.wrap, description.wrap, ext.wrap, save); add.append(form);
    form.addEventListener("submit", e => { e.preventDefault(); busy(save, async () => { const c = await api("/v1/contexts", { method: "POST", body: { name: name.input.value, kind: kind.input.value, description: description.input.value, external_id: ext.input.value } }); navigate("/knowledge/" + c.id); }); });
    append(layout, list, add); main.append(layout);
    main.append(memoryImportPanel(contexts));
    const tasks = (await api("/v1/memory/tasks")).items;
    const history = memoryCard("Learning-Vorschläge", "Gespeicherte Vorschläge öffnen und vor der Übernahme prüfen.");
    tasks.forEach(t => append(history, append(el("article", "memory-entry"), link(`Learning-Vorschlag · ${date(t.created_at)}`, "/memory-tasks/" + t.id), tag(statuses[t.status] || t.status))));
    if (!tasks.length) history.append(el("p", "hint", "Noch keine Learning-Vorschläge vorhanden."));
    main.append(history);
  } catch (e) { showError(e, main); }
}
async function renderMemoryContext(id, main) {
  const query = new URLSearchParams(location.search).get("q") || "";
  const context = await api("/v1/contexts/" + id + "?q=" + encodeURIComponent(query));
  append(main, heading(memoryKinds[context.kind], context.name, context.description || "Bestätigtes Wissen, Entscheidungen und Feedback für diesen Kontext."));
  const actions = el("div", "actions"); actions.append(link("← Alle Kontexte", "/knowledge", "button secondary"));
  if (context.owned) actions.append(button("Wissen exportieren", () => busy(actions.lastChild, async () => memoryDownload(await api("/v1/memory/export?context_ids=" + id), "ATP-Wissen.json")), "button secondary"));
  main.append(actions);
  const grid = el("div", "memory-grid");
  const list = memoryCard("Gespeichertes Wissen", `Änderungen sind versioniert. Aufbewahrung bis ${date(context.expires_at)}.`);
  const search = field("Wissen durchsuchen", "memory-search", query, { type: "search", max: 500 });
  const searchForm = el("form");
  append(searchForm, search.wrap, button("Suchen", null, "button secondary", "submit"));
  searchForm.addEventListener("submit", e => { e.preventDefault(); navigate("/knowledge/" + id + "?q=" + encodeURIComponent(search.input.value)); });
  list.append(searchForm);
  for (const entry of context.entries) {
    const row = append(el("article", "memory-entry"), tag(entryKinds[entry.kind]), tag(entry.status === "confirmed" ? "Bestätigt" : "Entwurf", entry.status === "confirmed" ? "matching" : "unclear"), el("p", null, entry.text), el("small", null, `${entry.source} · Stand ${entry.information_as_of || date(entry.created_at)} · Version ${entry.version}`));
    if (context.editable) {
      const edit = el("details"); append(edit, el("summary", null, "Bearbeiten / bestätigen"), entryForm(context, entry, () => route())); row.append(edit);
      row.append(button("Erkenntnis löschen", () => busy(row.lastChild, async () => {
        if (!window.confirm("Diese Erkenntnis und die daraus abgeleiteten Vorbereitungen einschließlich PDFs löschen? Bereits heruntergeladene Kopien bleiben bestehen.")) return;
        await api(`/v1/contexts/${id}/entries/${entry.id}`, { method: "DELETE" }); notify("Löschung beauftragt."); route();
      }), "text-button"));
    }
    list.append(row);
  }
  if (!context.entries.length) list.append(el("p", "hint", "Noch keine Erkenntnisse gespeichert."));
  append(grid, list, context.editable ? append(memoryCard("Neue Erkenntnis"), entryForm(context, null, () => route())) : null); main.append(grid);
  if (context.editable) main.append(memoryFeedbackPanel([context], {}));
  const feedback = memoryCard("Feedback & Gesprächsergebnisse", "Feedback bleibt zunächst Beobachtung. Ein Learning entsteht erst nach deiner Prüfung.");
  const targets = (await api("/v1/contexts")).items.filter(c => c.editable);
  context.feedback.forEach(f => {
    const row = append(el("article", "memory-entry"), tag(feedbackKinds[f.kind]), el("p", null, f.text), el("small", null, `${date(f.created_at)} · ${f.reference || "Zum Kontext"}`));
    if (context.editable) {
      const target = selectField("Learning gilt für", "target-" + f.id, Object.fromEntries(targets.map(c => [c.id, `${memoryKinds[c.kind]}: ${c.name}`])), id);
      const propose = button("Learning vorschlagen", () => busy(propose, async () => {
        const t = await api("/v1/memory/proposals", { method: "POST", headers: { "Idempotency-Key": uid() }, body: { feedback_id: f.id, target_context_id: target.input.value } }); navigate("/memory-tasks/" + t.id);
      }), "button secondary"); append(row, target.wrap, propose);
    }
    feedback.append(row);
  });
  main.append(feedback);
  if (context.owned) {
    const admin = el("details", "surface memory-card"); admin.append(el("summary", null, "Kontext bearbeiten und Zugriffe verwalten"));
    const name = field("Name", "edit-context-name", context.name, { max: 200, required: true });
    const desc = field("Beschreibung", "edit-context-desc", context.description, { area: true, max: 3000 });
    const ext = field("Externe Zuordnung", "edit-context-ext", context.external_id, { max: 200 });
    const save = button("Kontext speichern", () => busy(save, async () => { await api("/v1/contexts/" + id, { method: "PATCH", body: { name: name.input.value, kind: context.kind, description: desc.input.value, external_id: ext.input.value } }); route(); }));
    append(admin, name.wrap, desc.wrap, ext.wrap, save);
    const recipient = field("Benutzer-ID", "share-user", "", { hint: "Kennung eines Benutzers derselben Organisation. Keine externe Freigabe." });
    const permission = selectField("Zugriff", "share-permission", { read: "Lesen", edit: "Lesen und bearbeiten" }, "read");
    const share = button("Zugriff freigeben", () => busy(share, async () => { await api(`/v1/contexts/${id}/shares`, { method: "POST", body: { user_id: recipient.input.value, permission: permission.input.value } }); route(); }), "button secondary");
    append(admin, el("h3", null, "Explizite Freigaben"), recipient.wrap, permission.wrap, share);
    context.shares.forEach(s => { const revoke = button("Zugriff entziehen", () => busy(revoke, async () => { await api(`/v1/contexts/${id}/shares/${s.user_id}`, { method: "DELETE" }); route(); }), "text-button"); append(admin, el("p", "small", `${s.user_id} · ${s.permission}`), revoke); });
    const remove = button("Kontext und abgeleitete Vorbereitungen löschen", () => busy(remove, async () => {
      if (!window.confirm("Kontext, Wissen, Feedback und daraus abgeleitete Vorbereitungen samt PDFs löschen? Heruntergeladene Kopien können nicht zurückgerufen werden.")) return;
      await api("/v1/contexts/" + id, { method: "DELETE" }); navigate("/knowledge");
    }), "button secondary"); admin.append(remove); main.append(admin);
  }
}
function memoryFeedbackPanel(contexts, reference) {
  const box = memoryCard("Feedback festhalten", "Korrigiere eine Aussage oder halte das Gesprächsergebnis fest. Bestätigtes Wissen ändert sich dadurch noch nicht.");
  if (!contexts.length) { append(box, el("p", "hint", "Verknüpfe für Feedback zuerst einen eigenen Kontext mit der Vorbereitung.")); return box; }
  const form = el("form");
  const context = selectField("Kontext", "feedback-context", Object.fromEntries(contexts.map(c => [c.id, c.name || c.id])), contexts[0].id);
  const kind = selectField("Feedback-Art", "feedback-kind", feedbackKinds, "correction");
  const text = field("Beobachtung oder Korrektur", "feedback-text", "", { area: true, max: 3000, required: true });
  const ref = field("Bezug zur Aussage", "feedback-reference", "", { max: 1000, optional: true });
  const save = button("Feedback speichern", null, "button", "submit");
  append(form, context.wrap, kind.wrap, text.wrap, ref.wrap, save);
  form.addEventListener("submit", e => { e.preventDefault(); busy(save, async () => {
    await api("/v1/memory/feedback", { method: "POST", body: { context_id: context.input.value, kind: kind.input.value, text: text.input.value, reference: ref.input.value, ...reference } });
    notify("Feedback gespeichert. Im Kontext kannst du daraus ein Learning vorschlagen lassen."); navigate("/knowledge/" + context.input.value);
  }); }); box.append(form); return box;
}
async function renderMemoryTask(id) {
  const main = memoryPageRoot();
  try {
    const task = await api("/v1/memory/tasks/" + id);
    if (location.pathname !== "/memory-tasks/" + id) return;
    setBack("/knowledge/" + encodeURIComponent(task.context_id));
    append(main, heading("Learning-Vorschlag", task.result?.context_name || "Vorbereitung läuft", "Der ursprüngliche Stand bleibt nachvollziehbar. Neue Erkenntnisse benötigen deine Bestätigung."));
    if (["queued", "running"].includes(task.status)) {
      main.append(notice(task.status === "queued" ? "Auftrag gespeichert. Der Serverworker übernimmt die Vorbereitung." : "Der Agent erstellt den Vorschlag aus den bestätigten Angaben."));
      state.poll = setTimeout(() => { if (location.pathname === "/memory-tasks/" + id) route(); }, 3000); return;
    }
    if (task.status !== "completed") { main.append(notice(task.error_code === "MEMORY_CHANGED" ? "Das ausgewählte Wissen wurde geändert. Bitte starte eine neue Vorbereitung mit dem aktuellen Stand." : "Die Vorbereitung konnte nicht abgeschlossen werden. Die Eingabe bleibt gespeichert.", "error")); return; }
    const r = task.result;
    if (r.fixture_mode) main.append(notice("Testmodus · Dieses Ergebnis wurde mit kontrollierten synthetischen Antworten erstellt."));
    if (task.kind === "learning") {
      const box = memoryCard("Vorschlag prüfen", r.narrative.reason);
      const text = field("Learning", "learning-confirm-text", r.narrative.text, { area: true, max: 2000, required: true });
      const applies = el("fieldset"); applies.append(el("legend", null, "Gilt für diese Gesprächsarten (leer = alle)"));
      const checks = Object.entries(types).map(([value, label]) => { const c = check(label); applies.append(c.wrap); return { value, input: c.input }; });
      const approved = check("Ich habe Inhalt und Geltungsbereich geprüft. Dieses Learning darf künftig verwendet werden.");
      const save = button("Learning bestätigen", () => busy(save, async () => {
        if (!approved.input.checked) { notify("Bitte die Prüfung bestätigen.", true); return; }
        await api(`/v1/memory/tasks/${id}/confirm`, { method: "POST", body: { text: text.input.value, kind: "learning", status: "confirmed", source: "Geprüfter Vorschlag", applies_to: checks.filter(c => c.input.checked).map(c => c.value) } }); notify("Learning bestätigt."); navigate("/knowledge/" + task.context_id);
      })); append(box, text.wrap, applies, approved.wrap, save); main.append(box); return;
    }
  } catch (e) { showError(e, main); }
}
function memoryImportPanel(contexts) {
  const box = memoryCard("Wissen importieren", "Wähle eine ATP-Exportdatei und ordne jeden Quellkontext ausdrücklich einem eigenen Zielkontext zu. Freigaben werden nicht übernommen.");
  const input = field("Exportdatei", "memory-import-file", "", { type: "file" }); input.input.accept = ".json,application/json";
  const content = el("div"); append(box, input.wrap, content);
  input.input.addEventListener("change", async () => {
    content.replaceChildren();
    try {
      const file = input.input.files[0]; if (!file || file.size > 180000) throw new Error("Bitte eine Exportdatei bis 180 KB auswählen.");
      const data = JSON.parse(await file.text()); if (data.format !== "atp-memory" || !Array.isArray(data.contexts)) throw new Error("Ungültige ATP-Exportdatei.");
      const fields = data.contexts.map(c => { const f = selectField("Ziel für " + c.name, "import-" + c.id, Object.fromEntries(contexts.filter(t => t.owned && t.kind === c.kind).map(t => [t.id, t.name])), "", "Zielkontext auswählen"); content.append(f.wrap); return { id: c.id, input: f.input }; });
      let payload = null;
      const result = el("p", "hint");
      const apply = button("Geprüften Import übernehmen", () => busy(apply, async () => { if (!payload) return; await api("/v1/memory/import", { method: "POST", body: { ...payload, apply: true } }); notify("Import abgeschlossen."); route(); })); apply.disabled = true;
      fields.forEach(f => f.input.addEventListener("change", () => { payload = null; apply.disabled = true; result.textContent = "Bitte die neue Zuordnung prüfen."; }));
      const preview = button("Importvorschau prüfen", () => busy(preview, async () => {
        const next = { package: data, mapping: Object.fromEntries(fields.map(f => [f.id, f.input.value])), apply: false };
        const r = await api("/v1/memory/import", { method: "POST", body: next }); payload = next; result.textContent = `${r.entries.filter(e => e.action === "import").length} neue Einträge; ${r.entries.filter(e => e.action === "already_imported").length} bereits importiert.`; apply.disabled = false;
      }), "button secondary"); append(content, preview, result, apply);
    } catch (e) { showError(e, content); }
  }); return box;
}
async function mountMemorySelection(parent, current) {
  if (!config.memory_enabled) return;
  try {
    const status = await api("/v1/memory/status"); if (!status.enabled) return;
    const contexts = (await api("/v1/contexts")).items.filter(c => c.kind !== "general");
    if (!contexts.length) return;
    const box = memoryCard("Gespeichertes Wissen", "Wähle bis zu zwei Kontexte. Berechtigte allgemeine Learnings werden zusätzlich berücksichtigt.");
    const preview = el("div");
    const inputs = contexts.map(c => { const checkBox = check(`${memoryKinds[c.kind]}: ${c.name}`, current.context_ids?.includes(c.id)); box.append(checkBox.wrap); return { id: c.id, input: checkBox.input }; });
    const update = async () => {
      const selected = inputs.filter(c => c.input.checked).map(c => c.id);
      if (selected.length > 2) { notify("Bitte höchstens zwei Kontexte auswählen.", true); return; }
      current.context_ids = selected; if (state.input) state.input.context_ids = selected;
      try {
        const params = new URLSearchParams({ context_ids: selected.join(","), meeting_type: current.meeting_type || "setup", company_website: current.company?.website || "" });
        const m = await api(`/v1/memory/preview?${params}`);
        preview.replaceChildren(...(m.entries?.length ? [memoryEvidence(m)] : []));
      } catch (e) { showError(e, preview); }
    };
    inputs.forEach(c => c.input.addEventListener("change", update)); box.append(preview); parent.append(box); await update();
  } catch (e) { if (e.status !== 401) showError(e, parent); }
}

return { memoryEvidence, memoryFeedbackPanel, renderKnowledge, renderMemoryTask, mountMemorySelection };
}
