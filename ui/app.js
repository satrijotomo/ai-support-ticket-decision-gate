"use strict";

const API_ROOT = "/api";
const POLL_INTERVAL_MS = 2000;
const TERMINAL_STATUSES = new Set(["Completed", "Rejected", "ApprovalTimedOut", "Failed"]);
const AGENTS = [
  { name: "support-triage-agent", label: "Triage" },
  { name: "support-knowledge-agent", label: "Knowledge" },
  { name: "support-risk-reviewer-agent", label: "Risk reviewer" },
];

const state = {
  tickets: [],
  selectedTicketId: null,
  detail: null,
  pollTimer: null,
  loadingDetail: false,
};

const elements = {
  connectionStatus: document.querySelector("#connection-status"),
  failNextButton: document.querySelector("#fail-next-button"),
  toggleFormButton: document.querySelector("#toggle-form-button"),
  ticketForm: document.querySelector("#ticket-form"),
  submitButton: document.querySelector("#submit-button"),
  formMessage: document.querySelector("#form-message"),
  refreshButton: document.querySelector("#refresh-button"),
  ticketCount: document.querySelector("#ticket-count"),
  ticketList: document.querySelector("#ticket-list"),
  emptyState: document.querySelector("#empty-state"),
  detailContent: document.querySelector("#detail-content"),
  detailStatus: document.querySelector("#detail-status"),
  pollIndicator: document.querySelector("#poll-indicator"),
  detailTitle: document.querySelector("#detail-title"),
  detailDescription: document.querySelector("#detail-description"),
  ticketMetadata: document.querySelector("#ticket-metadata"),
  detailError: document.querySelector("#detail-error"),
  agentResults: document.querySelector("#agent-results"),
  recommendation: document.querySelector("#recommendation"),
  approvalSection: document.querySelector("#approval-section"),
  decisionForm: document.querySelector("#decision-form"),
  decisionMessage: document.querySelector("#decision-message"),
  auditTimeline: document.querySelector("#audit-timeline"),
  toastRegion: document.querySelector("#toast-region"),
};

function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function appendDefinition(list, label, value) {
  list.append(createElement("dt", "", label), createElement("dd", "", value || "Not provided"));
}

function formatDate(value) {
  if (!value) return "Not available";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function humanize(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/^./, (character) => character.toUpperCase());
}

function setMessage(element, message = "", kind = "") {
  element.textContent = message;
  if (kind) element.dataset.kind = kind;
  else delete element.dataset.kind;
}

function showToast(message, kind = "success") {
  const toast = createElement("div", "toast", message);
  toast.dataset.kind = kind;
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

async function request(path, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(payload?.error || `Request failed with status ${response.status}`);
  }
  return payload;
}

function setConnection(online) {
  elements.connectionStatus.textContent = online ? "Connected" : "API unavailable";
  elements.connectionStatus.dataset.state = online ? "online" : "offline";
}

function statusBadge(status) {
  const badge = createElement("span", "status-badge", humanize(status));
  badge.dataset.status = status;
  return badge;
}

function renderTicketList() {
  elements.ticketCount.textContent = String(state.tickets.length);
  elements.ticketList.replaceChildren();
  if (!state.tickets.length) {
    elements.ticketList.append(createElement("p", "loading", "No tickets submitted yet."));
    return;
  }

  for (const ticket of state.tickets) {
    const button = createElement("button", "ticket-item");
    button.type = "button";
    button.dataset.ticketId = ticket.ticket_id;
    button.setAttribute("aria-current", String(ticket.ticket_id === state.selectedTicketId));
    const firstRow = createElement("span", "ticket-row");
    firstRow.append(createElement("span", "ticket-title", ticket.title), statusBadge(ticket.status));
    const secondRow = createElement("span", "ticket-row");
    secondRow.append(
      createElement("span", "ticket-date", formatDate(ticket.created_at)),
      createElement("span", "ticket-date", ticket.assigned_team || "Unassigned"),
    );
    button.append(firstRow, secondRow);
    button.addEventListener("click", () => selectTicket(ticket.ticket_id));
    elements.ticketList.append(button);
  }
}

async function loadTickets({ quiet = false } = {}) {
  if (!quiet) elements.ticketList.replaceChildren(createElement("p", "loading", "Loading tickets…"));
  try {
    state.tickets = await request("/tickets");
    setConnection(true);
    renderTicketList();
  } catch (error) {
    setConnection(false);
    if (!quiet) elements.ticketList.replaceChildren(createElement("p", "loading", error.message));
  }
}

function renderMetadata(ticket) {
  elements.ticketMetadata.replaceChildren();
  appendDefinition(elements.ticketMetadata, "Service", ticket.affected_service);
  appendDefinition(elements.ticketMetadata, "Impact", ticket.customer_impact);
  appendDefinition(elements.ticketMetadata, "Submitted by", ticket.submitted_by);
  appendDefinition(elements.ticketMetadata, "Assigned team", ticket.assigned_team);
  appendDefinition(elements.ticketMetadata, "Created", formatDate(ticket.created_at));
  appendDefinition(elements.ticketMetadata, "Updated", formatDate(ticket.updated_at));
}

function parsedJson(value) {
  if (!value) return null;
  try { return JSON.parse(value); } catch { return null; }
}

function displayValue(value) {
  if (Array.isArray(value)) {
    const list = createElement("ul");
    value.forEach((item) => list.append(createElement("li", "", item)));
    return list;
  }
  if (typeof value === "boolean") return document.createTextNode(value ? "Yes" : "No");
  if (value && typeof value === "object") return document.createTextNode(JSON.stringify(value));
  return document.createTextNode(value ?? "Not provided");
}

function renderAgentResults(results) {
  elements.agentResults.replaceChildren();
  const byName = new Map(results.map((result) => [result.agent_name, result]));
  for (const agent of AGENTS) {
    const card = createElement("article", "agent-card");
    card.append(createElement("h4", "", agent.label));
    const record = byName.get(agent.name);
    const result = parsedJson(record?.result_json);
    if (!record || !result) {
      card.append(createElement("p", "agent-placeholder", "Waiting for analysis."));
    } else {
      const list = createElement("dl");
      for (const [key, value] of Object.entries(result)) {
        if (key === "confidence") continue;
        const wrapper = createElement("div");
        wrapper.append(createElement("dt", "", humanize(key)));
        const description = createElement("dd");
        description.append(displayValue(value));
        wrapper.append(description);
        list.append(wrapper);
      }
      card.append(list);
      const confidence = record.confidence ?? result.confidence;
      if (confidence !== null && confidence !== undefined) {
        card.append(createElement("p", "confidence", `Confidence ${Math.round(confidence * 100)}%`));
      }
    }
    elements.agentResults.append(card);
  }
}

function recommendationField(label, value, className = "") {
  const wrapper = createElement("div", className);
  wrapper.append(createElement("div", "recommendation-label", label));
  const content = createElement("div", className === "" ? "recommendation-copy" : "recommendation-value");
  content.append(displayValue(value));
  wrapper.append(content);
  return wrapper;
}

function renderRecommendation(recommendation) {
  elements.recommendation.replaceChildren();
  if (!recommendation) {
    elements.recommendation.append(createElement("p", "agent-placeholder", "Recommendation is generated after all agent analyses complete."));
    return;
  }
  const card = createElement("article", "recommendation-card");
  const summary = createElement("div", "recommendation-summary");
  summary.append(
    recommendationField("Priority", recommendation.priority, "summary-field"),
    recommendationField("Category", recommendation.category, "summary-field"),
    recommendationField("Route to", recommendation.recommended_team, "summary-field"),
    recommendationField("Risk", recommendation.risk_level, "summary-field"),
  );
  const details = createElement("div", "recommendation-grid");
  details.append(
    recommendationField("Draft response", recommendation.draft_response),
    recommendationField("Suggested steps", recommendation.suggested_steps),
    recommendationField("Concerns", recommendation.concerns.length ? recommendation.concerns : ["None recorded"]),
  );
  card.append(summary, details);
  elements.recommendation.append(card);
}

function renderAudit(events) {
  elements.auditTimeline.replaceChildren();
  if (!events.length) {
    elements.auditTimeline.append(createElement("li", "agent-placeholder", "No audit events recorded."));
    return;
  }
  for (const event of events) {
    const item = createElement("li", "timeline-item");
    const marker = createElement("span", "timeline-marker");
    const content = createElement("div", "timeline-content");
    content.append(createElement("div", "timeline-title", humanize(event.event_type)));
    const data = parsedJson(event.event_data);
    if (data) {
      content.append(createElement("p", "timeline-data", Object.entries(data).map(([key, value]) => `${humanize(key)}: ${value}`).join(" · ")));
    }
    item.append(createElement("time", "timeline-time", formatDate(event.occurred_at)), marker, content);
    elements.auditTimeline.append(item);
  }
}

function renderDetail(detail) {
  const ticket = detail.ticket;
  elements.emptyState.hidden = true;
  elements.detailContent.hidden = false;
  elements.detailStatus.replaceWith(statusBadge(ticket.status));
  elements.detailStatus = document.querySelector("#detail-content .status-badge");
  elements.detailStatus.id = "detail-status";
  elements.detailTitle.textContent = ticket.title;
  elements.detailDescription.textContent = ticket.description;
  renderMetadata(ticket);
  renderAgentResults(detail.agent_results);
  renderRecommendation(detail.recommendation);
  renderAudit(detail.audit_events);
  elements.approvalSection.hidden = ticket.status !== "PendingApproval";
  elements.pollIndicator.hidden = TERMINAL_STATUSES.has(ticket.status);
  elements.detailError.hidden = true;
}

function schedulePolling(status) {
  window.clearTimeout(state.pollTimer);
  state.pollTimer = null;
  if (!state.selectedTicketId || TERMINAL_STATUSES.has(status)) return;
  state.pollTimer = window.setTimeout(async () => {
    await Promise.all([loadTicketDetail(state.selectedTicketId, { quiet: true }), loadTickets({ quiet: true })]);
  }, POLL_INTERVAL_MS);
}

async function loadTicketDetail(ticketId, { quiet = false } = {}) {
  if (state.loadingDetail) return;
  state.loadingDetail = true;
  if (!quiet) {
    elements.emptyState.hidden = false;
    elements.emptyState.replaceChildren(createElement("p", "loading", "Loading ticket details…"));
    elements.detailContent.hidden = true;
  }
  try {
    const detail = await request(`/tickets/${encodeURIComponent(ticketId)}`);
    if (ticketId !== state.selectedTicketId) return;
    state.detail = detail;
    setConnection(true);
    renderDetail(detail);
    schedulePolling(detail.ticket.status);
  } catch (error) {
    setConnection(false);
    if (quiet && state.detail) {
      elements.detailError.textContent = error.message;
      elements.detailError.hidden = false;
      schedulePolling(state.detail.ticket.status);
    } else {
      elements.emptyState.replaceChildren(createElement("p", "loading", error.message));
    }
  } finally {
    state.loadingDetail = false;
  }
}

async function selectTicket(ticketId) {
  state.selectedTicketId = ticketId;
  state.detail = null;
  window.clearTimeout(state.pollTimer);
  renderTicketList();
  await loadTicketDetail(ticketId);
}

async function submitTicket(event) {
  event.preventDefault();
  if (!elements.ticketForm.reportValidity()) return;
  elements.submitButton.disabled = true;
  setMessage(elements.formMessage, "Submitting ticket…");
  const formData = new FormData(elements.ticketForm);
  const payload = Object.fromEntries(
    [...formData.entries()].map(([key, value]) => [key, String(value).trim() || null]),
  );
  try {
    const accepted = await request("/tickets", { method: "POST", body: JSON.stringify(payload) });
    elements.ticketForm.reset();
    setMessage(elements.formMessage, "Ticket submitted. Analysis has started.", "success");
    await loadTickets({ quiet: true });
    await selectTicket(accepted.ticket_id);
  } catch (error) {
    setMessage(elements.formMessage, error.message, "error");
  } finally {
    elements.submitButton.disabled = false;
  }
}

async function submitDecision(decision) {
  if (!state.selectedTicketId || !elements.decisionForm.reportValidity()) return;
  const formData = new FormData(elements.decisionForm);
  const payload = {
    decision,
    approver: String(formData.get("approver") || "").trim(),
    comments: String(formData.get("comments") || "").trim(),
  };
  const buttons = elements.decisionForm.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  setMessage(elements.decisionMessage, `Submitting ${decision} decision…`);
  try {
    await request(`/tickets/${encodeURIComponent(state.selectedTicketId)}/decision`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setMessage(elements.decisionMessage, "Decision accepted. Workflow is resuming.", "success");
    showToast("Decision accepted");
    await loadTicketDetail(state.selectedTicketId, { quiet: true });
  } catch (error) {
    setMessage(elements.decisionMessage, error.message, "error");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function armFailure() {
  elements.failNextButton.disabled = true;
  try {
    await request("/demo/fail-next-action", { method: "POST", body: "{}" });
    showToast("The next assignment attempt will fail once.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    elements.failNextButton.disabled = false;
  }
}

elements.ticketForm.addEventListener("submit", submitTicket);
elements.refreshButton.addEventListener("click", async () => {
  await loadTickets();
  if (state.selectedTicketId) await loadTicketDetail(state.selectedTicketId, { quiet: true });
});
elements.failNextButton.addEventListener("click", armFailure);
elements.toggleFormButton.addEventListener("click", () => {
  const collapsed = !elements.ticketForm.hidden;
  elements.ticketForm.hidden = collapsed;
  elements.toggleFormButton.textContent = collapsed ? "+" : "−";
  elements.toggleFormButton.setAttribute("aria-expanded", String(!collapsed));
});
elements.decisionForm.querySelectorAll("[data-decision]").forEach((button) => {
  button.addEventListener("click", () => submitDecision(button.dataset.decision));
});

loadTickets();
