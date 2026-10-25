import { el, append, button, field, selectField, tag, date, notify } from "./dom.js?v=focused-questions-1";
import { uid } from "./state.js?v=focused-questions-1";

const kinds = {
  correction: "Aussage korrigieren",
  outcome: "Gesprächsergebnis festhalten",
  unhelpful: "Vorbereitung verbessern",
  helpful: "Hilfreichen Ansatz merken",
};
const prompts = {
  correction: ["Was ist richtig?", "Zum Beispiel: Frau Sommer verantwortet den Einkauf, nicht die IT."],
  outcome: ["Was kam im Gespräch heraus?", "Zum Beispiel: Erst den Angebotsprozess prüfen. Im nächsten Gespräch soll der Einkauf dabei sein."],
  unhelpful: ["Was soll die nächste Vorbereitung besser machen?", "Zum Beispiel: Weniger allgemeine KI-Fragen. Auf die Übergabe zwischen Vertrieb und Fertigung eingehen."],
  helpful: ["Was hat im Gespräch geholfen?", "Zum Beispiel: Der Einstieg über den Angebotsprozess hat die konkreten Engpässe sichtbar gemacht."],
};

export function createBriefingFeedback({ api, busy, showError, link }) {
  return async function briefingFeedback(result, onChange) {
    const status = await api("/v1/memory/status");
    if (!status.enabled) return null;
    const base = "/v1/briefings/" + encodeURIComponent(result.briefing_id) + "/feedback";
    const company = result.input.company.name;
    const disclosure = el("details", "report-fold reader-action");
    disclosure.append(el("summary", null, "Feedback festhalten"));
    const box = el("section", "briefing-feedback");
    disclosure.append(box);
    box.id = "briefing-feedback";
    box.setAttribute("aria-labelledby", "briefing-feedback-title");
    const title = el("h2", null, "Feedback festhalten");
    title.id = "briefing-feedback-title";
    append(box, title, el("p", "muted", "Korrigiere eine Aussage oder halte fest, was im Gespräch herauskam. Gemerkte Hinweise fließen in deine nächsten Vorbereitungen für " + company + " ein."));
    const saved = el("div", "feedback-history");
    saved.setAttribute("aria-live", "polite");
    const errors = el("div");
    let items = [];
    function renderItems() {
      saved.replaceChildren();
      onChange?.(items);
      if (!items.length) return;
      saved.append(el("h3", null, "Deine Rückmeldungen zu diesem Briefing"));
      items.forEach(item => {
        const row = el("article", "feedback-entry");
        const meta = el("div", "feedback-meta");
        append(meta, tag(kinds[item.kind] || item.kind), el("small", null, date(item.created_at)));
        append(row, meta, el("p", "feedback-text", item.text));
        if (item.reference) row.append(el("blockquote", "feedback-reference", item.reference));
        if (item.remembered) {
          append(row, el("p", "feedback-effect", "Für deine nächsten Vorbereitungen zu " + company + " gemerkt."), link("Hinweis öffnen", "/knowledge/" + item.context_id, "text-button"));
        } else {
          row.append(el("p", "small muted", "Hier festgehalten. Wird noch nicht für weitere Vorbereitungen verwendet."));
          if (editable && item.automatic_reuse !== false) {
            const remember = button("Für nächste Vorbereitung merken", () => busy(remember, async () => {
              const updated = await api(base + "/" + encodeURIComponent(item.id) + "/remember", { method: "POST" });
              items = items.map(old => old.id === updated.id ? updated : old);
              renderItems();
              notify("Hinweis für die nächste Vorbereitung gemerkt.");
            }), "button secondary");
            row.append(remember);
          } else if (editable) {
            row.append(link("Im gespeicherten Wissen prüfen", "/knowledge/" + item.context_id, "text-button"));
          }
        }
        saved.append(row);
      });
    }
    let editable = true;
    try {
      const history = await api(base);
      items = history.items;
      editable = history.editable;
    } catch (error) {
      showError(error, errors);
    }
    if (!editable) {
      append(box, saved, errors);
      renderItems();
      return { element: disclosure };
    }
    const form = el("form", "briefing-feedback-form");
    const kind = selectField("Was möchtest du festhalten?", "briefing-feedback-kind", kinds, "correction");
    const text = field(prompts.correction[0], "briefing-feedback-text", "", { area: true, required: true, max: 1600, placeholder: prompts.correction[1] });
    const reference = field("Aussage aus dem Briefing", "briefing-feedback-reference", "", { area: true, max: 300, optional: true });
    const referenceDetails = el("details", "feedback-reference-details");
    append(referenceDetails, el("summary", null, "Bezug im Briefing ergänzen"), reference.wrap);
    const updatePrompt = () => {
      text.wrap.querySelector("label").textContent = prompts[kind.input.value][0];
      text.input.placeholder = prompts[kind.input.value][1];
    };
    kind.input.addEventListener("change", updatePrompt);
    const actions = el("div", "actions feedback-actions");
    const remember = button("Speichern und merken", null, "button", "submit");
    const note = button("Nur hier festhalten", null, "button secondary", "submit");
    const effect = el("p", "small muted", "Mit „Speichern und merken“ verwendest du den Text als eigenen Hinweis für weitere Gespräche mit " + company + ". Du kannst ihn unter „Wissen & Learnings“ bearbeiten.");
    append(actions, remember, note);
    append(form, kind.wrap, text.wrap, referenceDetails, actions, effect);
    let submitting = false;
    let pending = null;
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (submitting || !form.reportValidity()) return;
      if (!text.input.value.trim()) { text.input.focus(); return; }
      const value = { kind: kind.input.value, text: text.input.value.trim(), reference: reference.input.value.trim(), remember: event.submitter !== note };
      const key = JSON.stringify(value);
      if (pending?.key !== key) pending = { key, id: uid() };
      submitting = true;
      note.disabled = true;
      busy(remember, async () => {
        try {
          const item = await api(base, { method: "POST", body: { ...value, submission_id: pending.id } });
          items = [item, ...items.filter(old => old.id !== item.id)];
          renderItems();
          text.input.value = "";
          reference.input.value = "";
          referenceDetails.open = false;
          errors.replaceChildren();
          pending = null;
          notify(item.remembered ? "Gespeichert. Der Hinweis wird bei deiner nächsten Vorbereitung berücksichtigt." : "Rückmeldung zu diesem Briefing gespeichert.");
        } finally {
          submitting = false;
          note.disabled = false;
        }
      });
    });
    append(box, form, errors, saved);
    renderItems();
    return {
      element: disclosure,
      correct(claim) {
        kind.input.value = "correction";
        updatePrompt();
        reference.input.value = claim.slice(0, 300);
        referenceDetails.open = true;
        disclosure.open = true;
        box.scrollIntoView({ behavior: "instant", block: "start" });
        text.input.focus({ preventScroll: true });
      },
    };
  };
}
