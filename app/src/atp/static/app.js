import {
  el,
  append,
  button,
  textValue,
  date,
  dateInput,
  berlinToISO,
  tag,
  notice,
  safeUrl,
  externalLink,
  notify,
  heading,
  field,
  selectField,
  check,
  previewRow,
  reportSection,
  bulletList,
} from "./dom.js?v=focused-questions-1";
import {
  config,
  main,
  state,
  types,
  relationships,
  relationshipContexts,
  statuses,
  icpLabels,
  claimLabels,
  profileLabels,
  fieldLabels,
  errorMessages,
  decisions,
  uid,
  resetInput,
  createNavigation,
} from "./state.js?v=person-research-1";
import { createApi } from "./api.js?v=auth-navigation-1";
import { loginDestination } from "./auth-navigation.js?v=auth-navigation-1";
import { createHistoryTiming, historyPhase, patchHistoryCard } from "./history.js?v=focused-questions-1";
import { createMemoryViews } from "./memory.js?v=person-research-1";
import { createBriefingFeedback } from "./briefing-feedback.js?v=briefing-reader-1";
const { navigate, link, setBack } = createNavigation({ route, resetInput });
const { api, showError, busy } = createApi({ main, navigate, onAuthentication(value) { state.authenticated = value; authLabel(); } });
const { memoryEvidence, renderKnowledge, renderMemoryTask, mountMemorySelection } = createMemoryViews({ api, showError, busy, navigate, link, route, setBack });
const briefingFeedback = createBriefingFeedback({ api, busy, showError, link });
"use strict";
// All external strings enter the DOM through textContent or form values.
function authLabel() {
  document.getElementById("auth-button").textContent = state.authenticated
    ? "Abmelden"
    : "Anmelden";
}
window.addEventListener("popstate", route);
document.querySelectorAll("[data-nav]").forEach((a) =>
  a.addEventListener("click", (e) => {
    if (!e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      const href = a.getAttribute("href");
      if (href === "/") resetInput();
      navigate(href);
    }
  }),
);
document.getElementById("auth-button").addEventListener("click", async () => {
  if (!state.authenticated) {
    navigate("/login");
    return;
  }
  try {
    await api("/v1/auth/logout", { method: "POST" });
    state.authenticated = false;
    authLabel();
    navigate("/login");
    notify("Du bist abgemeldet.");
  } catch (e) {
    notify(e.message, true);
  }
});
const banner = document.getElementById("environment-banner");
if (config.fixture_mode) {
  const b = el("div", "environment");
  append(
    b,
    el("strong", null, "Testmodus · "),
    "Kontrollierte synthetische Quellen. Die Ergebnisse sind keine reale Unternehmensrecherche.",
  );
  banner.append(b);
}
if (!config.configured) {
  const b = el("div", "environment");
  append(
    b,
    el("strong", null, "Einrichtung erforderlich · "),
    "Vorbereitungen können erst nach der Einrichtung gespeichert und recherchiert werden.",
  );
  const detail = el("details");
  append(detail, el("summary", null, "Fehlende Einrichtung anzeigen"));
  const ul = el("ul");
  (config.setup_issues || []).forEach((x) => ul.append(el("li", null, x)));
  detail.append(ul);
  b.append(detail);
  banner.append(b);
}
function steps(active) {
  const n = el("ol", "steps");
  n.setAttribute("aria-label", "Fortschritt der Vorbereitung");
  ["Eingabe", "Prüfung", "Recherche", "Briefing"].forEach((text, i) => {
    const item = append(
      el("li", i === active ? "current" : ""),
      el("b", null, String(i + 1).padStart(2, "0")),
      text,
    );
    if (i === active) item.setAttribute("aria-current", "step");
    n.append(item);
  });
  return n;
}
function preparationSurface(active, title, subtitle) {
  const page = el("div", "preparation");
  const surface = el("div", "surface preparation-surface");
  append(page, heading(active ? "Eingabevorschau" : "Neue Vorbereitung", title, subtitle), steps(active), surface);
  main.append(page);
  return surface;
}
function inputDefaults() {
  return {
    schema_version: 1,
    company: { name: "", website: "", entity_hint: "" },
    people: [
      {
        participant_id: uid(),
        name: "",
        role_hint: "",
        profile_url: "",
        public_identifiers_confirmed: false,
      },
    ],
    meeting_type: null,
    relationship: "unknown",
    relationship_context: "unspecified",
    goal: "",
    internal_context: "",
    scheduled_at: null,
    confirmed: false,
    public_identifiers_confirmed: false,
    exception_reason: "",
  };
}
function renderInput() {
  const current = state.input || inputDefaults();
  state.input = current;
  const surface = preparationSurface(
    0,
    "Gespräch vorbereiten",
    "Die wichtigsten Angaben reichen für den nächsten Schritt.",
  );
  const tabs = el("div", "tabs");
  tabs.setAttribute("role", "tablist");
  const formTab = button(
    "Formular",
    () => {
      capture();
      state.mode = "form";
      route();
    },
    state.mode === "form" ? "selected" : "",
  );
  const freeTab = button(
    "Freitext",
    () => {
      capture();
      state.mode = "text";
      route();
    },
    state.mode === "text" ? "selected" : "",
  );
  [formTab, freeTab].forEach((b, i) => {
    b.setAttribute("role", "tab");
    b.setAttribute(
      "aria-selected",
      String((i === 0) === (state.mode === "form")),
    );
  });
  append(tabs, formTab, freeTab);
  surface.append(tabs);
  let capture = () => {};
  if (state.mode === "text") {
    const form = el("form");
    const f = field("Was steht an?", "freetext", state.freeText || "", {
      area: true,
      max: 20000,
      required: true,
      placeholder:
        "Musterwerk GmbH, https://musterwerk.example. Setup-Meeting mit Lea Sommer und Tobias Kern. Neukontakt. Ziel: Einsatzmöglichkeiten für KI verstehen.",
      hint: "Bis zu 20.000 Zeichen. Der nächste Schritt zeigt nur eine bearbeitbare Vorschau.",
    });
    setBack("/", () => { capture(); state.mode = "form"; route(); });
    f.input.rows = 9;
    append(form, f.wrap);
    capture = () => {
      state.freeText = f.input.value;
    };
    append(
      form,
      notice(
        "Nur die bestätigten öffentlichen Firmen- und Personenangaben werden für die Websuche verwendet. Interne Notizen kannst du in der Vorschau getrennt ergänzen.",
      ),
    );
    const b = button(
      "Vorschau aus Freitext erstellen",
      null,
      "button",
      "submit",
    );
    form.append(b);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      capture();
      await busy(b, async () => {
        const data = await api("/v1/input/parse", {
          method: "POST",
          body: JSON.stringify({ text: f.input.value }),
        });
        state.input = { ...inputDefaults(), ...data.preview };
        state.draftId = data.draft_id;
        state.warnings = data.warnings || [];
        state.mode = "form";
        state.idempotency = null;
        route();
        notify(
          "Vorschlag übernommen. Bitte ergänzen, prüfen und die Vorschau öffnen.",
        );
      });
    });
    surface.append(form);
    return;
  }
  const form = el("form");
  form.id = "meeting-form";
  const inputs = {};
  const personInputs = [];
  const section = (title) => {
    const n = el("section", "form-section");
    append(n, el("h2", null, title));
    form.append(n);
    return n;
  };
  const optional = el("section", "optional-sections");
  append(
    optional,
    el("h2", null, "Optional ergänzen"),
    el("p", "muted small", "Ergänze, was du schon weißt. Alles andere kannst du offenlassen."),
  );
  const disclosures = [];
  function disclosure(title, description, hasContent) {
    const node = el("details", "optional-section");
    const status = el("span", "optional-status", "Angaben ergänzt");
    const summary = append(
      el("summary"),
      append(
        el("span", "optional-label"),
        el("span", "optional-title", title),
        el("span", "optional-description", description),
      ),
      status,
    );
    const body = el("div", "optional-content");
    append(node, summary, body);
    optional.append(node);
    disclosures.push({ node, status, hasContent });
    return { node, body };
  }
  const refreshDisclosures = (initial = false) => {
    disclosures.forEach(({ node, status, hasContent }) => {
      const filled = Boolean(hasContent());
      status.hidden = !filled;
      if (initial && filled) node.open = true;
    });
  };
  const profiles = disclosure(
    "Rollen und Profile",
    "Funktion und öffentliches Profil der Ansprechpartner",
    () => personInputs.some(p => p.role_hint.value.trim() || p.profile_url.value.trim() || p.public_identifiers_confirmed.checked),
  );
  const meetingDetails = disclosure(
    "Gesprächsdetails",
    "Kundenbeziehung, Gesprächsziel und Termin",
    () => inputs.relationship.value !== "unknown" || inputs.goal.value.trim() || inputs.scheduled_at.value,
  );
  const extra = disclosure(
    "Weitere Informationen",
    "Unternehmenseinheit, interne Notizen und Ausnahmefall",
    () => inputs.entity_hint.value.trim() || inputs.internal_context.value.trim() || inputs.exception_reason.value.trim(),
  );
  if (state.warnings.length) {
    const box = notice("Bitte prüfen");
    const ul = el("ul", "plain-list");
    state.warnings.forEach((w) =>
      ul.append(el("li", null, typeof w === "string" ? w : JSON.stringify(w))),
    );
    append(box, ul);
    form.append(box);
  }
  const company = section("Unternehmen");
  const companyGrid = el("div", "field-grid");
  [
    [
      "Firmenname",
      "company_name",
      current.company.name,
      { required: true, max: 500, placeholder: "Musterwerk GmbH" },
    ],
    [
      "Website",
      "company_website",
      current.company.website,
      {
        required: true,
        type: "url",
        max: 2048,
        placeholder: "https://unternehmen.de",
      },
    ],
  ].forEach(([l, n, v, o]) => {
    const f = field(l, n, v, o);
    inputs[n] = f.input;
    companyGrid.append(f.wrap);
  });
  company.append(companyGrid);
  let f = field(
    "Gemeinte Unternehmenseinheit",
    "entity_hint",
    current.company.entity_hint,
    {
      optional: true,
      max: 500,
      hint: "Zum Beispiel eine Tochtergesellschaft oder ein bestimmter Standort.",
    },
  );
  inputs.entity_hint = f.input;
  extra.body.append(f.wrap);
  const people = section("Ansprechpartner");
  const personList = el("div");
  people.append(personList);
  function updatePeople() {
    personInputs.forEach((p, i) => {
      const label = p.name.value.trim() || "Person " + (i + 1);
      p.title.textContent = label;
      p.remove.hidden = personInputs.length === 1;
      p.remove.setAttribute("aria-label", label + " entfernen");
    });
    add.disabled = personInputs.length >= 10;
  }
  function addPerson(person) {
    const box = el("div", "person-editor");
    const profile = el("section", "person-details");
    const title = el("h3");
    profile.append(title);
    const row = { participant_id: person.participant_id || uid(), box, profile, title };
    const remove = button(
      "Entfernen",
      () => {
        if (personInputs.length === 1) {
          notify("Mindestens eine Person angeben.");
          return;
        }
        personInputs.splice(personInputs.indexOf(row), 1);
        box.remove();
        profile.remove();
        updatePeople();
        // A partially entered date must not block adding/removing a person.
        try { capture(); } catch {}
        refreshDisclosures();
        personInputs[0].name.focus();
      },
      "text-button",
    );
    row.remove = remove;
    [
      ["Name", "name", 300],
      ["Rolle / Funktion", "role_hint", 500],
    ].forEach(([label, key, max]) => {
      const f = field(label, uid(), person[key], {
        required: key === "name",
        optional: key !== "name",
        max,
      });
      row[key] = f.input;
      (key === "name" ? box : profile).append(f.wrap);
    });
    row.name.addEventListener("input", updatePeople);
    box.append(remove);
    const p = field(
      "Öffentliches berufliches Profil",
      "person-" + uid(),
      person.profile_url,
      { type: "url", optional: true, max: 2048, placeholder: "https://…" },
    );
    row.profile_url = p.input;
    profile.append(p.wrap);
    const c = check(
      "Rolle und Profil-URL sind öffentlich und dürfen zur Identitätsprüfung gesucht werden.",
      person.public_identifiers_confirmed,
    );
    row.public_identifiers_confirmed = c.input;
    profile.append(c.wrap);
    personInputs.push(row);
    personList.append(box);
    profiles.body.append(profile);
  }
  const add = button(
    "+ Weitere Person",
    () => {
      if (personInputs.length >= 10) return;
      addPerson({});
      updatePeople();
      try { capture(); } catch {}
      personInputs.at(-1).name.focus();
    },
    "button secondary small",
  );
  (current.people?.length ? current.people : [{}]).forEach(addPerson);
  updatePeople();
  append(
    people,
    add,
    el(
      "p",
      "small muted",
      "Bis zu 10 Personen. Jedes Profil bleibt getrennt zugeordnet.",
    ),
  );
  const meeting = section("Gespräch");
  f = selectField(
    "Meeting-Anlass",
    "meeting_type",
    types,
    current.meeting_type,
    "Bitte auswählen",
  );
  inputs.meeting_type = f.input;
  meeting.append(f.wrap);
  f = selectField(
    "Kundenbeziehung",
    "relationship",
    relationships,
    current.relationship,
  );
  inputs.relationship = f.input;
  meetingDetails.body.append(f.wrap);
  const relationshipDetail = selectField(
    "Anlass beim Bestandskunden",
    "relationship_context",
    relationshipContexts,
    current.relationship_context || "unspecified",
  );
  inputs.relationship_context = relationshipDetail.input;
  relationshipDetail.wrap.hidden = current.relationship !== "existing_customer";
  inputs.relationship.addEventListener("change", () => {
    relationshipDetail.wrap.hidden =
      inputs.relationship.value !== "existing_customer";
  });
  meetingDetails.body.append(relationshipDetail.wrap);
  f = field("Gesprächsziel", "goal", current.goal, {
    area: true,
    max: 3000,
    optional: true,
    placeholder: "Was soll nach dem Gespräch geklärt sein?",
  });
  inputs.goal = f.input;
  meetingDetails.body.append(f.wrap);
  f = field("Termin", "scheduled_at", dateInput(current.scheduled_at), {
    optional: true,
    type: "datetime-local",
    hint: "Anzeige und Eingabe in Europe/Berlin.",
  });
  inputs.scheduled_at = f.input;
  meetingDetails.body.append(f.wrap);
  f = field("Interne Notizen", "internal_context", current.internal_context, {
    area: true,
    max: 10000,
    optional: true,
    hint: "Bis zu 10.000 Zeichen. Wird nur für die Briefing-Erstellung verwendet und nicht an die öffentliche Suche übergeben.",
  });
  inputs.internal_context = f.input;
  extra.body.append(f.wrap);
  if (config.memory_enabled) {
    const knowledge = disclosure(
      "Gespeichertes Wissen",
      "Bis zu zwei Unternehmens- oder Projektkontexte auswählen",
      () => state.input?.context_ids?.length,
    );
    const loading = el("p", "small muted", "Gespeichertes Wissen wird geladen …");
    knowledge.body.append(loading);
    mountMemorySelection(knowledge.body, current).then(() => {
      loading.remove();
      if (!knowledge.body.childElementCount) {
        knowledge.body.append(el("p", "muted small", "Gespeichertes Wissen ist derzeit nicht verfügbar."));
      }
      if (knowledge.body.querySelector('[role="alert"]')) knowledge.node.open = true;
      if (state.input?.context_ids?.length) knowledge.node.open = true;
      refreshDisclosures();
    });
  }
  f = field(
    "Begründeter Ausnahmefall",
    "exception_reason",
    current.exception_reason,
    {
      area: true,
      max: 2000,
      optional: true,
      hint: "Wenn eine bekannte Zielgruppenabweichung bewusst geprüft werden soll.",
    },
  );
  inputs.exception_reason = f.input;
  extra.body.append(f.wrap);
  capture = () => {
    state.input = {
      ...current,
      company: {
        name: inputs.company_name.value.trim(),
        website: inputs.company_website.value.trim(),
        entity_hint: inputs.entity_hint.value.trim(),
      },
      people: personInputs.map((p) => ({
        participant_id: p.participant_id,
        name: p.name.value.trim(),
        role_hint: p.role_hint.value.trim(),
        profile_url: p.profile_url.value.trim(),
        public_identifiers_confirmed: p.public_identifiers_confirmed.checked,
      })),
      meeting_type: inputs.meeting_type.value || null,
      relationship: inputs.relationship.value || "unknown",
      relationship_context:
        inputs.relationship.value === "existing_customer"
          ? inputs.relationship_context.value
          : "unspecified",
      goal: inputs.goal.value,
      internal_context: inputs.internal_context.value,
      exception_reason: inputs.exception_reason.value,
      scheduled_at: inputs.scheduled_at.value
        ? berlinToISO(inputs.scheduled_at.value)
        : null,
      confirmed: false,
    };
    state.idempotency = null;
  };
  form.append(optional);
  refreshDisclosures(true);
  form.addEventListener("change", () => {
    try { capture(); } catch {}
    refreshDisclosures();
  });
  form.addEventListener("invalid", (event) => {
    const details = event.target.closest("details");
    if (details) details.open = true;
  }, true);
  const actions = el("div", "actions");
  append(
    actions,
    button("Angaben prüfen →", null, "button", "submit"),
    el("span", "hint", "Die Recherche beginnt nach deiner Bestätigung."),
  );
  form.append(actions);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    try {
      capture();
      renderPreview();
      window.scrollTo({ top: 0, behavior: "instant" });
    } catch (error) {
      notify(error.message, true);
      if (inputs.scheduled_at.value && error.message.includes("Zeitumstellung")) {
        meetingDetails.node.open = true;
        inputs.scheduled_at.focus();
      }
    }
  });
  form.addEventListener("input", () => {
    try {
      capture();
    } catch {}
    refreshDisclosures();
  });
  surface.append(form);
}
function renderPreview() {
  setBack("/", () => route());
  main.replaceChildren();
  const surface = preparationSurface(
    1,
    "Einmal prüfen. Dann recherchieren.",
    "Diese Angaben werden als bestätigter Stand gespeichert.",
  );
  const input = state.input;
  const dl = el("dl");
  previewRow(dl, "Unternehmen", input.company.name);
  previewRow(dl, "Website", input.company.website);
  if (input.company.entity_hint)
    previewRow(dl, "Unternehmenseinheit", input.company.entity_hint);
  input.people.forEach((p, i) =>
    previewRow(
      dl,
      "Person " + (i + 1),
      [
        p.name,
        p.role_hint,
        p.profile_url,
        p.public_identifiers_confirmed
          ? "Öffentliche Rollen-/Profilangaben freigegeben"
          : "Keine Rollen-/Profilangaben für die Suche freigegeben",
      ]
        .filter(Boolean)
        .join("\n"),
    ),
  );
  previewRow(dl, "Anlass", types[input.meeting_type]);
  previewRow(dl, "Beziehung", relationships[input.relationship]);
  if (input.relationship === "existing_customer")
    previewRow(
      dl,
      "Bestandskunden-Anlass",
      relationshipContexts[input.relationship_context || "unspecified"],
    );
  previewRow(dl, "Gesprächsziel", input.goal);
  previewRow(dl, "Termin", date(input.scheduled_at));
  if (input.internal_context)
    previewRow(dl, "Interne Notizen", input.internal_context);
  if (input.exception_reason)
    previewRow(dl, "Ausnahmegrund", input.exception_reason);
  surface.append(dl);
  if (config.memory_enabled && input.context_ids?.length) {
    const knowledge = el("div");
    surface.append(knowledge);
    api(`/v1/memory/preview?context_ids=${input.context_ids.join(",")}&meeting_type=${input.meeting_type}`).then(m => knowledge.replaceChildren(memoryEvidence(m))).catch(e => showError(e, knowledge));
  }
  const c = check(
    "Ich habe die Angaben geprüft. Firmenname, Website und Personennamen dürfen als öffentliche Identifikatoren für die Recherche verwendet werden.",
    false,
  );
  surface.append(c.wrap);
  const actions = el("div", "actions");
  const start = button(
    state.retryMeeting ? "Neue Version recherchieren" : "Briefing vorbereiten",
    () =>
      busy(start, async () => {
        if (!c.input.checked) {
          notify(
            "Bitte die geprüften Angaben und öffentlichen Identifikatoren bestätigen.",
            true,
          );
          c.input.focus();
          return;
        }
        const data = {
          ...state.input,
          confirmed: true,
          public_identifiers_confirmed: true,
        };
        state.input = data;
        state.idempotency = state.idempotency || uid();
        const url = state.retryMeeting
          ? "/v1/meetings/" + encodeURIComponent(state.retryMeeting) + "/runs"
          : "/v1/preparations";
        const response = await api(url, {
          method: "POST",
          body: JSON.stringify(data),
          headers: {
            "Idempotency-Key": state.idempotency,
            ...(state.draftId ? { "X-Draft-ID": state.draftId } : {}),
          },
        });
        state.retryMeeting = null;
        state.draftId = null;
        state.warnings = [];
        navigate("/runs/" + response.run_id);
      }),
  );
  start.disabled = !c.input.checked;
  c.input.addEventListener("change", () => {
    start.disabled = !c.input.checked;
  });
  append(
    actions,
    button("← Angaben bearbeiten", () => route(), "button secondary"),
    start,
  );
  surface.append(actions);
}
async function renderRun(id) {
  const container = el("div", "status-layout");
  append(main, container);
  const intro = heading(
    "Vorbereitung",
    "Dein Briefing entsteht.",
    "Du kannst dieses Fenster schließen und das Briefing später in der Historie öffnen.",
  );
  container.append(intro);
  const status = el("div", "surface");
  container.append(status);
  let failures = 0;
  async function update() {
    if (state.path !== "/runs/" + id) return;
    try {
      const run = await api("/v1/runs/" + encodeURIComponent(id));
      if (state.path !== "/runs/" + id) return;
      state.run = run;
      const userQuestions = (run.clarifications || []).filter((q) => !q.answer && q.code !== "K06");
      const needsAnswer = run.status === "needs_clarification" && userQuestions.length > 0;
      const finishingResearch = run.status === "needs_clarification" && !needsAnswer;
      intro.querySelector("h1").textContent = needsAnswer
        ? userQuestions.length === 1 ? "Eine Rückfrage zu deinem Termin." : "Rückfragen zu deinem Termin."
        : ["failed", "cancelled"].includes(run.status) ? "Die Vorbereitung wurde gestoppt." : "Dein Briefing entsteht.";
      intro.querySelector("p").hidden = needsAnswer || ["failed", "cancelled"].includes(run.status);
      failures = 0;
      status.replaceChildren();
      const phase = run.phase || run.status;
      const terminal = ["completed", "partial", "failed", "cancelled"].includes(
        run.status,
      );
      const head = el("div", "status-head");
      append(
        head,
        el(
          "span",
          "status-orb" +
            (!terminal && run.status !== "needs_clarification" ? " pulse" : ""),
        ),
        append(
          el("div"),
          el("h2", null, needsAnswer ? "Deine Rückfragen" : finishingResearch ? "Wir stellen dein Briefing fertig." : statuses[run.status] || "In Bearbeitung"),
          ...(!terminal && !needsAnswer && !finishingResearch && phase !== run.status ? [el("p", "muted", statuses[phase] || "In Bearbeitung")] : []),
        ),
      );
      if (!needsAnswer) status.append(head);
      if (needsAnswer) {
        const qs = userQuestions;
        const form = el("form");
        const answers = {};
        const correctInput = () => {
          if (!run.input) {
            notify(
              "Die gespeicherte Eingabe ist gerade nicht verfügbar. Bitte den Status neu laden.",
              true,
            );
            return;
          }
          state.input = structuredClone(run.input);
          state.input.confirmed = false;
          state.retryMeeting = run.meeting_id;
          state.draftId = null;
          state.warnings = [];
          state.idempotency = null;
          state.mode = "form";
          navigate("/");
          notify(
            "Gespeicherte Angaben übernommen. Korrigiere sie und bestätige die Vorschau für eine neue Version.",
          );
        };
        qs.forEach((q) => {
          const box = el("section", "clarification");
          append(
            box,
            el("h3", null, q.question || q.reason),
            ...(q.question && ["K02", "K03"].includes(q.code) ? [el("p", "muted", q.reason)] : []),
          );
          if (q.handling === "skippable_profile")
            append(
              box,
              el(
                "p",
                "small",
                "Du kannst dieses Profil als ungeklärt überspringen. Die übrige Vorbereitung bleibt nutzbar.",
              ),
            );
          const control = clarificationControl(q);
          box.append(control.wrap);
          answers[q.question_id] = control;
          if (
            ["K02", "K03"].includes(q.code) &&
            (q.options || []).some((option) => /korrigieren/i.test(option))
          ) {
            append(
              box,
              button(
                "Eingabe korrigieren",
                correctInput,
                "button secondary small",
              ),
              el(
                "p",
                "small muted",
                "Die bestätigte Eingabe wird vorausgefüllt. Eine korrigierte Recherche erzeugt eine eigene neue Version.",
              ),
            );
          }
          form.append(box);
        });
        const b = button(
          "Übernehmen und fortsetzen",
          null,
          "button",
          "submit",
        );
        form.append(b);
        form.addEventListener("submit", (e) => {
          e.preventDefault();
          const supplied = Object.fromEntries(Object.entries(answers)
            .map(([key, control]) => [key, control.read()])
            .filter(([, value]) => value !== null));
          if (!Object.keys(supplied).length) {
            notify("Bitte mindestens eine Antwort ergänzen oder ausdrücklich ungeklärt fortfahren wählen.", true);
            return;
          }
          const correctionSelected = qs.some(
            (q) =>
              ["K02", "K03"].includes(q.code) &&
              (q.options || []).includes(
                supplied[q.question_id]?.value,
              ) &&
              /korrigieren/i.test(supplied[q.question_id]?.value || ""),
          );
          if (correctionSelected) {
            correctInput();
            return;
          }
          document.getElementById("notification").hidden = true;
          busy(b, async () => {
            await api("/v1/runs/" + encodeURIComponent(id) + "/answers", {
              method: "POST",
              body: JSON.stringify({
                expected_version: run.version,
                answers: supplied,
              }),
            });
            await update();
          });
        });
        status.append(form);
      }
      if (run.briefing_id) {
        const actions = el("div", "actions");
        append(
          actions,
          link("Briefing öffnen →", "/briefings/" + run.briefing_id, "button"),
          link("Zur Historie", "/history", "button secondary"),
        );
        status.append(actions);
        if (run.pdf_status === "failed")
          status.append(
            notice(
              "Das Briefing ist gespeichert. Der PDF-Export konnte nicht abgeschlossen werden und kann im Briefing erneut gestartet werden.",
            ),
          );
        if (["completed", "partial"].includes(run.status)) return;
      }
      if (run.error)
        status.append(
          notice(
            typeof run.error === "string"
              ? errorMessages[run.error] ||
                  "Ein Verarbeitungsschritt konnte nicht abgeschlossen werden. Der gespeicherte Stand bleibt erhalten."
              : run.error.message ||
                  "Ein Verarbeitungsschritt konnte nicht abgeschlossen werden.",
            "error",
          ),
        );
      if (["failed", "cancelled"].includes(run.status)) {
        append(
          status,
          el(
            "p",
            null,
            "Deine bestätigte Eingabe bleibt gespeichert. Öffne die Historie für einen neuen Versuch.",
          ),
          link("Zur Historie", "/history", "button secondary"),
        );
        return;
      }
      if (!needsAnswer)
        state.poll = setTimeout(update, 1600);
    } catch (error) {
      if (state.path !== "/runs/" + id) return;
      if (error.status === 401) {
        showError(error, status);
        return;
      }
      if (error.status === 404) {
        status.replaceChildren();
        showError(error, status);
        return;
      }
      failures++;
      status.replaceChildren(
        notice(
          "Der Status ist gerade nicht erreichbar. Deine gespeicherte Vorbereitung bleibt erhalten.",
          "error",
        ),
      );
      if (failures < 5) state.poll = setTimeout(update, 4000);
      else
        status.append(
          button("Status erneut laden", update, "button secondary"),
        );
    }
  }
  await update();
}
function citations(ids, result) {
  const box = el("details", "citations source-peek");
  const unique = [...new Set(ids || [])];
  const found = unique.filter(id => result.sources.some(s => s.source_id === id));
  if (!found.length) return el("span");
  box.append(el("summary", null, found.length === 1 ? "Quelle ansehen" : found.length + " Quellen ansehen"));
  unique.forEach((id) => {
    const index = result.sources.findIndex((s) => s.source_id === id);
    if (index >= 0) {
      const a = el("a", null, result.sources[index].title);
      a.href = "#source-" + index;
      a.title = result.sources[index].title;
      box.append(a);
      a.addEventListener("click", (event) => navigateReportAnchor(event, a.hash));
    }
  });
  return box;
}
function claimSources(claim, result) {
  return [
    ...new Set([
      ...(claim.evidence_ids || []),
      ...(claim.fact_ids || []).flatMap(
        (id) => result.facts.find((f) => f.fact_id === id)?.evidence_ids || [],
      ),
    ]),
  ];
}
function claimNode(claim, result) {
  const n = el("div", "claim");
  append(
    n,
    ...(claim.kind === "fact" ? [] : [tag(claimLabels[claim.kind] || "Unbekannt", claim.kind)]),
    el("p", null, readerText(claim.text)),
    citations(claimSources(claim, result), result),
  );
  return n;
}
function readerText(text) {
  return (text || "").replace(/\s*\(Informationsstand unbekannt\)/g, "");
}
function foldReport(title, content, className = "report-fold") {
  const details = el("details", className);
  append(details, el("summary", null, title), content);
  return details;
}
function revealReportTarget(hash) {
  const target = document.getElementById(hash.replace(/^#/, ""));
  for (let node = target; node; node = node.parentElement) {
    if (node.tagName === "DETAILS") node.open = true;
  }
  return target;
}
function navigateReportAnchor(event, hash) {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  const target = revealReportTarget(hash);
  if (target) {
    history.pushState({}, "", hash);
    target.scrollIntoView({block: "start"});
  }
}
const overviewLabels = {
  company: "Unternehmen in 20 Sekunden",
  development: "Für dieses Gespräch relevant",
  people: "Dein Gegenüber",
  opener: "Dein Gesprächseinstieg",
  next_step: "Einordnung und nächster Schritt",
};
const qualityLabels = {
  sufficient: "Für den bestätigten Anlass ausreichend belegt",
  limited: "Mit Einschränkungen verwendbar",
  clarification_required: "Wesentliche Klärung offen",
  not_assessed: "Fachliche Verwendbarkeit nicht bewertet",
};
function qualityNode(result) {
  const quality = result.quality || {};
  const panel = el("details", "quality-panel report-fold");
  panel.setAttribute("aria-label", "Fachliche Verwendbarkeit");
  append(panel, el("summary", null, "Recherchehinweise · " + (qualityLabels[quality.state] || qualityLabels.not_assessed)));
  if (quality.reasons?.length) panel.append(bulletList([...new Set(quality.reasons)]));
  return panel;
}
function resolutionNotes(result) {
  return Object.entries(result.resolutions || {}).map(([key, value]) => {
    if (typeof value === "string") return key + ": " + value + " / Historische Antwort; Herkunft nicht dokumentiert.";
    if (!value || typeof value !== "object") return null;
    const label = value.question || fieldLabels[value.field] || value.field || key;
    const text = value.action === "leave_unknown"
      ? "Bewusst ungeklärt gelassen; keine Tatsachenbestätigung."
      : (value.value || "Antwort hinterlegt") + " / Bestätigte Nutzereingabe; kein Web-Beleg.";
    const provenance = [value.confirmed_by ? "Bestätigt von: " + value.confirmed_by : "",
      value.confirmed_at ? "Bestätigt am: " + date(value.confirmed_at) : ""].filter(Boolean).join(" / ");
    return label + ": " + text + (provenance ? " / " + provenance : "");
  }).filter(Boolean);
}
function clarificationControl(q) {
  const wrap = el("div", "clarification-control");
  const types = q.answer_types || ["provide", "select", "leave_unknown"];
  const options = types.includes("select") ? q.options || [] : [];
  const id = "answer-" + q.question_id;
  const canProvide = types.includes("provide");
  const canLeaveUnknown = q.code !== "K01" && q.allow_unknown !== false && types.includes("leave_unknown");
  const choice = el("select");
  choice.id = id + "-select";
  const placeholder = el("option", null, "Bitte auswählen …");
  placeholder.value = "";
  choice.append(placeholder);
  options.forEach((value) => {
    const option = el("option", null, value);
    option.value = value;
    choice.append(option);
  });
  // Empty value plus a marker avoids treating the custom-answer option as a
  // confirmed answer, even if a provider option contains the same label.
  const custom = el("option", null, "Eigene Antwort ergänzen …");
  custom.value = "";
  if (options.length && canProvide) choice.append(custom);
  const text = el("textarea");
  text.id = id + "-provide";
  text.maxLength = 3000;
  text.rows = 3;
  if (options.length) text.setAttribute("aria-label", "Eigene Antwort");
  const valueLabel = el("label", null, "Deine Antwort");
  valueLabel.htmlFor = options.length ? choice.id : text.id;
  const unknown = el("input");
  unknown.type = "checkbox";
  unknown.id = id + "-unknown";
  const unknownLabel = el("label", "check");
  unknownLabel.htmlFor = unknown.id;
  append(unknownLabel, unknown, el("span", null, "Weiß ich nicht – ohne diese Angabe fortfahren"));
  const hasCustom = () => options.length && choice.selectedOptions[0] === custom;
  const update = () => {
    choice.hidden = !options.length;
    text.hidden = options.length > 0 ? !hasCustom() : !canProvide;
    choice.disabled = text.disabled = canLeaveUnknown && unknown.checked;
  };
  choice.addEventListener("change", update);
  unknown.addEventListener("change", update);
  append(wrap, valueLabel, choice, text, ...(canLeaveUnknown ? [unknownLabel] : []));
  update();
  return {wrap, read: () => {
    if (canLeaveUnknown && unknown.checked) return {action: "leave_unknown", value: ""};
    const select = options.length > 0 && !hasCustom();
    const value = (select ? choice.value : text.value).trim();
    return value ? {action: select ? "select" : "provide", value} : null;
  }};
}
async function renderBriefing(id) {
  const loading = notice("Gespeichertes Briefing wird geladen …");
  main.append(loading);
  try {
    const r = await api("/v1/briefings/" + encodeURIComponent(id));
    if (state.path !== "/briefings/" + id) return;
    state.briefing = r;
    if (r.reader_view?.overview) r.overview = r.reader_view.overview;
    main.replaceChildren();
    const sheet = el("div", "reader-sheet");
    main.append(sheet);
    const intro = heading(
      "Gesprächsbriefing",
      r.input.company.name,
      types[r.input.meeting_type] + " · " + relationships[r.input.relationship],
    );
    intro.classList.add("briefing-intro");
    const tags = el("div", "tags");
    append(
      tags,
      ...(r.status === "partial" ? [tag("Teilergebnis", "partial")] : []),
      tag("Version " + r.result_version),
      tag(icpLabels[r.icp.status], r.icp.status),
    );
    if (r.fixture_mode) tags.append(tag("Synthetischer Testfall", "partial"));
    append(
      intro,
      tags,
      el(
        "p",
        "report-meta",
        "Erstellt: " +
          date(r.generated_at) +
          " · Meeting: " +
          date(r.input.scheduled_at),
      ),
    );
    if (r.input.relationship === "existing_customer")
      intro.append(
        el(
          "p",
          "report-meta",
          "Bestandskunden-Anlass: " +
            relationshipContexts[r.input.relationship_context || "unspecified"],
        ),
      );
    const actions = el("div", "actions");
    const htmlDownload = el("a", "button secondary", "HTML herunterladen");
    htmlDownload.href = "/v1/briefings/" + encodeURIComponent(r.briefing_id) + "/html";
    htmlDownload.download = "";
    actions.append(htmlDownload);
    if (
      r.pdf_status === "ready" ||
      r.pdf_status === "available" ||
      r.pdf_status === "completed" ||
      !r.pdf_status
    ) {
      const a = el("a", "button", "PDF herunterladen ↓");
      a.href = "/v1/briefings/" + encodeURIComponent(r.briefing_id) + "/pdf?compact=true";
      a.download = "";
      actions.append(a);
      const fullPdf = el("a", "button secondary", "PDF mit Nachweisen");
      fullPdf.href = "/v1/briefings/" + encodeURIComponent(r.briefing_id) + "/pdf";
      fullPdf.download = "";
      actions.append(fullPdf);
    } else {
      const b = button(
        "PDF erneut erstellen",
        () =>
          busy(b, async () => {
            const response = await api(
              "/v1/briefings/" +
                encodeURIComponent(r.briefing_id) +
                "/pdf/retry",
              { method: "POST" },
            );
            if (response?.pdf_status === "ready") {
              notify("PDF erneut erstellt.");
              route();
            } else {
              notify("PDF-Erstellung gestartet.");
              navigate("/runs/" + r.run_id);
            }
          }),
        "button secondary",
      );
      actions.append(b);
    }
    append(
      actions,
      button(
        "Neu recherchieren",
        () => {
          state.input = structuredClone(r.input);
          state.input.confirmed = false;
          state.retryMeeting = r.meeting_id;
          state.draftId = null;
          state.warnings = [];
          state.mode = "form";
          navigate("/");
          notify(
            "Angaben für eine neue Version prüfen. Das bisherige Briefing bleibt erhalten.",
          );
        },
        "button secondary",
      ),
      link("Zur Historie", "/history", "button secondary"),
    );
    const toolbar = el("div", "briefing-toolbar");
    const primaryPdf = [...actions.children].find(node => node.textContent.startsWith("PDF "));
    const moreActions = foldReport("Weitere Aktionen", actions, "briefing-tools");
    if (primaryPdf) toolbar.append(primaryPdf);
    toolbar.append(moreActions);
    intro.querySelectorAll(".report-meta").forEach(node => moreActions.append(node));
    if (r.input.scheduled_at) intro.append(el("p", "reader-date", date(r.input.scheduled_at)));
    const versionTag = [...tags.children].find(node => node.textContent.startsWith("Version "));
    if (versionTag) moreActions.append(versionTag);
    intro.append(toolbar);
    sheet.append(intro);
    const purpose = el("div", "briefing-purpose");
    append(purpose, el("span", "mini-label", "Dein Gesprächsziel"), el("p", null, r.input.goal || "Erwartungen und nächsten Schritt gemeinsam klären."));
    sheet.append(purpose);
    if (r.reader_view?.attention?.length) sheet.append(notice(r.reader_view.attention.join(" ")));
    let nextStep = null;
    if (r.overview?.length) {
      const overview = el("section", "overview-grid");
      overview.setAttribute("aria-label", "Briefing auf einen Blick");
      r.overview.slice(0, 5).forEach((item) => {
        const card = el("article", "overview-card");
        card.dataset.category = item.category;
        append(card, el("h2", null, overviewLabels[item.category] || "Auf einen Blick"),
          ...(["hypothesis", "user_confirmed"].includes(item.kind) ? [tag(item.category === "opener" ? "Gesprächsvorschlag" : claimLabels[item.kind], item.kind)] : []), el("p", null, readerText(item.text)),
          citations(claimSources(item, r), r));
        overview.append(card);
      });
      const context = el("div", "reader-context");
      const contextTitle = el("h2", "reader-section-title", "Für deinen Termin");
      overview.prepend(contextTitle, context);
      overview.querySelectorAll(".overview-card").forEach(card => {
        if (card.dataset.category === "opener") {
          card.classList.add("reader-opener");
          card.querySelector(".tag")?.remove();
          card.querySelector("h2").textContent = "Dein Gesprächseinstieg · Vorschlag";
          const text = card.querySelector("p");
          text.textContent = text.textContent.replace(/^Gesprächsvorschlag:\s*/, "");
          overview.append(card);
        } else if (card.dataset.category === "next_step") {
          nextStep = card;
          nextStep.classList.add("reader-next-step");
          card.remove();
        } else context.append(card);
      });
      sheet.append(overview);
    } else {
    const top = el("div", "report-top");
    const summary = el("section", "surface");
    append(
      summary,
      el("div", "eyebrow", "Auf einen Blick"),
      el("h2", null, "Die Kernpunkte"),
    );
    const list = el("ul", "keypoints");
    r.summary.slice(0, 5).forEach((t) => list.append(el("li", null, t)));
    summary.append(list);
    const recommendation = el("section", "surface recommendation");
    append(
      recommendation,
      el("div", "eyebrow", "Meeting-Empfehlung"),
      el("h2", null, r.recommendation.value),
      el("p", null, r.recommendation.reason),
      tag(icpLabels[r.icp.status], r.icp.status),
      el(
        "p",
        "small muted",
        "Die Empfehlung ist von deiner Entscheidung getrennt.",
      ),
    );
    append(top, summary, recommendation);
    sheet.append(top);
    const open = el("section", "notice open-points");
    append(
      open,
      el("h3", null, "Wichtigste offene Punkte"),
      bulletList(
        [...new Set([...(r.icp.open_points || []), ...(r.gaps || [])])].slice(
          0,
          5,
        ),
        "Keine zusätzlichen offenen Punkte hinterlegt.",
      ),
    );
    sheet.append(open);
    }
    if (r.fixture_mode)
      sheet.append(
        notice(
          "Dieser gespeicherte Stand verwendet kontrollierte synthetische Quellen. Die Angaben sind kein Nachweis über ein reales Unternehmen.",
        ),
      );
    const layout = el("div", "report-layout report-focus"), body = el("div");
    append(layout, body);
    sheet.append(layout);
    const icp = reportSection("icp", "Zielgruppenpassung");
    append(icp, tag(icpLabels[r.icp.status], r.icp.status), el("p", null, r.recommendation.value + ": " + r.recommendation.reason));
    const criteria = el("div", "icp-criteria");
    r.icp.criteria.forEach((c) => {
      const item = el("article", "criterion");
      append(
        item,
        append(
          el("div", "criterion-heading"),
          el("h3", null, c.label),
          tag(
            {
              matching: "Erfüllt",
              deviating: "Abweichend",
              unclear: "Ungeklärt",
            }[c.status],
            c.status,
          ),
        ),
        el("p", null, "Wert: " + textValue(c.value)),
        el("p", null, c.reason),
        el(
          "p",
          "small muted",
          "Bezug: " +
            (c.subject_ref === "company"
              ? r.input.company.name
              : c.subject_ref) +
            " · Informationsstand: " +
            (c.information_as_of || "Unbekannt") +
            " · Herkunft: " +
            ({
              web_verified: "Web-Beleg",
              user_confirmed: "Bestätigte Eingabe",
              unknown: "Unbekannt",
            }[c.provenance] || c.provenance),
        ),
        citations(c.evidence_ids, r),
      );
      if (c.deviation_kind)
        append(
          item,
          el(
            "p",
            "small muted",
            "Abweichungsart: " +
              {
                corridor: "Größenkorridor",
                target_structure: "Zielstruktur",
                exclusion: "Bestätigter Ausschluss",
              }[c.deviation_kind],
          ),
        );
      criteria.append(item);
    });
    append(
      icp,
      criteria,
      el("h3", null, "Passender Leistungseinstieg"),
      el("p", null, r.icp.entry_reason),
    );
    const company = reportSection("A01", "Unternehmensüberblick");
    r.company.forEach((c) => company.append(claimNode(c, r)));
    if (!r.company.length)
      company.append(
        el(
          "p",
          "muted",
          "Keine hinreichend belegten Unternehmensangaben verfügbar.",
        ),
      );
    body.append(company);
    const people = reportSection("A02", "Ansprechpartner im Überblick");
    r.people.forEach((p) => {
      const card = el("article", "person-profile");
      append(
        card,
        el("h3", null, p.name),
        tag(profileLabels[p.status], p.status),
        el("h4", null, "Rolle"),
        claimNode(p.role, r),
        el("h4", null, "Beruflicher Werdegang"),
      );
      if (p.career.length)
        p.career.forEach((c) => card.append(claimNode(c, r)));
      else
        card.append(
          el("p", "muted", "Keine belegten Karrieredetails verfügbar."),
        );
      card.append(el("h4", null, "Berufliche Themen"));
      if (p.topics.length)
        p.topics.forEach((c) => card.append(claimNode(c, r)));
      else
        card.append(
          el("p", "muted", "Keine belegten beruflichen Themen verfügbar."),
        );
      if (p.gaps.length)
        append(card, el("h4", null, "Offen"), bulletList(p.gaps));
      people.append(card);
    });
    body.append(people);
    const opportunities = reportSection("A03", "Anlass und ATP-Ansatzpunkte");
    append(
      opportunities,
      el("p", null, "Anlass: " + types[r.input.meeting_type]),
      el(
        "p",
        null,
        "Gesprächsziel: " + (r.input.goal || "Nicht zusätzlich angegeben."),
      ),
    );
    r.opportunities.forEach((o) => {
      const item = el("article", "opportunity");
      append(
        item,
        el(
          "h3",
          null,
          {
            entry: "Ersteinschätzung und Setup",
            audit: "AI Audit",
            enablement: "AI Enablement",
            training: "KI Schulungen",
            workshop: "Workshop für Führungskräfte",
            keynote: "Impulse und Keynotes",
          }[o.service_id] || o.service_id,
        ),
        el("span", "mini-label", "Beobachtung"),
        append(el("p"), o.observation, citations(o.evidence_ids, r)),
        append(
          el("div", "hypothesis-text"),
          el("span", "mini-label", "Hypothese · im Gespräch prüfen"),
          el("p", null, o.hypothesis),
        ),
        el("span", "mini-label", "Prüfungsfrage"),
        el("p", null, o.validation_question),
      );
      opportunities.append(item);
    });
    if (!r.opportunities.length)
      opportunities.append(
        el(
          "p",
          "muted",
          "Kein belastbarer Ansatzpunkt abgeleitet. Die Gesprächsfragen klären den möglichen Bedarf.",
        ),
      );
    body.append(opportunities);
    const questions = reportSection("A04", "Die wichtigsten Gesprächsfragen");
    const ql = el("ol", "question-list");
    const extraQuestions = el("ol", "question-list");
    extraQuestions.start = 4;
    r.questions.forEach((q, index) =>
      (index < 3 ? ql : extraQuestions).append(
        append(
          el("li", index < 3 ? "priority-question" : ""),
          el("h3", null, q.text),
          foldReport("Warum diese Frage?", append(el("div"), el("p", "muted", q.purpose), el("p", "muted", q.reference)), "question-context"),
        ),
      ),
    );
    questions.append(ql);
    body.append(questions);
    const story = reportSection("A05", "Gesprächs-Storyline");
    r.storyline.forEach((p) =>
      story.append(
        append(
          el("article", "story-phase"),
          el("h3", null, p.phase),
          append(
            el("div"),
            el("p", null, p.goal),
            el("p", "impulse", p.impulse),
          ),
        ),
      ),
    );
    body.append(story, icp);
    const sources = reportSection("A06", "Quellen und Wissenslücken");
    const diagnostics = el("div", "research-diagnostics");
    const notes = r.report_notes || r;
    if (r.research_coverage?.length) {
      diagnostics.append(el("h3", null, "Rechercheabdeckung"));
      r.research_coverage.forEach((coverage) => {
        diagnostics.append(el("p", null,
          ({company: "Unternehmen", developments: "Entwicklungen", people: "Ansprechpartner", icp: "Zielgruppenprüfung"}[coverage.area] || coverage.area)
          + ": " + ({covered: "Abgedeckt", partial: "Teilweise abgedeckt", missing: "Öffentlich nicht ausreichend belegt"}[coverage.status] || coverage.status)));
        if (coverage.reasons?.length) diagnostics.append(bulletList(coverage.reasons));
      });
    }
    append(
      diagnostics,
      el("h3", null, "Offene Punkte"),
      bulletList(notes.gaps, "Keine weiteren Wissenslücken hinterlegt."),
      el("h3", null, "Bestätigte Angaben und offene Entscheidungen"),
      bulletList(notes.resolutions || resolutionNotes(r), "Keine zusätzlichen Nutzerentscheidungen hinterlegt."),
      el("h3", null, "Grenzen dieses Laufs"),
      bulletList(
        notes.limits,
        "Keine zusätzlichen technischen Einschränkungen hinterlegt.",
      ),
      el("div", "divider"),
      el("h3", null, "Quellenverzeichnis"),
      el(
        "p",
        "small muted",
        "Abrufzeit und Informationsstand sind getrennt angegeben. Ein heutiger Abruf bestätigt keine aktuelle Gültigkeit.",
      ),
    );
    r.sources.forEach((s, i) => {
      const n = el("article", "source");
      n.id = "source-" + i;
      append(
        n,
        el("h3", null, "[" + (i + 1) + "] " + s.title),
        externalLink(s.url, s.url, "url"),
        el(
          "p",
          "source-meta",
          (s.retrieval_kind === "search_index" ? "Profilhinweis im Recherchelauf: " : "Abgerufen: ") +
            date(s.retrieved_at) +
            " · Informationsstand: " +
            (s.information_as_of || "Unbekannt") +
            " · Veröffentlicht: " +
            (s.published_at || "Unbekannt"),
        ),
        tag(
          s.verified ? "Beleg geprüft" : s.retrieval_kind === "search_index" ? "Profiltreffer · Inhalt nicht direkt geprüft" : "Nicht hinreichend geprüft",
          s.verified ? "matching" : "unclear",
        ),
      );
      if (s.excerpt) append(n, foldReport("Textstelle lesen", el("blockquote", null, s.excerpt), "source-excerpt"));
      if (s.limitation) append(n, el("p", "small muted", s.limitation));
      sources.append(n);
    });
    if (!r.sources.length)
      sources.append(el("p", "muted", "Keine prüfbaren Web-Belege verfügbar."));
    sources.append(foldReport("Rechercheverlauf und offene Punkte", diagnostics));
    const details = el("details", "version-details");
    append(details, el("summary", null, "Ergebnis- und Versionsstand"));
    const dl = el("dl");
    [
      ["Ergebnisversion", r.result_version],
      ["Profil", r.profile_version],
      ["Profil-Hash", r.profile_hash],
      ["Prompt", r.prompt_version],
      ["Regeln", r.rule_version],
      ["Darstellung", r.presentation_version || "legacy"],
      [
        "Modelle",
        Object.entries(r.models || {})
          .map(([a, b]) => a + ": " + b)
          .join("; "),
      ],
      ["Meeting", r.meeting_id],
      ["Recherchelauf", r.run_id],
      ["Briefing", r.briefing_id],
    ].forEach(([k, v]) => previewRow(dl, k, v));
    details.append(dl);
    sources.append(details);
    const sourceDetails = foldReport("Quellen & Recherchegrenzen", append(el("div"), qualityNode(r), sources));
    const background = foldReport("Unternehmen & Personen", append(el("div"), company, people));
    background.id = "reader-background";
    const assessmentContent = append(el("div"), opportunities, story, icp);
    if (r.questions.length > 3) assessmentContent.append(foldReport("Weitere Gesprächsfragen", extraQuestions));
    if (r.memory?.entries?.length) assessmentContent.append(foldReport("Eigenes Vorwissen · " + r.memory.entries.length + " Hinweise", memoryEvidence(r.memory)));
    const assessment = foldReport("Einordnung", assessmentContent);
    assessment.id = "reader-assessment";
    const supporting = el("section", "reader-supporting");
    const backToQuestions = el("a", "reader-back-to-questions", "Zurück zu den Gesprächsfragen");
    backToQuestions.href = "#A04";
    backToQuestions.addEventListener("click", event => navigateReportAnchor(event, "#A04"));
    append(supporting, el("h2", "reader-section-title", "Bei Bedarf vertiefen"), backToQuestions, background, assessment, sourceDetails);
    const decision = foldReport("Entscheidung festhalten", decisionPanel(r), "report-fold reader-action");
    const followUp = el("section", "reader-follow-up");
    append(followUp, el("h2", "reader-section-title", "Nach dem Lesen"), decision);
    body.replaceChildren(questions);
    if (nextStep) body.append(nextStep);
    append(body, supporting, followUp);
    if (config.memory_enabled) {
      try {
        const feedback = await briefingFeedback(r, items => {
          if (!body.isConnected) return;
          main.querySelectorAll(".overview-card").forEach(card => {
            card.querySelector(".feedback-correction-note")?.remove();
            const claim = card.querySelector("p")?.textContent;
            const correction = items.find(item => item.kind === "correction" && item.reference && item.reference === claim?.slice(0, 300));
            if (correction) card.append(append(el("aside", "feedback-correction-note"), el("strong", null, "Deine Korrektur"), el("p", null, correction.text)));
          });
        });
        if (!body.isConnected) return;
        if (feedback) {
          followUp.append(feedback.element);
          if (feedback.correct) {
            const edit = button("Aussage korrigieren", () => {
              const active = !sheet.classList.contains("reader-editing");
              sheet.classList.toggle("reader-editing", active);
              edit.setAttribute("aria-pressed", String(active));
              if (active) sheet.querySelector(".feedback-correction")?.focus();
            }, "text-button reader-edit-toggle");
            edit.setAttribute("aria-pressed", "false");
            toolbar.append(edit);
            main.querySelectorAll(".overview-card").forEach(card => {
              const claim = card.querySelector("p");
              if (claim) card.append(button("Korrigieren", () => feedback.correct(claim.textContent), "text-button feedback-correction"));
            });
          }
        }
      } catch (error) { showError(error, body); }
    }
    if (location.hash) revealReportTarget(location.hash);
  } catch (error) {
    main.replaceChildren();
    showError(error);
  }
}
function decisionPanel(r) {
  const section = el("section", "decision-panel");
  section.id = "decision";
  const box = el("div", "surface");
  append(
    box,
    el("div", "eyebrow", "Entscheidung durch ATP"),
    el("h2", null, "Wie gehst du weiter vor?"),
    el(
      "p",
      "small muted",
      "Halte fest, ob du den Termin wahrnimmst oder vorher etwas klären möchtest.",
    ),
  );
  append(box, el("p", "decision-recommendation", "Empfehlung: " + r.recommendation.value), el("p", "muted", r.recommendation.reason));
  if (r.decision)
    append(
      box,
      notice(
        "Gespeicherte Entscheidung: " +
          r.decision.confirmed_decision +
          " · " +
          date(r.decision.decided_at),
      ),
      el(
        "p",
        "small muted",
        "Entschieden von: " +
          r.decision.decided_by +
          (r.decision.override_reason
            ? " · Begründung: " + r.decision.override_reason
            : ""),
      ),
    );
  const form = el("form");
  const values = Object.fromEntries(decisions.map((x) => [x, x]));
  const choice = selectField(
    "Deine Entscheidung",
    "decision",
    values,
    r.decision?.confirmed_decision || r.recommendation.value,
  );
  const reason = field(
    "Begründung bei abweichender Entscheidung",
    "override_reason",
    r.decision?.override_reason || "",
    { area: true, max: 2000 },
  );
  const updateReason = () => {
    const differs = choice.input.value !== r.recommendation.value;
    reason.wrap.hidden = !differs;
    reason.input.required = differs;
  };
  choice.input.addEventListener("change", updateReason);
  updateReason();
  const b = button("Entscheidung speichern", null, "button", "submit");
  append(form, choice.wrap, reason.wrap, b);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(b, async () => {
      if (
        choice.input.value !== r.recommendation.value &&
        !reason.input.value.trim()
      ) {
        notify("Bitte die abweichende Entscheidung begründen.", true);
        reason.input.focus();
        return;
      }
      await api(
        "/v1/meetings/" + encodeURIComponent(r.meeting_id) + "/decision",
        {
          method: "POST",
          body: JSON.stringify({
            confirmed_decision: choice.input.value,
            override_reason: reason.input.value,
            run_id: r.run_id,
          }),
        },
      );
      notify("Entscheidung gespeichert.");
      route();
    });
  });
  append(box, form);
  section.append(box);
  return section;
}
async function confirmDelete(meetingId, name) {
  const dialog = el("dialog");
  append(
    dialog,
    el("div", "eyebrow", "Vorbereitung löschen"),
    el("h2", null, name),
    el(
      "p",
      null,
      "Das Meeting wird sofort für alle Zugriffe gesperrt. Alle zugehörigen Versionen, Belege und PDFs werden anschließend gelöscht. Bereits heruntergeladene Dateien bleiben außerhalb der Anwendung bestehen.",
    ),
  );
  const actions = el("div", "actions");
  append(
    actions,
    button(
      "Behalten",
      () => {
        dialog.close();
        dialog.remove();
      },
      "button secondary",
    ),
  );
  const b = button(
    "Vorbereitung löschen",
    () =>
      busy(b, async () => {
        await api("/v1/meetings/" + encodeURIComponent(meetingId), {
          method: "DELETE",
        });
        dialog.close();
        dialog.remove();
        notify("Vorbereitung gesperrt. Die Löschung wurde beauftragt.");
        route();
      }),
    "button danger",
  );
  actions.append(b);
  dialog.append(actions);
  document.body.append(dialog);
  dialog.addEventListener("cancel", () => dialog.remove());
  dialog.showModal();
}
async function renderHistory() {
  append(
    main,
    heading(
      "Historie",
      "Vorbereitet. Und wieder auffindbar.",
      "Eigene und ausdrücklich freigegebene Meetings mit ihren getrennten Ergebnisversionen.",
    ),
  );
  const form = el("form", "search-row");
  const search = el("input");
  search.type = "search";
  search.name = "q";
  search.setAttribute("aria-label", "Firma oder Termin suchen");
  search.placeholder = "Firma oder Termin suchen";
  search.value = new URLSearchParams(location.search).get("q") || "";
  const b = button("Suchen", null, "button secondary", "submit");
  append(form, search, b);
  main.append(form);
  const results = el("div");
  main.append(results);
  const feedback = notice("");
  feedback.hidden = true;
  feedback.setAttribute("role", "status");
  main.append(feedback);
  const cards = new Map();
  const timing = createHistoryTiming();
  let query = search.value;
  let request = 0;
  let hasLoaded = false;
  async function load() {
    clearTimeout(state.poll);
    const generation = ++request;
    if (!hasLoaded) results.replaceChildren(notice("Vorbereitungen werden geladen …"));
    try {
      const data = await api(
        "/v1/meetings?q=" + encodeURIComponent(query),
      );
      if (state.path !== "/history" || !results.isConnected || generation !== request) return;
      if (!hasLoaded) results.replaceChildren();
      hasLoaded = true;
      feedback.hidden = true;
      const items = data.items || [];
      const ids = new Set(items.map(m => String(m.meeting_id || m.id)));
      for (const [id, card] of cards) {
        if (!ids.has(id)) { card.remove(); cards.delete(id); }
      }
      if (items.length) results.querySelector(".empty")?.remove();
      if (!items.length) {
        const empty = el("div", "empty");
        append(
          empty,
          el(
            "h2",
            null,
            query
              ? "Keine passende Vorbereitung"
              : "Dein erstes Briefing wartet.",
          ),
          el(
            "p",
            "muted",
            query
              ? "Suche mit einem anderen Firmennamen oder öffne alle Vorbereitungen."
              : "Beginne mit Firma, Ansprechpartnern und Anlass. Gespeicherte Meetings erscheinen anschließend hier.",
          ),
          link("Neue Vorbereitung", "/", "button"),
        );
        results.replaceChildren(empty);
        return;
      }
      items.forEach((m) => {
        const id = String(m.meeting_id || m.id);
        const runs = m.runs || [];
        const latest = runs.find(x => (x.run_id || x.id) === m.latest_run_id) || runs[0];
        const displayStatus = latest?.status || m.status;
        const phase = historyPhase(latest || m);
        const card = el("article", "history-card");
        const content = el("div");
        append(
          content,
          el(
            "h2",
            null,
            m.company_name || m.input?.company?.name || "Vorbereitung",
          ),
          el(
            "p",
            null,
            "Meeting: " + date(m.scheduled_at || m.input?.scheduled_at),
          ),
          tag(statuses[phase] || phase || "Gespeichert", displayStatus),
          el("p", "history-start", latest
            ? `Version ${latest.result_version || latest.version || "–"} · Gestartet: ${latest.created_at ? date(latest.created_at) : "Zeitpunkt unbekannt"}`
            : "Noch kein Lauf gestartet"),
          el("p", "history-estimate", latest ? timing(latest) : ""),
        );
        if (runs.length) {
          const d = el("details");
          append(
            d,
            el(
              "summary",
              null,
              runs.length +
                " Version" +
                (runs.length === 1 ? "" : "en") +
                " anzeigen",
            ),
          );
          const ul = el("ul", "version-list");
          runs.forEach((run) => {
            const rid = run.run_id || run.id;
            const bid = run.briefing_id;
            append(
              ul,
              append(
                el("li"),
                link(
                  "Version " + (run.result_version || run.version || "–"),
                  bid ? "/briefings/" + bid : "/runs/" + rid,
                ),
                tag(statuses[historyPhase(run)] || historyPhase(run), run.status),
                el("span", "muted", run.created_at ? date(run.created_at) : ""),
              ),
            );
          });
          append(d, ul);
          content.append(d);
        }
        const actions = el("div", "actions");
        const bid = m.briefing_id || latest?.briefing_id;
        const rid = m.latest_run_id || latest?.run_id || latest?.id;
        if (bid)
          actions.append(
            link(
              "Briefing öffnen →",
              "/briefings/" + bid,
              "button secondary small",
            ),
          );
        else if (rid)
          actions.append(
            link("Status öffnen →", "/runs/" + rid, "button secondary small"),
          );
        if (rid && !bid) {
          const retry = button(
            "Neue Recherche",
            () =>
              busy(retry, async () => {
                const run = await api("/v1/runs/" + encodeURIComponent(rid));
                if (!run.input && !m.input) {
                  notify(
                    "Öffne den gespeicherten Eingabestand, um einen neuen Lauf zu bestätigen.",
                    true,
                  );
                  return;
                }
                state.input = structuredClone(run.input || m.input);
                state.retryMeeting = id;
                state.mode = "form";
                state.draftId = null;
                navigate("/");
              }),
            "button secondary small",
          );
          actions.append(retry);
        }
        actions.append(
          button(
            "Löschen",
            () => confirmDelete(id, m.company_name || "Meeting"),
            "text-button",
          ),
        );
        append(card, content, actions);
        card.dataset.actionKey = `${latest?.run_id || latest?.id || ""}:${bid || ""}`;
        const previous = cards.get(id);
        if (previous) patchHistoryCard(previous, card);
        else { cards.set(id, card); results.append(card); }
      });
      items.forEach((m, index) => {
        const card = cards.get(String(m.meeting_id || m.id));
        if (results.children[index] !== card) results.insertBefore(card, results.children[index] || null);
      });
      if (items.some(m => (m.runs || []).some(r =>
        !["completed", "partial", "failed", "cancelled"].includes(r.status))))
        state.poll = setTimeout(load, 5000);
    } catch (e) {
      if (state.path !== "/history" || !results.isConnected || generation !== request) return;
      if (e.status === 401 || e.status === 404) {
        results.replaceChildren(); cards.clear(); hasLoaded = false;
        showError(e, results);
        return;
      }
      if (!hasLoaded) { results.replaceChildren(); showError(e, results); }
      feedback.textContent = "Aktualisierung fehlgeschlagen – die Angaben sind möglicherweise veraltet. Erneuter Versuch folgt.";
      feedback.hidden = false;
      state.poll = setTimeout(load, 5000);
    }
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    query = search.value;
    history.replaceState(
      {},
      "",
      "/history" +
        (search.value ? "?q=" + encodeURIComponent(search.value) : ""),
    );
    load();
  });
  await load();
}
function renderLogin() {
  const layout = el("div", "login-layout");
  append(
    layout,
    heading(
      "Interner Zugang",
      "Willkommen bei ATP.",
      "Melde dich an, um Vorbereitungen zu speichern und geschützte Briefings zu öffnen.",
    ),
  );
  const form = el("form", "surface");
  const email = field("E-Mail-Adresse", "email", "", {
    type: "email",
    required: true,
    auto: "username",
  });
  const pass = field("Passwort", "password", "", {
    type: "password",
    required: true,
    auto: "current-password",
  });
  const b = button("Anmelden", null, "button", "submit");
  const errors = el("div", "form-error");
  errors.setAttribute("role", "alert");
  append(
    form,
    email.wrap,
    pass.wrap,
    b,
    errors,
    el(
      "p",
      "small muted",
      "Dein Zugang wird von der ATP-Administration bereitgestellt.",
    ),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errors.textContent = "";
    const old = b.textContent;
    b.disabled = true;
    b.textContent = "Anmeldung läuft …";
    try {
      await api("/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: email.input.value,
          password: pass.input.value,
        }),
      });
      pass.input.value = "";
      state.authenticated = true;
      authLabel();
      notify("Du bist angemeldet.");
      const target = loginDestination(new URLSearchParams(location.search).get("next"));
      if (target && new URL(target, location.origin).pathname === "/help") window.location.assign(target);
      else navigate(target || (state.input ? "/" : "/history"));
    } catch (e) {
      errors.textContent = e.message;
      pass.input.value = "";
    } finally {
      b.disabled = false;
      b.textContent = old;
    }
  });
  layout.append(form);
  main.append(layout);
}
async function route() {
  clearTimeout(state.poll);
  state.path = location.pathname;
  const backTarget = state.path.startsWith("/knowledge/") || state.path.startsWith("/memory-tasks/")
    ? "/knowledge" : /^\/(runs|briefings)\//.test(state.path) ? "/history" : state.path !== "/" ? "/" : null;
  setBack(backTarget);
  main.replaceChildren();
  document
    .querySelectorAll("[data-nav]")
    .forEach((a) =>
      a.classList.toggle("active", a.getAttribute("href") === state.path),
    );
  document.title =
    state.path === "/history"
      ? "Historie · ATP"
      : state.path === "/login"
        ? "Anmelden · ATP"
        : "Meeting-Vorbereitung · ATP";
  if (state.path === "/history") return renderHistory();
  if (state.path === "/login") return renderLogin();
  if (state.path === "/knowledge") return renderKnowledge();
  if (state.path === "/projects") { history.replaceState({}, "", "/knowledge"); return route(); }
  const context = state.path.match(/^\/knowledge\/([^/]+)$/);
  if (context) return renderKnowledge(decodeURIComponent(context[1]));
  const task = state.path.match(/^\/memory-tasks\/([^/]+)$/);
  if (task) return renderMemoryTask(decodeURIComponent(task[1]));
  const run = state.path.match(/^\/runs\/([^/]+)$/);
  if (run) return renderRun(decodeURIComponent(run[1]));
  const brief = state.path.match(/^\/briefings\/([^/]+)$/);
  if (brief) return renderBriefing(decodeURIComponent(brief[1]));
  renderInput();
}
route();
if (config.configured && location.pathname !== "/login")
  fetch("/v1/meetings", { credentials: "same-origin" })
    .then((r) => {
      if (r.ok) {
        state.authenticated = true;
        authLabel();
      }
    })
    .catch(() => {});
