const channelSelect = document.querySelector("#channel");
const channelStatus = document.querySelector("#channel-status");
const activeCheckin = document.querySelector("#active-checkin");
const historyList = document.querySelector("#history");
const emptyHistory = document.querySelector("#empty-history");
const appError = document.querySelector("#app-error");
let creatingCheckin = false;

const outcomeLabels = {
  OK: "Estou bem",
  HELP_REQUESTED: "Pediu ajuda",
  POSTPONED: "Preferiu responder depois",
  UNANSWERED: "Sem resposta",
};

const channelLabels = {
  telephone: "Telefone (simulado)",
  whatsapp_call: "Chamada de voz pelo WhatsApp (simulada)",
  whatsapp_message: "Mensagem pelo WhatsApp (simulada)",
};

const responseOptions = [
  { id: "checkin_ok", label: "Estou bem", className: "response-ok" },
  { id: "checkin_help", label: "Preciso de ajuda", className: "response-help" },
  {
    id: "checkin_postpone",
    label: "Não posso responder agora",
    className: "response-postpone",
  },
];

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail || "Não foi possível concluir a operação.");
  }
  return body;
}

function showError(error) {
  appError.textContent = error.message;
}

function clearError() {
  appError.textContent = "";
}

async function loadPreferences() {
  const preferences = await request("/api/v1/customers/me/checkin-preferences");
  channelSelect.replaceChildren(
    ...preferences.channels.map((channel) => {
      const option = document.createElement("option");
      option.value = channel.id;
      option.textContent = channel.label;
      return option;
    }),
  );
  channelSelect.value = preferences.preferred_checkin_channel;
}

function formatDate(value) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "America/Sao_Paulo",
  }).format(new Date(value));
}

function checkinStatus(checkin) {
  if (checkin.final_outcome) return outcomeLabels[checkin.final_outcome];
  if (checkin.started_at) return "Aguardando resposta";
  return `Agendado para ${formatDate(checkin.scheduled_for)}`;
}

function renderHistory(items) {
  historyList.replaceChildren();
  emptyHistory.hidden = items.length > 0;
  for (const checkin of items) {
    const item = document.createElement("li");
    const title = document.createElement("strong");
    title.textContent = checkinStatus(checkin);
    const detail = document.createElement("span");
    detail.textContent = checkin.started_at
      ? formatDate(checkin.started_at)
      : formatDate(checkin.scheduled_for);
    item.append(title, detail);
    historyList.append(item);
  }
}

function renderActiveCheckin(checkin) {
  activeCheckin.replaceChildren();
  if (!checkin) return;

  const heading = document.createElement("h3");
  heading.textContent = checkinStatus(checkin);
  const description = document.createElement("p");
  description.textContent = checkin.started_at
    ? `Canal: ${channelLabels[checkin.final_channel]}. Responda até ${formatDate(checkin.response_deadline_at)}.`
    : `Este check-in está agendado para ${formatDate(checkin.scheduled_for)}.`;
  activeCheckin.append(heading, description);

  if (checkin.final_outcome === "HELP_REQUESTED") {
    const note = document.createElement("p");
    note.className = "notice";
    note.textContent =
      "O pedido de ajuda foi registrado apenas nesta demonstração. Ninguém foi avisado.";
    activeCheckin.append(note);
  }

  if (!checkin.final_outcome && checkin.started_at) {
    const actions = document.createElement("div");
    actions.className = "response-actions";
    actions.setAttribute("aria-label", "Escolha sua resposta");
    for (const option of responseOptions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `button ${option.className}`;
      button.textContent = option.label;
      button.addEventListener("click", () => submitResponse(checkin.id, option.id));
      actions.append(button);
    }
    activeCheckin.append(actions);
  }
}

async function refresh() {
  const data = await request("/api/v1/checkins");
  renderHistory(data.items);
  const active = data.items.find(
    (checkin) => checkin.started_at && !checkin.final_outcome,
  );
  renderActiveCheckin(active || data.items[0]);
}

async function submitResponse(checkinId, response) {
  clearError();
  const buttons = activeCheckin.querySelectorAll("button");
  buttons.forEach((button) => {
    button.disabled = true;
  });
  try {
    await request(`/api/v1/checkins/${checkinId}/response`, {
      method: "POST",
      body: JSON.stringify({ response }),
    });
    await refresh();
  } catch (error) {
    showError(error);
    buttons.forEach((button) => {
      button.disabled = false;
    });
  }
}

document.querySelector("#save-channel").addEventListener("click", async () => {
  clearError();
  channelStatus.textContent = "";
  try {
    const preferences = await request(
      "/api/v1/customers/me/checkin-preferences",
      {
        method: "PATCH",
        body: JSON.stringify({
          preferred_checkin_channel: channelSelect.value,
        }),
      },
    );
    channelStatus.textContent = `Canal salvo: ${preferences.preferred_checkin_channel_label}.`;
  } catch (error) {
    showError(error);
  }
});

document.querySelector("#start-checkin").addEventListener("click", async (event) => {
  if (creatingCheckin) return;
  creatingCheckin = true;
  event.currentTarget.disabled = true;
  clearError();
  try {
    await request("/api/v1/checkins", { method: "POST" });
    await refresh();
  } catch (error) {
    showError(error);
  } finally {
    creatingCheckin = false;
    event.currentTarget.disabled = false;
  }
});

async function initialize() {
  try {
    await loadPreferences();
    await refresh();
    window.setInterval(() => {
      refresh().catch(showError);
    }, 2000);
  } catch (error) {
    showError(error);
  }
}

initialize();

