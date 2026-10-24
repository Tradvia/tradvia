// SPDX-License-Identifier: FSL-1.1-ALv2
// Service worker for Web Push (docs/notifications.md, ADR 0009).
// The push message has only {project, card, kind}: the text shown here is built from those three
// values and the labels below. No title, comment or plan text ever travels through the push.
"use strict";

const LABELS = {
  pt: {
    plan_ready: "plano pronto para aprovar", agent_question: "o agente fez uma pergunta",
    pr_opened: "PR aberto para revisão", budget_limit: "limite de orçamento atingido",
    adjust_limit: "limite de ajustes atingido", fallback: "um card precisa de você",
  },
  en: {
    plan_ready: "plan ready to approve", agent_question: "the agent asked a question",
    pr_opened: "pull request ready for review", budget_limit: "budget limit reached",
    adjust_limit: "adjustment limit reached", fallback: "a card needs you",
  },
};

function labelsFor() {
  return /^pt/i.test(self.navigator.language || "") ? LABELS.pt : LABELS.en;
}

// Keeps only plain values of the expected shape; anything else is dropped.
function parse(event) {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
  const project = typeof data.project === "string" && /^[a-z0-9._-]{1,100}$/i.test(data.project) ? data.project : "";
  const card = Number.isInteger(data.card) && data.card > 0 ? data.card : 0;
  const kind = typeof data.kind === "string" ? data.kind : "";
  return { project, card, kind };
}

self.addEventListener("push", (event) => {
  const { project, card, kind } = parse(event);
  const labels = labelsFor();
  const what = Object.prototype.hasOwnProperty.call(labels, kind) ? labels[kind] : labels.fallback;
  const body = [project, card ? "#" + card : "", what].filter(Boolean).join(" · ");
  event.waitUntil(self.registration.showNotification("Tradvia", {
    body,
    tag: "tradvia-" + project + "-" + card,
    data: { project, card },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const { project, card } = event.notification.data || {};
  const q = new URLSearchParams();
  if (project) q.set("project", project);
  if (card) q.set("card", String(card));
  const url = new URL("./?" + q.toString(), self.registration.scope).href;
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of open) {
      if (c.url.startsWith(self.registration.scope) && "navigate" in c) {
        await c.navigate(url);
        return c.focus();
      }
    }
    return self.clients.openWindow(url);
  })());
});
