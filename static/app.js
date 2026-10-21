let currentSession = null;
let sessionsList = [];
let activePollTimer = null;

async function fetchWithRetry(url, options = {}, maxRetries = 3) {
  let lastErr = null;
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await fetch(url, options);
    } catch (err) {
      lastErr = err;
      await new Promise((r) => setTimeout(r, 600 * (attempt + 1)));
    }
  }
  throw lastErr;
}

document.addEventListener("DOMContentLoaded", async () => {
  await checkApiKeyStatus();
  await loadSessionsList();

  if (sessionsList.length > 0) {
    await selectSession(sessionsList[0].id);
  } else {
    await createNewSession();
  }

  const promptEl = document.getElementById("promptInput");
  promptEl.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      sendPrompt();
    }
  });

  const titleEl = document.getElementById("sessionTitleInput");
  titleEl.addEventListener("change", () => {
    saveSessionSettings();
  });

  const prevInput = document.getElementById("prevInteractionIdInput");
  prevInput.addEventListener("input", updateHeaderAndComposerBadges);

  const envInput = document.getElementById("environmentIdInput");
  envInput.addEventListener("input", updateHeaderAndComposerBadges);
});

async function checkApiKeyStatus() {
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();
    const statusText = document.getElementById("apiKeyStatusText");
    if (data.has_api_key) {
      statusText.textContent = `Key (${data.masked_key})`;
    } else {
      statusText.textContent = "API Key (Proxy/Auto)";
    }
  } catch (err) {
    console.error("Erro ao verificar status da API Key:", err);
  }
}

async function loadSessionsList() {
  try {
    const res = await fetch("/api/sessions");
    const data = await res.json();
    sessionsList = data.sessions || [];
    renderSessionsSidebar();
  } catch (err) {
    console.error("Erro ao listar sessões:", err);
  }
}

function renderSessionsSidebar() {
  const container = document.getElementById("sessionsList");
  if (!sessionsList.length) {
    container.innerHTML = `
      <div class="p-3 text-xs text-gray-500 text-center">
        Nenhuma sessão salva ainda.
      </div>
    `;
    return;
  }

  container.innerHTML = sessionsList
    .map((s) => {
      const isSelected = currentSession && currentSession.id === s.id;
      const envBadge = s.environment_id
        ? `<span class="text-[10px] font-mono text-purple-300 bg-purple-500/10 px-1.5 py-0.5 rounded">${escapeHtml(s.environment_id.slice(0, 12))}</span>`
        : "";
      return `
        <div
          onclick="selectSession('${s.id}')"
          class="group flex items-center justify-between px-2.5 py-2 rounded-lg cursor-pointer transition text-xs ${
            isSelected
              ? "bg-blue-600/20 border border-blue-500/40 text-white"
              : "hover:bg-studio-card text-gray-300 border border-transparent"
          }"
        >
          <div class="min-w-0 flex-1 pr-2">
            <div class="font-medium truncate">${escapeHtml(s.title || "Sem título")}</div>
            <div class="flex items-center gap-1.5 mt-1 text-[10px] text-gray-400">
              <span>${s.turn_count || 0} turnos</span>
              ${envBadge}
            </div>
          </div>
          <button
            onclick="event.stopPropagation(); removeSession('${s.id}')"
            class="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400 px-1 transition"
            title="Excluir sessão"
          >
            ✕
          </button>
        </div>
      `;
    })
    .join("");
}

async function createNewSession() {
  try {
    const res = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: "Nova Conversa Antigravity",
        mode: "agent",
        agent_name: "antigravity-preview-09-2026",
        model_name: "gemini-3.8-flash",
      }),
    });
    const session = await res.json();
    await loadSessionsList();
    await selectSession(session.id);
  } catch (err) {
    alert("Erro ao criar sessão: " + err.message);
  }
}

async function selectSession(sessionId) {
  stopPolling();
  try {
    const res = await fetch(`/api/sessions/${sessionId}`);
    if (!res.ok) return;
    currentSession = await res.json();
    syncInputsFromSession();
    renderSessionsSidebar();
    renderChatTurns();

    // Se o último turno estiver in_progress, inicia polling automático
    const turns = currentSession.turns || [];
    if (turns.length > 0 && turns[turns.length - 1].status === "in_progress") {
      startPollingTurn(currentSession.id, turns[turns.length - 1].interaction_id);
    }
  } catch (err) {
    console.error("Erro ao selecionar sessão:", err);
  }
}

async function removeSession(sessionId) {
  if (!confirm("Deseja realmente excluir esta sessão do histórico local?")) return;
  await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
  await loadSessionsList();
  if (currentSession && currentSession.id === sessionId) {
    if (sessionsList.length > 0) {
      await selectSession(sessionsList[0].id);
    } else {
      await createNewSession();
    }
  }
}

function syncInputsFromSession() {
  if (!currentSession) return;
  document.getElementById("sessionTitleInput").value = currentSession.title || "";
  document.getElementById("agentSelect").value =
    currentSession.agent_name || "antigravity-preview-09-2026";
  document.getElementById("modelSelect").value =
    currentSession.model_name || "gemini-3.8-flash";
  document.getElementById("prevInteractionIdInput").value =
    currentSession.last_interaction_id || "";
  document.getElementById("environmentIdInput").value =
    currentSession.environment_id || "";
  document.getElementById("maxTokensInput").value =
    currentSession.max_total_tokens || 0;
  document.getElementById("sysInstructionInput").value =
    currentSession.system_instruction || "";

  updateModeUI(currentSession.mode || "agent");
  renderSourcesList();
  renderUploadedFilesList();
  updateHeaderAndComposerBadges();
}

function setMode(mode) {
  updateModeUI(mode);
  saveSessionSettings(mode);
}

function updateModeUI(mode) {
  const agentBtn = document.getElementById("modeAgentBtn");
  const modelBtn = document.getElementById("modeModelBtn");
  const agentGroup = document.getElementById("agentConfigGroup");
  const envGroup = document.getElementById("envConfigGroup");
  const sourcesGroup = document.getElementById("sourcesConfigGroup");
  const budgetGroup = document.getElementById("budgetConfigGroup");
  const sysGroup = document.getElementById("sysInstructionGroup");
  const modelLabel = document.getElementById("modelSelectLabel");

  if (mode === "agent") {
    agentBtn.className =
      "py-1.5 rounded-md font-medium transition bg-studio-card text-blue-300 border border-studio-border";
    modelBtn.className =
      "py-1.5 rounded-md font-medium transition text-gray-400 hover:text-white";
    agentGroup.classList.remove("hidden");
    envGroup.classList.remove("hidden");
    if (sourcesGroup) sourcesGroup.classList.remove("hidden");
    budgetGroup.classList.remove("hidden");
    sysGroup.classList.add("hidden");
    modelLabel.textContent = "Modelo Subjacente (agent_config.model)";
  } else {
    modelBtn.className =
      "py-1.5 rounded-md font-medium transition bg-studio-card text-blue-300 border border-studio-border";
    agentBtn.className =
      "py-1.5 rounded-md font-medium transition text-gray-400 hover:text-white";
    agentGroup.classList.add("hidden");
    envGroup.classList.add("hidden");
    if (sourcesGroup) sourcesGroup.classList.add("hidden");
    budgetGroup.classList.add("hidden");
    sysGroup.classList.remove("hidden");
    modelLabel.textContent = "Modelo Gemini (model)";
  }
  if (currentSession) currentSession.mode = mode;
  updateHeaderAndComposerBadges();
}

function updateHeaderAndComposerBadges() {
  const prevId = document.getElementById("prevInteractionIdInput").value.trim();
  const envId = document.getElementById("environmentIdInput").value.trim();
  const mode = currentSession ? currentSession.mode : "agent";

  const headerPrev = document.getElementById("headerPrevId");
  const headerEnv = document.getElementById("headerEnvId");
  const composerPrev = document.getElementById("composerPrevBadge");
  const composerEnv = document.getElementById("composerEnvBadge");
  const chainDot = document.getElementById("chainDot");

  if (prevId) {
    headerPrev.textContent = prevId;
    composerPrev.textContent = `previous_interaction_id: ${prevId.slice(0, 22)}${prevId.length > 22 ? "..." : ""}`;
    chainDot.className = "w-2 h-2 rounded-full bg-emerald-400";
  } else {
    headerPrev.textContent = "Nenhum (1º turno)";
    composerPrev.textContent = "previous_interaction_id: Nenhum (1º Turno)";
    chainDot.className = "w-2 h-2 rounded-full bg-gray-500";
  }

  if (mode === "agent") {
    headerEnv.textContent = envId ? envId : "remote (novo)";
    composerEnv.textContent = `environment: ${envId ? envId : "remote"}`;
    composerEnv.classList.remove("hidden");
  } else {
    headerEnv.textContent = "N/A (Modo Model)";
    composerEnv.classList.add("hidden");
  }
}

async function saveSessionSettings(overrideMode = null) {
  if (!currentSession) return;
  const payload = {
    title: document.getElementById("sessionTitleInput").value.trim() || "Conversa Antigravity",
    mode: overrideMode || currentSession.mode || "agent",
    agent_name: document.getElementById("agentSelect").value,
    model_name: document.getElementById("modelSelect").value,
    last_interaction_id: document.getElementById("prevInteractionIdInput").value.trim(),
    environment_id: document.getElementById("environmentIdInput").value.trim(),
    max_total_tokens: parseInt(document.getElementById("maxTokensInput").value || "0", 10),
    system_instruction: document.getElementById("sysInstructionInput").value,
    sources: currentSession ? (currentSession.sources || []) : [],
    uploaded_files: currentSession ? (currentSession.uploaded_files || []) : [],
  };

  try {
    const res = await fetch(`/api/sessions/${currentSession.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      currentSession = await res.json();
      syncInputsFromSession();
      updateHeaderAndComposerBadges();
      renderChatTurns();
      await loadSessionsList();
    }
  } catch (err) {
    console.error("Erro ao salvar configurações da sessão:", err);
  }
}

async function onPrevInteractionIdChanged() {
  const prevId = document.getElementById("prevInteractionIdInput").value.trim();
  if (!prevId) {
    await saveSessionSettings();
    return;
  }
  const existingIds = new Set(((currentSession && currentSession.turns) || []).map((t) => t.interaction_id));
  if (existingIds.has(prevId)) {
    await saveSessionSettings();
    return;
  }
  await loadChainFromPrevIdInput();
}

async function loadChainFromPrevIdInput() {
  if (!currentSession) return;
  const prevId = document.getElementById("prevInteractionIdInput").value.trim();
  if (!prevId) {
    alert("Informe um previous_interaction_id (ex: v1_...) no campo para carregar a conversa anterior.");
    focusPrevIdInput();
    return;
  }

  const loadBtn = document.getElementById("loadPrevChainBtn");
  const oldBtnText = loadBtn ? loadBtn.textContent : "";
  if (loadBtn) {
    loadBtn.disabled = true;
    loadBtn.textContent = "⏳ Carregando...";
  }

  const container = document.getElementById("chatContainer");
  if (container && (!currentSession.turns || currentSession.turns.length === 0)) {
    container.innerHTML = `
      <div class="max-w-3xl mx-auto my-8 bg-studio-panel border border-blue-500/40 rounded-2xl p-6 text-xs text-blue-200 flex items-center gap-3">
        <span class="animate-spin text-base">🔄</span>
        <div>
          <div class="font-semibold text-white">Carregando conversa anterior na tela...</div>
          <div class="text-gray-400">Percorrendo a cadeia de <code>previous_interaction_id</code> a partir de <code class="text-blue-300">${escapeHtml(prevId)}</code></div>
        </div>
      </div>
    `;
  }

  try {
    const res = await fetch(`/api/sessions/${currentSession.id}/load-chain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ interaction_id: prevId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      await saveSessionSettings();
      throw new Error(data.detail || "Não foi possível recuperar os turnos anteriores na API.");
    }
    if (data.session) {
      currentSession = data.session;
      syncInputsFromSession();
      updateHeaderAndComposerBadges();
      renderChatTurns();
      await loadSessionsList();
    }
  } catch (err) {
    renderChatTurns();
    alert("Aviso ao carregar histórico do previous_interaction_id:\n\n" + err.message);
  } finally {
    if (loadBtn) {
      loadBtn.disabled = false;
      loadBtn.textContent = oldBtnText || "🔄 Carregar na tela";
    }
  }
}

function clearPrevInteractionId() {
  document.getElementById("prevInteractionIdInput").value = "";
  saveSessionSettings();
}

function resetEnvironmentId() {
  document.getElementById("environmentIdInput").value = "";
  saveSessionSettings();
}

function setMaxTokensPreset(val) {
  document.getElementById("maxTokensInput").value = val;
  saveSessionSettings();
}

function focusPrevIdInput() {
  const el = document.getElementById("prevInteractionIdInput");
  el.focus();
  el.select();
}

function renderChatTurns() {
  const container = document.getElementById("chatContainer");
  if (!currentSession) return;

  const turns = currentSession.turns || [];
  const activePrevId = document.getElementById("prevInteractionIdInput").value.trim();
  const activeEnvId = document.getElementById("environmentIdInput").value.trim();

  if (turns.length === 0) {
    container.innerHTML = `
      <div class="max-w-3xl mx-auto my-8 bg-studio-panel border border-studio-border rounded-2xl p-6 space-y-4">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-blue-500/20 border border-blue-400/40 flex items-center justify-center text-blue-300 font-bold">
            AG
          </div>
          <div>
            <h2 class="text-base font-semibold text-white">Playground Stateful para Antigravity & Gemini</h2>
            <p class="text-xs text-gray-400">Gerenciamento completo de <code class="text-blue-300">previous_interaction_id</code> e <code class="text-purple-300">environment_id</code></p>
          </div>
        </div>

        ${
          activePrevId
            ? `
          <div class="p-3.5 rounded-xl bg-blue-500/10 border border-blue-500/30 text-xs space-y-2">
            <div class="font-semibold text-blue-300">✅ Sessão vinculada a um Interaction ID anterior!</div>
            <div class="text-gray-300">Próxima mensagem continuará a partir de: <code class="font-mono text-blue-200">${escapeHtml(activePrevId)}</code></div>
            ${activeEnvId ? `<div class="text-gray-300">Reutilizando Sandbox Linux: <code class="font-mono text-purple-200">${escapeHtml(activeEnvId)}</code></div>` : ""}
            <div class="pt-1">
              <button
                onclick="loadChainFromPrevIdInput()"
                class="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold transition"
              >
                🔄 Carregar conversa anterior na tela
              </button>
            </div>
          </div>
        `
            : `
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
            <div class="p-3.5 rounded-xl bg-studio-card border border-studio-border space-y-1.5">
              <div class="font-semibold text-blue-300">🔗 Já tem um Interaction ID?</div>
              <p class="text-gray-400 leading-relaxed">
                Clique em <strong>"Importar / Continuar por ID"</strong> no topo para buscar todos os turnos anteriores na API usando <code>previous_interaction_id</code> ou cole o ID direto na barra lateral direita.
              </p>
              <button onclick="openImportModal()" class="mt-1 px-3 py-1.5 rounded-lg bg-blue-600/25 hover:bg-blue-600/40 text-blue-200 border border-blue-500/30 font-medium">
                Importar Conversa Agora →
              </button>
            </div>
            <div class="p-3.5 rounded-xl bg-studio-card border border-studio-border space-y-1.5">
              <div class="font-semibold text-purple-300">🐧 Como funciona o Estado aqui?</div>
              <p class="text-gray-400 leading-relaxed">
                Cada turno salva o <code>interaction_id</code> e o <code>environment_id</code> no banco SQLite local. Você pode fechar o navegador e retomar a conversa ou ramificar (Fork) de qualquer turno!
              </p>
            </div>
          </div>
        `
        }
      </div>
    `;
    return;
  }

  container.innerHTML = turns
    .map((turn, idx) => renderSingleTurnCard(turn, idx + 1))
    .join("");

  container.scrollTop = container.scrollHeight;
}

function renderSingleTurnCard(turn, turnNumber) {
  const status = turn.status || "completed";
  const statusColors = {
    completed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    in_progress: "bg-amber-500/15 text-amber-300 border-amber-500/30 animate-pulse",
    incomplete: "bg-orange-500/15 text-orange-300 border-orange-500/30",
    requires_action: "bg-blue-500/15 text-blue-300 border-blue-500/30",
    failed: "bg-red-500/15 text-red-300 border-red-500/30",
    cancelled: "bg-gray-500/15 text-gray-400 border-gray-500/30",
  };
  const badgeClass = statusColors[status] || statusColors.completed;

  const usage = turn.usage || {};
  const totalTokens = usage.total_tokens ? `${usage.total_tokens.toLocaleString()} tok` : "";
  const cachedTokens = usage.total_cached_tokens
    ? `(${usage.total_cached_tokens.toLocaleString()} em cache)`
    : "";

  // Filtra steps intermediários (pensamentos e chamadas de ferramentas)
  const steps = turn.steps || [];
  const thoughtSteps = [];
  const toolSteps = [];

  for (const s of steps) {
    if (!s || typeof s !== "object") continue;
    if (s.type === "thought") {
      const txt = (s.content || [])
        .filter((c) => c && c.type === "text")
        .map((c) => c.text || "")
        .join("\n")
        .trim();
      if (txt) thoughtSteps.push(txt);
    } else if (
      [
        "function_call",
        "function_result",
        "code_execution_call",
        "code_execution_result",
        "google_search_call",
        "google_search_result",
      ].includes(s.type)
    ) {
      toolSteps.push(s);
    }
  }

  const thoughtsHtml = thoughtSteps.length
    ? `
      <details class="bg-studio-bg/70 border border-studio-border rounded-xl px-3.5 py-2 text-xs">
        <summary class="cursor-pointer text-gray-300 font-medium flex items-center justify-between">
          <span>💭 Pensamentos do Modelo / Agente (${thoughtSteps.length} etapa${thoughtSteps.length > 1 ? "s" : ""}${usage.total_thought_tokens ? ` · ${usage.total_thought_tokens.toLocaleString()} thought tokens` : ""})</span>
          <span class="text-[11px] text-gray-500">Expandir</span>
        </summary>
        <div class="mt-2 pt-2 border-t border-studio-border space-y-2 text-gray-300 font-mono text-[11px] whitespace-pre-wrap leading-relaxed">${escapeHtml(thoughtSteps.join("\n\n---\n\n"))}</div>
      </details>
    `
    : "";

  const toolsHtml = toolSteps.length
    ? `
      <details class="bg-studio-bg/70 border border-studio-border rounded-xl px-3.5 py-2 text-xs">
        <summary class="cursor-pointer text-amber-300/90 font-medium flex items-center justify-between">
          <span>🔧 Execução de Ferramentas & Sandbox (${toolSteps.length} evento${toolSteps.length > 1 ? "s" : ""})</span>
          <span class="text-[11px] text-gray-500">Expandir</span>
        </summary>
        <div class="mt-2 pt-2 border-t border-studio-border space-y-2">
          ${toolSteps
            .map(
              (ts) => `
            <div class="bg-studio-card p-2.5 rounded-lg border border-studio-border font-mono text-[11px]">
              <div class="text-blue-300 font-semibold mb-1">${escapeHtml(ts.type)}${ts.name ? `: ${escapeHtml(ts.name)}` : ""}</div>
              <pre class="text-gray-300 overflow-x-auto whitespace-pre-wrap">${escapeHtml(JSON.stringify(ts.arguments || ts.result || ts, null, 2))}</pre>
            </div>
          `
            )
            .join("")}
        </div>
      </details>
    `
    : "";

  const renderedOutput = turn.output_text
    ? marked.parse(turn.output_text)
    : status === "in_progress"
    ? `<div class="text-amber-300 text-xs flex items-center gap-2">⏳ O agente Antigravity está executando esta tarefa no sandbox remoto...</div>`
    : `<div class="text-gray-500 italic text-xs">(Sem texto de saída final neste turno)</div>`;

  const incompleteBanner =
    status === "incomplete"
      ? `
      <div class="mt-3 p-3 rounded-xl bg-orange-500/15 border border-orange-500/40 flex items-center justify-between text-xs">
        <div class="text-orange-200">
          ⚠️ <strong>Interação pausada (status: incomplete)</strong> — O limite de <code>max_total_tokens</code> foi atingido. O estado e o ambiente estão preservados.
        </div>
        <button
          onclick="continueIncompleteTurn('${turn.interaction_id}', '${turn.environment_id || ""}')"
          class="px-3 py-1.5 rounded-lg bg-orange-500 hover:bg-orange-400 text-gray-950 font-semibold shrink-0 ml-3"
        >
          ▶️ Continuar de onde parou
        </button>
      </div>
    `
      : "";

  const inProgressBanner =
    status === "in_progress"
      ? `
      <div class="mt-3 p-3 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-between text-xs">
        <div class="text-amber-200">
          🔄 Execução em segundo plano em andamento (ID: <code>${escapeHtml(turn.interaction_id)}</code>)
        </div>
        <div class="flex items-center gap-2">
          <button
            onclick="pollTurnOnce('${currentSession.id}', '${turn.interaction_id}')"
            class="px-2.5 py-1 rounded bg-studio-card hover:bg-gray-700 text-gray-200 border border-studio-border"
          >
            ↻ Atualizar agora
          </button>
          <button
            onclick="cancelRunningTurn('${currentSession.id}', '${turn.interaction_id}', this)"
            class="px-2.5 py-1 rounded bg-red-500/20 hover:bg-red-500/30 text-red-300 border border-red-500/30 font-medium transition"
          >
            ⏹ Cancelar
          </button>
        </div>
      </div>
    `
      : "";

  return `
    <div class="max-w-4xl mx-auto bg-studio-panel border border-studio-border rounded-2xl overflow-hidden shadow-lg">
      <!-- Turn Metadata Header -->
      <div class="px-4 py-2.5 bg-studio-card/70 border-b border-studio-border flex flex-wrap items-center justify-between gap-2 text-[11px]">
        <div class="flex flex-wrap items-center gap-2">
          <span class="font-bold text-white bg-blue-500/20 border border-blue-500/40 px-2 py-0.5 rounded">Turno #${turnNumber}</span>
          <span class="px-2 py-0.5 rounded border ${badgeClass} font-mono">${escapeHtml(status)}</span>
          <span class="text-gray-400 font-mono" title="ID desta interação">
            id: <code class="text-blue-300">${escapeHtml(turn.interaction_id)}</code>
            <button onclick="navigator.clipboard.writeText('${turn.interaction_id}')" class="hover:text-white ml-0.5" title="Copiar ID">📋</button>
          </span>
          ${
            turn.previous_interaction_id
              ? `<span class="text-gray-500 font-mono" title="Encadeado a partir deste previous_interaction_id">← prev: <code class="text-gray-300">${escapeHtml(turn.previous_interaction_id.slice(0, 16))}...</code></span>`
              : `<span class="text-gray-500 font-mono">(Raiz da conversa)</span>`
          }
          ${
            turn.environment_id
              ? `<span class="text-purple-300 font-mono bg-purple-500/10 border border-purple-500/30 px-1.5 py-0.5 rounded">env: ${escapeHtml(turn.environment_id)}</span>`
              : ""
          }
        </div>

        <div class="flex items-center gap-2">
          ${
            totalTokens
              ? `<span class="text-gray-400 font-mono">${totalTokens} <span class="text-emerald-400">${cachedTokens}</span></span>`
              : ""
          }
          <button
            onclick="useTurnAsPreviousId('${turn.interaction_id}', '${turn.environment_id || ""}')"
            class="px-2 py-0.5 rounded bg-studio-bg hover:bg-gray-800 text-blue-300 border border-studio-border transition"
            title="Definir este turno como o previous_interaction_id ativo"
          >
            🎯 Apontar ID aqui
          </button>
          <button
            onclick="forkFromTurn('${turn.interaction_id}')"
            class="px-2 py-0.5 rounded bg-studio-bg hover:bg-gray-800 text-emerald-300 border border-studio-border transition"
            title="Criar uma nova sessão ramificando a conversa a partir deste turno"
          >
            🌿 Ramificar (Fork)
          </button>
        </div>
      </div>

      <!-- Turn Content Body -->
      <div class="p-4 space-y-4">
        <!-- User Input -->
        <div class="bg-studio-card/90 border border-studio-border rounded-xl p-3.5">
          <div class="text-[11px] font-semibold uppercase tracking-wider text-blue-300 mb-1">👤 Usuário (User Input)</div>
          <div class="text-sm text-gray-100 whitespace-pre-wrap">${escapeHtml(turn.user_input_text || "(Input não retornado)")}</div>
        </div>

        <!-- Intermediate Steps: Thoughts & Tools -->
        ${thoughtsHtml}
        ${toolsHtml}

        <!-- Final Model/Agent Output -->
        <div class="bg-studio-bg/50 border border-studio-border rounded-xl p-4">
          <div class="text-[11px] font-semibold uppercase tracking-wider text-emerald-300 mb-2">🤖 Resposta do Antigravity / Gemini</div>
          <div class="prose-studio text-sm text-gray-100 leading-relaxed overflow-x-auto">${renderedOutput}</div>
          ${incompleteBanner}
          ${inProgressBanner}
        </div>
      </div>
    </div>
  `;
}

async function sendPrompt(overridePrompt = null) {
  if (!currentSession) return;
  const promptEl = document.getElementById("promptInput");
  const promptText = overridePrompt !== null ? overridePrompt : promptEl.value.trim();
  if (!promptText) return;

  const sendBtn = document.getElementById("sendBtn");
  const sendBtnText = document.getElementById("sendBtnText");
  sendBtn.disabled = true;
  sendBtnText.textContent = "Executando...";

  if (overridePrompt === null) {
    promptEl.value = "";
  }

  const body = {
    prompt: promptText,
    mode: currentSession.mode || "agent",
    agent_name: document.getElementById("agentSelect").value,
    model_name: document.getElementById("modelSelect").value,
    previous_interaction_id: document.getElementById("prevInteractionIdInput").value.trim(),
    environment_id: document.getElementById("environmentIdInput").value.trim(),
    max_total_tokens: parseInt(document.getElementById("maxTokensInput").value || "0", 10),
    system_instruction: document.getElementById("sysInstructionInput").value,
    background: document.getElementById("backgroundToggle").checked,
    sources: currentSession ? (currentSession.sources || []) : [],
  };

  try {
    const res = await fetchWithRetry(`/api/sessions/${currentSession.id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }, 3);
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Erro ao executar interação.");
    }

    currentSession = data.session;
    syncInputsFromSession();
    await loadSessionsList();
    renderChatTurns();

    const latest = data.latest_interaction || {};
    if (latest.status === "in_progress") {
      startPollingTurn(currentSession.id, latest.id);
    }
  } catch (err) {
    alert("Erro na chamada da Interactions API:\n\n" + err.message);
    if (overridePrompt === null) {
      promptEl.value = promptText;
    }
  } finally {
    sendBtn.disabled = false;
    sendBtnText.textContent = "Run";
  }
}

function continueIncompleteTurn(interactionId, environmentId) {
  document.getElementById("prevInteractionIdInput").value = interactionId;
  if (environmentId) {
    document.getElementById("environmentIdInput").value = environmentId;
  }
  updateHeaderAndComposerBadges();
  sendPrompt("continue");
}

function useTurnAsPreviousId(interactionId, environmentId) {
  document.getElementById("prevInteractionIdInput").value = interactionId;
  if (environmentId) {
    document.getElementById("environmentIdInput").value = environmentId;
  }
  saveSessionSettings();
}

async function forkFromTurn(interactionId) {
  if (!currentSession) return;
  try {
    const res = await fetch(`/api/sessions/${currentSession.id}/fork`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ up_to_interaction_id: interactionId }),
    });
    const forked = await res.json();
    if (!res.ok) throw new Error(forked.detail || "Erro ao criar Fork.");
    await loadSessionsList();
    await selectSession(forked.id);
  } catch (err) {
    alert("Erro ao ramificar conversa: " + err.message);
  }
}

function startPollingTurn(sessionId, interactionId) {
  stopPolling();
  activePollTimer = setInterval(async () => {
    await pollTurnOnce(sessionId, interactionId);
  }, 4000);
}

function stopPolling() {
  if (activePollTimer) {
    clearInterval(activePollTimer);
    activePollTimer = null;
  }
}

async function pollTurnOnce(sessionId, interactionId) {
  try {
    const res = await fetchWithRetry(`/api/sessions/${sessionId}/turns/${interactionId}/poll`, {}, 2);
    if (!res.ok) return;
    const data = await res.json();
    currentSession = data.session;
    syncInputsFromSession();
    renderChatTurns();
    if (data.latest_interaction && data.latest_interaction.status !== "in_progress") {
      stopPolling();
      await loadSessionsList();
    }
  } catch (err) {
    console.error("Erro no polling:", err);
  }
}

async function cancelRunningTurn(sessionId, interactionId, btnEl = null) {
  stopPolling();
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.innerHTML = "⏳ Cancelando...";
  }
  try {
    const res = await fetchWithRetry(`/api/sessions/${sessionId}/turns/${interactionId}/cancel`, {
      method: "POST",
    }, 2);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    stopPolling();
    if (data.session) {
      currentSession = data.session;
      syncInputsFromSession();
      renderChatTurns();
      await loadSessionsList();
    }
  } catch (err) {
    alert("Erro ao cancelar execução: " + err.message);
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.innerHTML = "⏹ Cancelar";
    }
  }
}

// MODAL IMPORTAÇÃO POR INTERACTION_ID
function openImportModal() {
  document.getElementById("importErrorBox").classList.add("hidden");
  document.getElementById("importModal").classList.remove("hidden");
  document.getElementById("importModal").classList.add("flex");
  document.getElementById("importInteractionId").focus();
}

function closeImportModal() {
  document.getElementById("importModal").classList.add("hidden");
  document.getElementById("importModal").classList.remove("flex");
}

async function submitImportChain() {
  const interactionId = document.getElementById("importInteractionId").value.trim();
  const environmentId = document.getElementById("importEnvironmentId").value.trim();
  const title = document.getElementById("importTitle").value.trim();
  const modeRadio = document.querySelector('input[name="importModeRadio"]:checked').value;
  const errBox = document.getElementById("importErrorBox");
  const btn = document.getElementById("confirmImportBtn");

  if (!interactionId) {
    errBox.textContent = "Informe o Interaction ID (v1_...).";
    errBox.classList.remove("hidden");
    return;
  }

  errBox.classList.add("hidden");
  btn.disabled = true;
  btn.textContent = "Recuperando na API...";

  try {
    const res = await fetch("/api/sessions/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        interaction_id: interactionId,
        environment_id: environmentId,
        title: title,
        mode: currentSession ? currentSession.mode : "agent",
        agent_name: document.getElementById("agentSelect").value,
        model_name: document.getElementById("modelSelect").value,
        fetch_history: modeRadio === "fetch",
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Falha ao importar conversa.");
    }

    closeImportModal();
    document.getElementById("importInteractionId").value = "";
    document.getElementById("importEnvironmentId").value = "";
    document.getElementById("importTitle").value = "";

    await loadSessionsList();
    await selectSession(data.session.id);
  } catch (err) {
    errBox.textContent = err.message;
    errBox.classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Importar Conversa";
  }
}

// MODAL GET CODE
function openCodeModal() {
  const prevId = document.getElementById("prevInteractionIdInput").value.trim();
  const envId = document.getElementById("environmentIdInput").value.trim() || "remote";
  const agent = document.getElementById("agentSelect").value;
  const model = document.getElementById("modelSelect").value;
  const mode = currentSession ? currentSession.mode : "agent";

  let pyCode = "";
  let curlCode = "";

  const sources = currentSession ? (currentSession.sources || []) : [];
  let pyEnvStr = `"${envId}"`;
  let curlEnvStr = `"${envId}"`;

  if (!prevId && (envId === "remote" || !envId) && sources.length > 0) {
    pyEnvStr = JSON.stringify({ type: "remote", sources: sources }, null, 4).replace(/\n/g, "\n    ");
    curlEnvStr = JSON.stringify({ type: "remote", sources: sources }, null, 4).replace(/\n/g, "\n    ");
  }

  if (mode === "agent") {
    pyCode = `from google import genai

client = genai.Client()

interaction = client.interactions.create(
    agent="${agent}",
    input="Sua próxima instrução aqui...",${prevId ? `\n    previous_interaction_id="${prevId}",` : ""}
    environment=${pyEnvStr},
    agent_config={
        "type": "antigravity",
        "model": "${model}",
    },
)

print("Novo Interaction ID:", interaction.id)
print("Environment ID:", interaction.environment_id)
print(interaction.output_text)`;

    curlCode = `curl -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" \\
  -H "Content-Type: application/json" \\
  -H "x-goog-api-key: $GEMINI_API_KEY" \\
  -d '{
    "agent": "${agent}",
    "input": "Sua próxima instrução aqui...",${prevId ? `\n    "previous_interaction_id": "${prevId}",` : ""}
    "environment": ${curlEnvStr},
    "agent_config": {
      "type": "antigravity",
      "model": "${model}"
    }
  }'`;
  } else {
    pyCode = `from google import genai

client = genai.Client()

interaction = client.interactions.create(
    model="${model}",
    input="Sua próxima mensagem aqui...",${prevId ? `\n    previous_interaction_id="${prevId}",` : ""}
)

print("Novo Interaction ID:", interaction.id)
print(interaction.output_text)`;

    curlCode = `curl -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" \\
  -H "Content-Type: application/json" \\
  -H "x-goog-api-key: $GEMINI_API_KEY" \\
  -d '{
    "model": "${model}",
    "input": "Sua próxima mensagem aqui..."${prevId ? `,\n    "previous_interaction_id": "${prevId}"` : ""}
  }'`;
  }

  const cleanEnv = (!envId || envId === "remote") ? "<ENVIRONMENT_ID>" : envId.replace(/^environments\//, "");
  const uploadedFiles = currentSession ? (currentSession.uploaded_files || []) : [];
  const lastUploaded = uploadedFiles.length > 0 ? uploadedFiles[uploadedFiles.length - 1] : null;
  const sampleTarget = lastUploaded ? (lastUploaded.target_path || "/workspace/project") : "/workspace/project";
  const sampleRel = sampleTarget.replace(/^\/+/, "");

  const envUploadCode = `# --- Python SDK (google-genai) ---
from google import genai

client = genai.Client()
env_id = "${cleanEnv}"  # ou env = client.environments.create(); env_id = env.environment_id

# 1) Upload de arquivo único (sobrescrevendo se existir)
client.environments.files.upload(
    environment=env_id,
    path="${sampleRel}/main.py",
    file="local_dir/main.py",
    overwrite=True,
)

# 2) Upload de pasta compactada (.tar.gz) com extração automática no sandbox
client.environments.files.upload(
    environment=env_id,
    path="${sampleRel}",
    file="project.tar.gz",
    extract=True,
    overwrite=True,
)

# --- REST / cURL (Environments Upload API) ---
# Upload simples:
curl -X PUT \\
  "https://generativelanguage.googleapis.com/upload/v1beta/environments/${cleanEnv}/files/${sampleRel}/main.py?overwrite=true" \\
  -H "x-goog-api-key: $GEMINI_API_KEY" \\
  -H "Content-Type: text/x-python" \\
  --data-binary @local_dir/main.py

# Upload de arquivo .tar.gz com extração automática (extract=true):
curl -X PUT \\
  "https://generativelanguage.googleapis.com/upload/v1beta/environments/${cleanEnv}/files/${sampleRel}?extract=true&overwrite=true" \\
  -H "x-goog-api-key: $GEMINI_API_KEY" \\
  -H "Content-Type: application/gzip" \\
  --data-binary @project.tar.gz`;

  document.getElementById("pythonCodeSnippet").textContent = pyCode;
  document.getElementById("curlCodeSnippet").textContent = curlCode;
  const envUploadEl = document.getElementById("envUploadCodeSnippet");
  if (envUploadEl) {
    envUploadEl.textContent = envUploadCode;
  }
  document.getElementById("codeModal").classList.remove("hidden");
  document.getElementById("codeModal").classList.add("flex");
}

function closeCodeModal() {
  document.getElementById("codeModal").classList.add("hidden");
  document.getElementById("codeModal").classList.remove("flex");
}

// MODAL API KEY
function openApiKeyModal() {
  document.getElementById("apiKeyModal").classList.remove("hidden");
  document.getElementById("apiKeyModal").classList.add("flex");
  document.getElementById("apiKeyInput").focus();
}

function closeApiKeyModal() {
  document.getElementById("apiKeyModal").classList.add("hidden");
  document.getElementById("apiKeyModal").classList.remove("flex");
}

async function saveApiKey() {
  const key = document.getElementById("apiKeyInput").value.trim();
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: key }),
  });
  document.getElementById("apiKeyInput").value = "";
  closeApiKeyModal();
  await checkApiKeyStatus();
}

function exportCurrentSessionMarkdown() {
  if (!currentSession) return;
  window.open(`/api/sessions/${currentSession.id}/export/markdown`, "_blank");
}

async function downloadActiveEnvironmentSnapshot() {
  const envInputVal = (document.getElementById("environmentIdInput").value || "").trim();
  const sessionEnvVal = currentSession ? (currentSession.environment_id || "").trim() : "";
  const targetEnv = envInputVal || sessionEnvVal;

  if (!targetEnv || targetEnv === "remote") {
    alert(
      "⚠️ Nenhum ambiente remoto ativo nesta sessão ainda.\n\n" +
      "Envie uma mensagem primeiro ou clique em '⚡ Provisionar' / '⬆️ Upload Ambiente' para criar o container sandbox ('environment_id'). " +
      "Assim que o ambiente for provisionado, o botão Download baixará o snapshot compactado (.tar.gz) com todos os arquivos do ambiente."
    );
    return;
  }

  const btnSidebar = document.getElementById("sidebarDownloadEnvBtn");
  const btnHeader = document.getElementById("headerDownloadEnvBtn");
  const originalSidebarText = btnSidebar ? btnSidebar.innerHTML : "";
  const originalHeaderText = btnHeader ? btnHeader.innerHTML : "";

  if (btnSidebar) btnSidebar.innerHTML = '<span>⏳</span><span>Baixando snapshot...</span>';
  if (btnHeader) btnHeader.innerHTML = '<span>⏳</span><span>Baixando...</span>';

  try {
    const url = (currentSession && currentSession.id && currentSession.environment_id === targetEnv)
      ? `/api/sessions/${currentSession.id}/environment/download`
      : `/api/environments/${encodeURIComponent(targetEnv)}/download`;

    const resp = await fetch(url);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: "Erro desconhecido ao baixar snapshot." }));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    const blob = await resp.blob();
    const disposition = resp.headers.get("content-disposition");
    let filename = `environment_snapshot_${targetEnv}.tar.gz`;
    if (disposition && disposition.includes("filename=")) {
      const match = disposition.match(/filename=["']?([^"';]+)["']?/);
      if (match && match[1]) {
        filename = match[1].trim();
      }
    }

    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(downloadUrl);
  } catch (err) {
    alert(`❌ Falha ao baixar snapshot do ambiente:\n${err.message}`);
  } finally {
    if (btnSidebar) btnSidebar.innerHTML = originalSidebarText;
    if (btnHeader) btnHeader.innerHTML = originalHeaderText;
  }
}

function copyActivePrevId() {
  const val = document.getElementById("prevInteractionIdInput").value.trim();
  if (!val) return;
  navigator.clipboard.writeText(val);
}

function copyActiveEnvId() {
  const val = document.getElementById("environmentIdInput").value.trim();
  if (!val) return;
  navigator.clipboard.writeText(val);
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// GERENCIAMENTO DE SOURCES (REPOSITÓRIOS GITHUB / GCS / INLINE)
function renderSourcesList() {
  const container = document.getElementById("sourcesList");
  if (!container) return;
  const sources = (currentSession && currentSession.sources) ? currentSession.sources : [];

  if (sources.length === 0) {
    container.innerHTML = `
      <div id="sourcesEmptyState" class="text-[11px] text-gray-500 italic py-2 text-center bg-studio-bg/60 rounded-lg border border-dashed border-studio-border">
        Nenhuma fonte configurada. Clique em "＋ Adicionar Fonte" para clonar um repositório GitHub ou use "⬆️ Upload Direto ao Ambiente".
      </div>
    `;
    return;
  }

  container.innerHTML = sources
    .map((s, idx) => {
      let icon = "📦";
      let title = s.source || "Fonte";
      if (s.type === "gcs") {
        icon = "☁️";
      } else if (s.type === "inline") {
        icon = "📝";
        title = `Inline: ${s.target || "arquivo"}`;
      }

      return `
        <div class="flex items-center justify-between gap-2 bg-studio-bg p-2 rounded-lg border border-studio-border text-xs group hover:border-emerald-500/40 transition">
          <div class="flex items-center gap-2 min-w-0">
            <span class="text-sm shrink-0">${icon}</span>
            <div class="min-w-0">
              <div class="font-medium text-emerald-300 truncate text-[11px]" title="${escapeHtml(title)}">
                ${escapeHtml(title)}
              </div>
              <div class="font-mono text-[10px] text-gray-400 truncate">
                ➜ ${escapeHtml(s.target || "/workspace")}
              </div>
            </div>
          </div>
          <button
            onclick="removeSource(${idx})"
            class="text-gray-500 hover:text-red-400 p-1 rounded transition shrink-0"
            title="Remover esta fonte"
          >
            ✕
          </button>
        </div>
      `;
    })
    .join("");
}

function openAddSourceModal() {
  document.getElementById("sourceTypeSelect").value = "repository";
  document.getElementById("sourceUrlInput").value = "";
  document.getElementById("sourceContentInput").value = "";
  document.getElementById("sourceTargetInput").value = "/workspace";
  document.getElementById("addSourceErrorBox").classList.add("hidden");
  onSourceTypeChanged();

  const modal = document.getElementById("addSourceModal");
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  document.getElementById("sourceUrlInput").focus();
}

function closeAddSourceModal() {
  const modal = document.getElementById("addSourceModal");
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}

function onSourceTypeChanged() {
  const type = document.getElementById("sourceTypeSelect").value;
  const urlGroup = document.getElementById("sourceUrlFieldGroup");
  const contentGroup = document.getElementById("sourceContentFieldGroup");
  const urlLabel = document.getElementById("sourceUrlLabel");
  const urlInput = document.getElementById("sourceUrlInput");

  if (type === "direct_upload") {
    closeAddSourceModal();
    openUploadEnvModal("folder");
    return;
  }

  if (type === "repository") {
    urlGroup.classList.remove("hidden");
    contentGroup.classList.add("hidden");
    urlLabel.textContent = "URL do Repositório Git *";
    urlInput.placeholder = "https://github.com/usuario/meu-projeto";
  } else if (type === "gcs") {
    urlGroup.classList.remove("hidden");
    contentGroup.classList.add("hidden");
    urlLabel.textContent = "Caminho Google Cloud Storage (GCS) *";
    urlInput.placeholder = "gs://cloud-samples-data/bigquery/us-states/";
  } else if (type === "inline") {
    urlGroup.classList.add("hidden");
    contentGroup.classList.remove("hidden");
    document.getElementById("sourceTargetInput").placeholder = "/workspace/notes.txt";
  }
}

function onSourceUrlInput(val) {
  const type = document.getElementById("sourceTypeSelect").value;
  if (type !== "repository") return;
  const targetEl = document.getElementById("sourceTargetInput");
  const trimmed = (val || "").trim();
  if (!trimmed) return;

  const cleanUrl = trimmed.replace(/\.git$/i, "").replace(/\/+$/, "");
  const parts = cleanUrl.split("/");
  if (parts.length >= 2) {
    const repoName = parts[parts.length - 1];
    if (repoName && !repoName.startsWith("http")) {
      targetEl.value = `/workspace/${repoName}`;
    }
  }
}

async function submitAddSource() {
  const errBox = document.getElementById("addSourceErrorBox");
  errBox.classList.add("hidden");

  const type = document.getElementById("sourceTypeSelect").value;
  if (type === "direct_upload") {
    closeAddSourceModal();
    openUploadEnvModal("folder");
    return;
  }

  const target = document.getElementById("sourceTargetInput").value.trim();

  if (!target) {
    errBox.textContent = "O caminho de destino (target) no contêiner é obrigatório.";
    errBox.classList.remove("hidden");
    return;
  }
  if (!target.startsWith("/")) {
    errBox.textContent = "O target deve ser um caminho absoluto (ex: /workspace/meu-projeto).";
    errBox.classList.remove("hidden");
    return;
  }
  if (target === "/" || target === "") {
    errBox.textContent = "Não é permitido montar diretamente na raiz '/'. Especifique um subdiretório como /workspace/projeto.";
    errBox.classList.remove("hidden");
    return;
  }

  let sourceObj = { type, target };

  if (type === "repository" || type === "gcs") {
    const url = document.getElementById("sourceUrlInput").value.trim();
    if (!url) {
      errBox.textContent = type === "repository"
        ? "Informe a URL do repositório Git (ex: https://github.com/octocat/Spoon-Knife)."
        : "Informe a URI do Cloud Storage (ex: gs://bucket/path).";
      errBox.classList.remove("hidden");
      return;
    }
    sourceObj.source = url;
  } else if (type === "inline") {
    const content = document.getElementById("sourceContentInput").value;
    sourceObj.content = content;
  }

  if (!currentSession.sources) {
    currentSession.sources = [];
  }
  currentSession.sources.push(sourceObj);

  closeAddSourceModal();
  renderSourcesList();
  await saveSessionSettings();
}

async function removeSource(index) {
  if (!currentSession || !currentSession.sources) return;
  currentSession.sources.splice(index, 1);
  renderSourcesList();
  await saveSessionSettings();
}

// ============================================================================
// UPLOAD DIRETO VIA API DE AMBIENTES (ENVIRONMENTS API)
// PUT /upload/v1beta/environments/{environment_id}/files/{path}
// ============================================================================

let currentUploadModalMode = "single"; // 'single' | 'folder' | 'archive'
let selectedUploadFiles = [];

function formatBytesSize(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function renderUploadedFilesList() {
  const group = document.getElementById("uploadedFilesGroup");
  const container = document.getElementById("uploadedFilesList");
  const countBadge = document.getElementById("uploadedFilesCountBadge");
  if (!group || !container) return;

  const uploaded = (currentSession && Array.isArray(currentSession.uploaded_files))
    ? currentSession.uploaded_files
    : [];

  if (uploaded.length === 0) {
    group.classList.add("hidden");
    container.innerHTML = "";
    if (countBadge) countBadge.textContent = "0";
    return;
  }

  group.classList.remove("hidden");
  if (countBadge) countBadge.textContent = String(uploaded.length);

  container.innerHTML = uploaded
    .map((item, idx) => {
      const icon = item.extract ? "📦" : "📄";
      const label = item.summary || item.target_path || "Upload";
      const targetPath = item.target_path || "/workspace";
      const sizeStr = formatBytesSize(item.size_bytes || 0);
      const flags = [];
      if (item.extract) flags.push("extract=true");
      if (item.overwrite) flags.push("overwrite=true");
      if (item.resumable) flags.push("resumable");

      return `
        <div class="flex items-center justify-between gap-2 bg-studio-bg p-2 rounded-lg border border-blue-500/30 text-xs group hover:border-blue-400/60 transition">
          <div class="flex items-center gap-2 min-w-0">
            <span class="text-sm shrink-0">${icon}</span>
            <div class="min-w-0">
              <div class="font-medium text-blue-300 truncate text-[11px]" title="${escapeHtml(label)}">
                ${escapeHtml(label)} <span class="text-[10px] text-gray-500">(${sizeStr})</span>
              </div>
              <div class="font-mono text-[10px] text-gray-400 truncate" title="${escapeHtml(targetPath)}">
                ➜ ${escapeHtml(targetPath)} ${flags.length ? `<span class="text-emerald-400/80">[${flags.join(", ")}]</span>` : ""}
              </div>
            </div>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            <button
              onclick='downloadSpecificEnvFile(${JSON.stringify(targetPath)}, this)'
              class="text-blue-300 hover:text-white bg-blue-500/15 hover:bg-blue-500/30 border border-blue-500/30 px-1.5 py-0.5 rounded text-[10px] transition"
              title="Baixar este arquivo/pasta do ambiente"
            >
              ⬇️
            </button>
            <button
              onclick="removeUploadedFileRecord(${idx})"
              class="text-gray-500 hover:text-red-400 p-1 rounded transition"
              title="Remover registro da lista"
            >
              ✕
            </button>
          </div>
        </div>
      `;
    })
    .join("");
}

async function removeUploadedFileRecord(index) {
  if (!currentSession || !Array.isArray(currentSession.uploaded_files)) return;
  currentSession.uploaded_files.splice(index, 1);
  renderUploadedFilesList();
  await saveSessionSettings();
}

async function provisionSessionEnvironmentNow() {
  if (!currentSession) return;
  const envInput = document.getElementById("environmentIdInput");
  const currentEnv = (envInput.value || "").trim();
  if (currentEnv && currentEnv !== "remote") {
    alert(`✅ Esta sessão já possui um ambiente provisionado:\n\n${currentEnv}\n\nSe quiser criar outro do zero, clique primeiro em 'Resetar' ao lado do Environment ID.`);
    return;
  }

  try {
    const res = await fetch(`/api/sessions/${currentSession.id}/environment/provision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sources: currentSession.sources || [],
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }

    currentSession = data.session || currentSession;
    syncInputsFromSession();
    await loadSessionsList();
    alert(`✅ Ambiente remoto provisionado com sucesso via Environments API!\n\nenvironment_id: ${data.environment_id}`);
  } catch (err) {
    alert(`❌ Falha ao provisionar ambiente remoto:\n${err.message}`);
  }
}

function openUploadEnvModal(initialMode = "single") {
  selectedUploadFiles = [];
  const fileSingle = document.getElementById("uploadSingleFileInput");
  const fileFolder = document.getElementById("uploadFolderInput");
  const fileArchive = document.getElementById("uploadArchiveInput");
  if (fileSingle) fileSingle.value = "";
  if (fileFolder) fileFolder.value = "";
  if (fileArchive) fileArchive.value = "";

  const errBox = document.getElementById("uploadEnvErrorBox");
  const okBox = document.getElementById("uploadEnvSuccessBox");
  if (errBox) errBox.classList.add("hidden");
  if (okBox) okBox.classList.add("hidden");

  const envId = (document.getElementById("environmentIdInput").value || "").trim();
  const badge = document.getElementById("uploadModalEnvBadge");
  if (badge) {
    if (envId && envId !== "remote") {
      badge.textContent = envId;
      badge.className = "font-mono text-xs text-blue-300 bg-blue-950/60 px-2.5 py-1 rounded border border-blue-500/30";
    } else {
      badge.textContent = "remote (será provisionado automaticamente antes do upload)";
      badge.className = "font-mono text-xs text-amber-300 bg-amber-950/60 px-2.5 py-1 rounded border border-amber-500/30";
    }
  }

  setUploadModalMode(initialMode || "single");

  const modal = document.getElementById("uploadEnvModal");
  modal.classList.remove("hidden");
  modal.classList.add("flex");
}

function closeUploadEnvModal() {
  const modal = document.getElementById("uploadEnvModal");
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}

function setUploadModalMode(mode) {
  currentUploadModalMode = mode;
  selectedUploadFiles = [];

  const tabSingle = document.getElementById("uploadTabSingle");
  const tabFolder = document.getElementById("uploadTabFolder");
  const tabArchive = document.getElementById("uploadTabArchive");

  const activeCls = "py-2 px-3 rounded-lg border border-blue-500/50 bg-blue-600/20 text-blue-300 font-semibold transition flex flex-col items-center gap-0.5";
  const inactiveCls = "py-2 px-3 rounded-lg border border-studio-border bg-studio-bg text-gray-400 hover:text-white transition flex flex-col items-center gap-0.5";

  if (tabSingle) tabSingle.className = mode === "single" ? activeCls : inactiveCls;
  if (tabFolder) tabFolder.className = mode === "folder" ? activeCls : inactiveCls;
  if (tabArchive) tabArchive.className = mode === "archive" ? activeCls : inactiveCls;

  const pickerIcon = document.getElementById("uploadPickerIcon");
  const pickerTitle = document.getElementById("uploadPickerTitle");
  const pickerSub = document.getElementById("uploadPickerSubtitle");
  const targetInput = document.getElementById("uploadTargetPathInput");
  const targetHint = document.getElementById("uploadTargetHint");
  const extractCheck = document.getElementById("uploadExtractCheckbox");
  const selectedBox = document.getElementById("uploadSelectedFilesSummary");

  if (selectedBox) selectedBox.classList.add("hidden");

  if (mode === "single") {
    if (pickerIcon) pickerIcon.textContent = "📄";
    if (pickerTitle) pickerTitle.textContent = "Clique para selecionar um ou mais arquivos";
    if (pickerSub) pickerSub.textContent = "Se escolher 1 arquivo, faz PUT direto no caminho destino. Se escolher vários, empacota e extrai.";
    if (targetInput) targetInput.value = "/workspace/main.py";
    if (targetHint) targetHint.textContent = "Para 1 arquivo, informe o caminho completo (ex: /workspace/main.py) ou diretório (/workspace/).";
    if (extractCheck) extractCheck.checked = false;
  } else if (mode === "folder") {
    if (pickerIcon) pickerIcon.textContent = "📁";
    if (pickerTitle) pickerTitle.textContent = "Clique para selecionar uma pasta local inteira";
    if (pickerSub) pickerSub.textContent = "O backend empacota automaticamente a pasta em .tar.gz em memória e envia com extract=true.";
    if (targetInput) targetInput.value = "/workspace/project";
    if (targetHint) targetHint.textContent = "Diretório de destino onde os arquivos da pasta serão extraídos no sandbox.";
    if (extractCheck) extractCheck.checked = true;
  } else if (mode === "archive") {
    if (pickerIcon) pickerIcon.textContent = "📦";
    if (pickerTitle) pickerTitle.textContent = "Clique para selecionar um arquivo .tar.gz ou .tar";
    if (pickerSub) pickerSub.textContent = "Envia o pacote compactado e extrai automaticamente no destino (extract=true).";
    if (targetInput) targetInput.value = "/workspace/project";
    if (targetHint) targetHint.textContent = "Diretório onde o conteúdo do .tar.gz será extraído.";
    if (extractCheck) extractCheck.checked = true;
  }

  updateUploadModalCodePreview();
}

function triggerUploadFilePicker() {
  if (currentUploadModalMode === "folder") {
    document.getElementById("uploadFolderInput").click();
  } else if (currentUploadModalMode === "archive") {
    document.getElementById("uploadArchiveInput").click();
  } else {
    document.getElementById("uploadSingleFileInput").click();
  }
}

function onUploadFilesSelected(inputElOrFiles) {
  const rawList = (inputElOrFiles && inputElOrFiles.files) ? inputElOrFiles.files : inputElOrFiles;
  const files = Array.from(rawList || []);
  selectedUploadFiles = files;

  const summaryBox = document.getElementById("uploadSelectedFilesSummary");
  const countText = document.getElementById("uploadSelectedCountText");
  const sizeText = document.getElementById("uploadSelectedSizeText");
  const listPreview = document.getElementById("uploadSelectedListPreview");
  const targetInput = document.getElementById("uploadTargetPathInput");
  const extractCheck = document.getElementById("uploadExtractCheckbox");

  if (files.length === 0) {
    if (summaryBox) summaryBox.classList.add("hidden");
    updateUploadModalCodePreview();
    return;
  }

  const totalBytes = files.reduce((acc, f) => acc + (f.size || 0), 0);
  if (summaryBox) summaryBox.classList.remove("hidden");
  if (countText) countText.textContent = `${files.length} arquivo(s) selecionado(s)`;
  if (sizeText) sizeText.textContent = formatBytesSize(totalBytes);

  if (listPreview) {
    const previewItems = files.slice(0, 15).map((f) => {
      const rel = f.webkitRelativePath || f.name;
      return `• ${escapeHtml(rel)} (${formatBytesSize(f.size)})`;
    });
    if (files.length > 15) {
      previewItems.push(`... e mais ${files.length - 15} arquivo(s)`);
    }
    listPreview.innerHTML = previewItems.join("<br/>");
  }

  if (currentUploadModalMode === "single" && files.length === 1) {
    const fname = files[0].name;
    const isArchive = /\.(tar\.gz|tgz|tar)$/i.test(fname);
    if (isArchive) {
      if (extractCheck) extractCheck.checked = true;
      const baseName = fname.replace(/\.(tar\.gz|tgz|tar)$/i, "");
      if (targetInput) targetInput.value = `/workspace/${baseName}`;
    } else {
      if (extractCheck) extractCheck.checked = false;
      if (targetInput) targetInput.value = `/workspace/${fname}`;
    }
  } else if (currentUploadModalMode === "single" && files.length > 1) {
    if (extractCheck) extractCheck.checked = true;
    if (targetInput && targetInput.value.endsWith(".py")) {
      targetInput.value = "/workspace/project";
    }
  } else if (currentUploadModalMode === "folder" && files.length > 0) {
    if (extractCheck) extractCheck.checked = true;
    const firstRel = files[0].webkitRelativePath || "";
    const topFolder = firstRel.split("/")[0];
    if (topFolder && targetInput) {
      targetInput.value = `/workspace/${topFolder}`;
    }
  } else if (currentUploadModalMode === "archive" && files.length === 1) {
    if (extractCheck) extractCheck.checked = true;
    const baseName = files[0].name.replace(/\.(tar\.gz|tgz|tar)$/i, "");
    if (targetInput && baseName) {
      targetInput.value = `/workspace/${baseName}`;
    }
  }

  updateUploadModalCodePreview();
}

function updateUploadModalCodePreview() {
  const previewEl = document.getElementById("uploadModalCodePreview");
  if (!previewEl) return;

  const envIdRaw = (document.getElementById("environmentIdInput").value || "").trim();
  const cleanEnv = (!envIdRaw || envIdRaw === "remote") ? "<ENVIRONMENT_ID>" : envIdRaw.replace(/^environments\//, "");
  const targetVal = (document.getElementById("uploadTargetPathInput").value || "/workspace/project").trim();
  const relPath = targetVal.replace(/^\/+/, "") || "workspace/project";
  const overwrite = document.getElementById("uploadOverwriteCheckbox")?.checked ?? true;
  const extract = document.getElementById("uploadExtractCheckbox")?.checked ?? false;
  const resumable = document.getElementById("uploadResumableCheckbox")?.checked ?? false;

  const firstFile = selectedUploadFiles.length === 1 ? selectedUploadFiles[0].name : (extract ? "project.tar.gz" : "local_dir/main.py");
  const qParams = [];
  if (resumable) qParams.push("uploadType=resumable");
  if (extract) qParams.push("extract=true");
  if (overwrite) qParams.push("overwrite=true");
  const qs = qParams.length ? `?${qParams.join("&")}` : "";

  const snippet = `# Python SDK (google-genai)
client.environments.files.upload(
    environment="${cleanEnv}",
    path="${relPath}",
    file="${firstFile}",${extract ? "\n    extract=True," : ""}${overwrite ? "\n    overwrite=True," : ""}
)

# REST API (Environments Upload)
curl -X PUT \\
  "https://generativelanguage.googleapis.com/upload/v1beta/environments/${cleanEnv}/files/${relPath}${qs}" \\
  -H "x-goog-api-key: $GEMINI_API_KEY" \\
  --data-binary @${firstFile}`;

  previewEl.textContent = snippet;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const commaIdx = result.indexOf(",");
      resolve(commaIdx >= 0 ? result.slice(commaIdx + 1) : result);
    };
    reader.onerror = (err) => reject(err);
    reader.readAsDataURL(file);
  });
}

async function submitUploadToEnvironment() {
  if (!currentSession) return;

  const errBox = document.getElementById("uploadEnvErrorBox");
  const okBox = document.getElementById("uploadEnvSuccessBox");
  const submitBtn = document.getElementById("submitUploadEnvBtn");
  errBox.classList.add("hidden");
  okBox.classList.add("hidden");

  if (!selectedUploadFiles || selectedUploadFiles.length === 0) {
    errBox.textContent = "Selecione pelo menos um arquivo ou pasta para enviar ao ambiente.";
    errBox.classList.remove("hidden");
    return;
  }

  const targetPath = (document.getElementById("uploadTargetPathInput").value || "").trim();
  if (!targetPath) {
    errBox.textContent = "Informe o caminho de destino (path) dentro do ambiente (ex: /workspace/main.py).";
    errBox.classList.remove("hidden");
    return;
  }

  const overwrite = document.getElementById("uploadOverwriteCheckbox")?.checked ?? true;
  const extract = document.getElementById("uploadExtractCheckbox")?.checked ?? false;
  const resumable = document.getElementById("uploadResumableCheckbox")?.checked ?? false;
  const autoProvision = document.getElementById("uploadAutoProvisionToggle")?.checked ?? true;

  const originalBtnHtml = submitBtn.innerHTML;
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<span>⏳</span><span>Enviando para o Ambiente...</span>';

  try {
    const encodedFiles = [];
    for (const f of selectedUploadFiles) {
      const b64 = await fileToBase64(f);
      let relPath = f.webkitRelativePath || f.name;
      // Se for upload de pasta (webkitRelativePath = "minha_pasta/sub/arq.py") e o targetPath já termina com "minha_pasta",
      // removemos o primeiro nível para evitar duplicar "/workspace/minha_pasta/minha_pasta/sub/arq.py"
      if (currentUploadModalMode === "folder" && relPath.includes("/")) {
        const parts = relPath.split("/");
        const topDir = parts[0];
        if (targetPath.replace(/\/+$/, "").endsWith(`/${topDir}`)) {
          relPath = parts.slice(1).join("/");
        }
      }
      encodedFiles.push({
        path: relPath,
        content_b64: b64,
        content_type: f.type || "",
      });
    }

    const res = await fetch(`/api/sessions/${currentSession.id}/environment/upload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_path: targetPath,
        overwrite,
        extract,
        resumable,
        auto_provision: autoProvision,
        pack_as_tar_gz: currentUploadModalMode === "folder" || encodedFiles.length > 1,
        files: encodedFiles,
      }),
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }

    if (data.session) {
      currentSession = data.session;
    }
    syncInputsFromSession();
    await loadSessionsList();

    const envBadge = document.getElementById("uploadModalEnvBadge");
    if (envBadge && data.environment_id) {
      envBadge.textContent = data.environment_id;
      envBadge.className = "font-mono text-xs text-blue-300 bg-blue-950/60 px-2.5 py-1 rounded border border-blue-500/30";
    }

    okBox.innerHTML = `✅ <strong>Upload concluído com sucesso!</strong><br/>` +
      `Ambiente: <code class="font-mono text-emerald-200">${escapeHtml(data.environment_id)}</code><br/>` +
      `Destino: <code class="font-mono text-emerald-200">${escapeHtml(data.upload?.target_path || targetPath)}</code> ` +
      `(${formatBytesSize(data.upload?.size_bytes || 0)})`;
    okBox.classList.remove("hidden");
  } catch (err) {
    errBox.textContent = `❌ Falha no upload para o ambiente: ${err.message}`;
    errBox.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = originalBtnHtml;
  }
}

// ============================================================================
// MODAL EXPLORADOR DE ARQUIVOS DO AMBIENTE (GET /v1beta/environments/{id}/files)
// COM DOWNLOAD INDIVIDUAL DE ARQUIVOS E PASTAS
// ============================================================================

async function openEnvFilesModal() {
  const modal = document.getElementById("envFilesModal");
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  const filterEl = document.getElementById("envFilesFilterInput");
  if (filterEl) filterEl.value = "";
  await refreshEnvFilesList();
}

function closeEnvFilesModal() {
  const modal = document.getElementById("envFilesModal");
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}

function normalizeEnvEntry(item, queriedPath) {
  const base = (queriedPath || "workspace").replace(/^\/+|\/+$/g, "");
  if (typeof item === "string") {
    let clean = item.trim();
    if (clean.includes("/files/")) {
      clean = clean.split("/files/")[1];
    }
    const isDir = clean.endsWith("/");
    return {
      fullPath: clean.replace(/^\/+/, ""),
      displayName: clean,
      isDir,
      sizeStr: "",
    };
  }
  if (!item || typeof item !== "object") {
    return {
      fullPath: String(item),
      displayName: String(item),
      isDir: false,
      sizeStr: "",
    };
  }

  let rawPath = item.path || item.name || item.file_path || "";
  if (rawPath.includes("/files/")) {
    rawPath = rawPath.split("/files/")[1];
  }
  let cleanPath = rawPath.replace(/^\/+/, "");
  if (cleanPath && base && !cleanPath.startsWith(base + "/") && cleanPath !== base && !rawPath.startsWith("/")) {
    cleanPath = `${base}/${cleanPath}`;
  }

  const rawType = String(item.type || item.file_type || "").toLowerCase();
  const rawMime = String(item.mimeType || item.mime_type || "").toLowerCase();
  const isDir =
    rawType === "directory" ||
    rawType === "dir" ||
    rawType === "folder" ||
    item.is_dir === true ||
    item.isDirectory === true ||
    rawMime === "inode/directory" ||
    rawPath.endsWith("/");

  const rawSize = item.size !== undefined ? item.size : (item.size_bytes !== undefined ? item.size_bytes : item.sizeBytes);
  const sizeStr = rawSize !== undefined && rawSize !== null && !isDir ? formatBytesSize(rawSize) : "";

  return {
    fullPath: cleanPath || base,
    displayName: cleanPath || rawPath || JSON.stringify(item),
    isDir,
    sizeStr,
  };
}

function navigateEnvFolder(folderPath) {
  const input = document.getElementById("envFilesPathInput");
  if (!input) return;
  input.value = (folderPath || "workspace").replace(/^\/+|\/+$/g, "") || "workspace";
  refreshEnvFilesList();
}

function navigateEnvParentFolder() {
  const input = document.getElementById("envFilesPathInput");
  if (!input) return;
  const current = (input.value || "workspace").replace(/^\/+|\/+$/g, "");
  if (!current || !current.includes("/")) {
    input.value = "workspace";
  } else {
    const parts = current.split("/");
    parts.pop();
    input.value = parts.join("/") || "workspace";
  }
  refreshEnvFilesList();
}

function filterEnvFilesRenderedList() {
  const query = (document.getElementById("envFilesFilterInput")?.value || "").trim().toLowerCase();
  const rows = document.querySelectorAll("#envFilesListContainer .env-file-row");
  let visibleCount = 0;
  rows.forEach((row) => {
    const pathAttr = (row.getAttribute("data-filepath") || "").toLowerCase();
    if (!query || pathAttr.includes(query)) {
      row.classList.remove("hidden");
      row.classList.add("flex");
      visibleCount++;
    } else {
      row.classList.add("hidden");
      row.classList.remove("flex");
    }
  });
  const countEl = document.getElementById("envFilesVisibleCount");
  if (countEl) {
    countEl.textContent = `${visibleCount} item(ns)`;
  }
}

async function copyEnvFilePath(filePath, btnEl) {
  const formatted = filePath.startsWith("/") ? filePath : `/${filePath}`;
  try {
    await navigator.clipboard.writeText(formatted);
    if (btnEl) {
      const orig = btnEl.innerHTML;
      btnEl.innerHTML = "✅ Copiado";
      setTimeout(() => {
        btnEl.innerHTML = orig;
      }, 1500);
    }
  } catch (err) {
    console.error("Erro ao copiar caminho:", err);
  }
}

function downloadCurrentEnvFolder() {
  const pathVal = (document.getElementById("envFilesPathInput")?.value || "workspace").trim();
  downloadSpecificEnvFile(pathVal, null);
}

async function downloadSpecificEnvFile(filePath, btnEl = null) {
  const envInput = document.getElementById("environmentIdInput");
  const targetEnv = (envInput?.value || (currentSession && currentSession.environment_id) || "").trim();

  if (!targetEnv || targetEnv === "remote") {
    alert("⚠️ Nenhum environment_id ativo nesta sessão para baixar arquivos.");
    return;
  }

  const cleanPath = (filePath || "").trim().replace(/^\/+/, "");
  const origHtml = btnEl ? btnEl.innerHTML : "";
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.innerHTML = "⏳ Baixando...";
  }

  try {
    const qs = cleanPath ? `?${new URLSearchParams({ path: cleanPath }).toString()}` : "";
    const url = (currentSession && currentSession.id && currentSession.environment_id === targetEnv)
      ? `/api/sessions/${currentSession.id}/environment/download${qs}`
      : `/api/environments/${encodeURIComponent(targetEnv)}/download${qs}`;

    const resp = await fetchWithRetry(url, {}, 2);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: "Erro desconhecido ao baixar arquivo." }));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    const blob = await resp.blob();
    const disposition = resp.headers.get("content-disposition") || "";
    let filename = cleanPath ? cleanPath.split("/").filter(Boolean).pop() : `environment_${targetEnv}.tar.gz`;

    // Suporta RFC 5987 filename*=UTF-8''... e filename="..."
    const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match && utf8Match[1]) {
      try {
        filename = decodeURIComponent(utf8Match[1].trim());
      } catch (_) {}
    } else if (disposition.includes("filename=")) {
      const match = disposition.match(/filename=["']?([^"';]+)["']?/i);
      if (match && match[1]) {
        filename = match[1].trim();
      }
    }

    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = filename || "arquivo";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(downloadUrl);

    if (btnEl) {
      btnEl.innerHTML = "✅ Baixado!";
      setTimeout(() => {
        btnEl.disabled = false;
        btnEl.innerHTML = origHtml;
      }, 1800);
      return;
    }
  } catch (err) {
    alert(`❌ Falha ao baixar '${cleanPath || targetEnv}':\n${err.message}`);
  }

  if (btnEl) {
    btnEl.disabled = false;
    btnEl.innerHTML = origHtml;
  }
}

async function refreshEnvFilesList() {
  const container = document.getElementById("envFilesListContainer");
  if (!container) return;

  const envId = (document.getElementById("environmentIdInput").value || "").trim();
  const pathVal = (document.getElementById("envFilesPathInput")?.value || "workspace").trim();
  const recursive = document.getElementById("envFilesRecursiveCheckbox")?.checked ?? true;

  if (!envId || envId === "remote") {
    const uploaded = (currentSession && Array.isArray(currentSession.uploaded_files))
      ? currentSession.uploaded_files
      : [];
    container.innerHTML = `
      <div class="p-4 text-xs text-amber-300 bg-amber-950/30 border border-amber-500/30 rounded-lg space-y-2">
        <p class="font-semibold">⚠️ O ambiente desta sessão ainda está como <code>remote</code> (não provisionado).</p>
        <p class="text-gray-300">Clique em <strong>"⚡ Provisionar"</strong>, <strong>"⬆️ Upload de Arquivo/Pasta"</strong> ou envie uma mensagem no chat para instanciar o contêiner e listar seus arquivos.</p>
        ${uploaded.length > 0 ? `<p class="text-blue-300">Registros de uploads na sessão: ${uploaded.length}</p>` : ""}
      </div>
    `;
    return;
  }

  container.innerHTML = `<div class="text-xs text-gray-400 py-8 text-center">⏳ Consultando arquivos de <code>${escapeHtml(envId)}</code> (${escapeHtml(pathVal)})...</div>`;

  try {
    const qs = new URLSearchParams({ path: pathVal, recursive: String(recursive) }).toString();
    const url = (currentSession && currentSession.id)
      ? `/api/sessions/${currentSession.id}/environment/files?${qs}`
      : `/api/environments/${encodeURIComponent(envId)}/files?${qs}`;

    const res = await fetchWithRetry(url, {}, 2);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }

    const apiData = data.data || {};
    let filesArr = null;
    if (Array.isArray(apiData)) {
      filesArr = apiData;
    } else if (Array.isArray(apiData.files)) {
      filesArr = apiData.files;
    } else if (Array.isArray(apiData.entries)) {
      filesArr = apiData.entries;
    } else if (Array.isArray(apiData.items)) {
      filesArr = apiData.items;
    }

    let html = "";
    if (filesArr && filesArr.length > 0) {
      const entries = filesArr.map((item) => normalizeEnvEntry(item, pathVal));
      html += `
        <div class="flex items-center justify-between text-[11px] text-gray-400 pb-1.5 mb-1.5 border-b border-studio-border">
          <span>Caminho consultado: <code class="text-purple-300">/${escapeHtml(pathVal.replace(/^\/+/, ""))}</code></span>
          <span id="envFilesVisibleCount">${entries.length} item(ns)</span>
        </div>
        <div class="space-y-1.5">
      `;
      for (const entry of entries) {
        const safePathJson = JSON.stringify(entry.fullPath);
        if (entry.isDir) {
          html += `
            <div data-filepath="${escapeHtml(entry.fullPath)}" class="env-file-row flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-studio-card hover:bg-gray-800/80 border border-studio-border text-xs font-mono transition">
              <button
                type="button"
                onclick='navigateEnvFolder(${safePathJson})'
                class="flex items-center gap-2 text-left text-amber-200 hover:text-amber-100 truncate min-w-0 flex-1"
                title="Clique para abrir esta pasta"
              >
                <span class="shrink-0">📁</span>
                <span class="truncate">${escapeHtml(entry.displayName)}</span>
              </button>
              <div class="flex items-center gap-1.5 shrink-0">
                <button
                  type="button"
                  onclick='navigateEnvFolder(${safePathJson})'
                  class="px-2 py-1 rounded bg-purple-600/20 hover:bg-purple-600/35 text-purple-200 border border-purple-500/30 text-[10px]"
                  title="Abrir pasta"
                >
                  📂 Abrir
                </button>
                <button
                  type="button"
                  onclick='downloadSpecificEnvFile(${safePathJson}, this)'
                  class="px-2 py-1 rounded bg-blue-600/20 hover:bg-blue-600/35 text-blue-200 border border-blue-500/30 text-[10px]"
                  title="Baixar pasta como .tar.gz"
                >
                  ⬇️ .tar.gz
                </button>
              </div>
            </div>
          `;
        } else {
          html += `
            <div data-filepath="${escapeHtml(entry.fullPath)}" class="env-file-row flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-studio-card hover:bg-gray-800/80 border border-studio-border text-xs font-mono transition">
              <div class="flex items-center gap-2 min-w-0 flex-1">
                <span class="shrink-0">📄</span>
                <span class="text-gray-100 truncate" title="/${escapeHtml(entry.fullPath)}">${escapeHtml(entry.displayName)}</span>
                ${entry.sizeStr ? `<span class="text-gray-500 text-[10px] shrink-0">(${escapeHtml(entry.sizeStr)})</span>` : ""}
              </div>
              <div class="flex items-center gap-1.5 shrink-0">
                <button
                  type="button"
                  onclick='copyEnvFilePath(${safePathJson}, this)'
                  class="px-2 py-1 rounded bg-studio-bg hover:bg-gray-700 text-gray-300 border border-studio-border text-[10px]"
                  title="Copiar caminho completo do arquivo no sandbox"
                >
                  📋 Caminho
                </button>
                <button
                  type="button"
                  onclick='downloadSpecificEnvFile(${safePathJson}, this)'
                  class="px-2.5 py-1 rounded bg-emerald-600/25 hover:bg-emerald-600/40 text-emerald-200 border border-emerald-500/40 font-semibold text-[10px] transition"
                  title="Baixar este arquivo para o seu computador"
                >
                  ⬇️ Baixar
                </button>
              </div>
            </div>
          `;
        }
      }
      html += `</div>`;
    } else {
      html += `<pre class="text-xs font-mono text-gray-200 whitespace-pre-wrap">${escapeHtml(JSON.stringify(apiData, null, 2))}</pre>`;
    }

    container.innerHTML = html;
    filterEnvFilesRenderedList();
  } catch (err) {
    const uploaded = (currentSession && Array.isArray(currentSession.uploaded_files))
      ? currentSession.uploaded_files
      : [];
    container.innerHTML = `
      <div class="p-3 text-xs text-red-300 bg-red-950/30 border border-red-500/30 rounded-lg space-y-2">
        <div>❌ Erro ao consultar API de arquivos do ambiente: ${escapeHtml(err.message)}</div>
        ${uploaded.length > 0 ? `
          <div class="pt-2 border-t border-red-500/20 text-gray-300">
            <div class="font-semibold text-blue-300 mb-1">Histórico de Uploads registrados nesta sessão:</div>
            ${uploaded.map(u => `
              <div class="flex items-center justify-between py-1 font-mono text-[11px]">
                <span>• ${escapeHtml(u.target_path)} (${formatBytesSize(u.size_bytes || 0)})</span>
                <button
                  type="button"
                  onclick='downloadSpecificEnvFile(${JSON.stringify(u.target_path)}, this)'
                  class="px-2 py-0.5 rounded bg-emerald-600/25 hover:bg-emerald-600/40 text-emerald-200 border border-emerald-500/40 text-[10px]"
                >
                  ⬇️ Baixar
                </button>
              </div>
            `).join("")}
          </div>
        ` : ""}
      </div>
    `;
  }
}


