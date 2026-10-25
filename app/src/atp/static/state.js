import { el } from "./dom.js?v=focused-questions-1";

export const config = JSON.parse(document.getElementById("app-config").textContent);
export const main = document.getElementById("main");
export const state = {
  input: null,
  draftId: null,
  warnings: [],
  people: [],
  mode: "form",
  authenticated: false,
  path: location.pathname,
  poll: null,
  run: null,
  briefing: null,
  retryMeeting: null,
  idempotency: null,
};
export const types = {
  setup: "Setup-Meeting",
  audit_consultation: "Audit-Beratungsgespräch",
  enablement_consultation: "Enablement-Beratungsgespräch",
  project_review: "Projektbesprechung",
};
export const relationships = {
  new_contact: "Neukontakt",
  existing_customer: "Bestandskunde",
  ongoing_project: "Laufendes Projekt",
  unknown: "Beziehung unbekannt",
};
export const relationshipContexts = {
  unspecified: "Noch nicht eingeordnet",
  existing_obligation: "Bestehende Verpflichtung",
  new_sales_inquiry: "Neue Vertriebsanfrage",
};
export const statuses = {
  queued: "In Warteschlange",
  researching: "Recherche läuft",
  evaluating: "Belege und ICP prüfen",
  generating: "Briefing erstellen",
  exporting: "PDF erstellen",
  needs_clarification: "Rückfrage offen",
  completed: "Erstellung abgeschlossen",
  partial: "Teilergebnis",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
  validating: "Eingabe prüfen",
};
export const icpLabels = {
  matching: "ICP passend",
  deviating: "ICP abweichend",
  unclear: "ICP ungeklärt",
};
export const claimLabels = {
  fact: "Belegte Tatsache",
  user_confirmed: "Bestätigte Eingabe",
  hypothesis: "Hypothese",
  unknown: "Unbekannt",
};
export const profileLabels = {
  confirmed: "Zuordnung bestätigt",
  ambiguous: "Zuordnung mehrdeutig",
  unresolved: "Zuordnung ungeklärt",
};
export const fieldLabels = {
  "company.name": "Firmenname",
  "company.website": "Website",
  "company.entity_hint": "Angefragte Unternehmenseinheit",
  people: "Ansprechpartner",
  meeting_type: "Meeting-Anlass",
  relationship: "Kundenbeziehung",
  relationship_context: "Anlass beim Bestandskunden",
  goal: "Gesprächsziel",
  exception_reason: "Ausnahmebegründung",
};
export const errorMessages = {
  BUDGET_EXHAUSTED:
    "Die verfügbare Verarbeitungszeit oder das Recherchebudget wurde erreicht. Der gespeicherte Stand bleibt erhalten.",
  PROCESSING_FAILED:
    "Die Vorbereitung konnte nicht abgeschlossen werden. Du kannst die gespeicherten Angaben in der Historie erneut verwenden.",
  PROVIDER_INSUFFICIENT_QUOTA:
    "Die Recherche wurde gestoppt, weil das API-Guthaben aufgebraucht oder das Abrechnungslimit erreicht ist. Bitte den API-Zugang prüfen und die Vorbereitung anschließend erneut starten. Der gespeicherte Stand bleibt erhalten.",
  PDF_EXPORT_FAILED:
    "Das Briefing ist gespeichert. Bitte den PDF-Export im Briefing erneut starten.",
};
export const decisions = [
  "Gespräch wahrnehmen",
  "Vorab klären",
  "Ausnahme prüfen",
  "Absage oder Weiterleitung empfehlen",
];
export const uid = () => crypto.randomUUID();

export function resetInput() {
  state.input = null;
  state.draftId = null;
  state.warnings = [];
  state.retryMeeting = null;
  state.idempotency = null;
  state.mode = "form";
  state.freeText = "";
}

export function createNavigation({ route, resetInput }) {
const backNavigation = document.getElementById("back-navigation");
function setBack(target = null, action = null) {
  backNavigation.replaceChildren();
  backNavigation.hidden = !target;
  if (!target) return;
  const a = el("a", "back-link", "← Zurück");
  a.href = target;
  a.addEventListener("click", e => {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    if (action) action(); else navigate(target);
  });
  backNavigation.append(a);
}

function navigate(path) {
  history.pushState({}, "", path);
  route();
  window.scrollTo({ top: 0, behavior: "instant" });
}
function link(text, href, cls = "") {
  const a = el("a", cls, text);
  a.href = href;
  if (href.startsWith("/")) {
    a.addEventListener("click", (e) => {
      if (!e.ctrlKey && !e.metaKey && !e.shiftKey) {
        e.preventDefault();
        if (href === "/") resetInput();
        navigate(href);
      }
    });
  }
  return a;
}

return { navigate, link, setBack };
}
