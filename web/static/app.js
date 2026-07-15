(() => {
  const $ = (id) => document.getElementById(id);

  const chatEl = $("chat");
  const consoleEl = $("console");
  const serverBadge = $("serverBadge");
  const mcPortBadge = $("mcPortBadge");
  const aiBadge = $("aiBadge");
  const chatInput = $("chatInput");
  const btnSend = $("btnSend");

  let history = [];
  let sending = false;
  let autoScrollConsole = true;
  let mcInfo = { port: 25565, address: "127.0.0.1:25565", local: "127.0.0.1:25565" };

  function appendChat(role, content, extra = null) {
    const div = document.createElement("div");
    div.className = `msg ${role}${extra?.error ? " error" : ""}`;
    const roleLabel = role === "user" ? "You" : role === "assistant" ? "AI" : "System";
    div.innerHTML = `<span class="role">${roleLabel}</span>`;
    const body = document.createElement("div");
    body.textContent = content;
    div.appendChild(body);

    if (extra?.tool_trace?.length) {
      const tools = document.createElement("div");
      tools.className = "tools";
      tools.innerHTML = `<strong>Tools (${extra.tool_trace.length})</strong>`;
      for (const t of extra.tool_trace) {
        const d = document.createElement("details");
        const s = document.createElement("summary");
        s.textContent = t.tool;
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(
          { arguments: t.arguments, result: tryParse(t.result) },
          null,
          2
        );
        d.appendChild(s);
        d.appendChild(pre);
        tools.appendChild(d);
      }
      div.appendChild(tools);
    }

    chatEl.appendChild(div);
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  function tryParse(s) {
    try {
      return JSON.parse(s);
    } catch {
      return s;
    }
  }

  function setServerBadge(status) {
    if (!status.running) {
      serverBadge.textContent = "Server: offline";
      serverBadge.className = "badge badge-off";
    } else if (!status.ready) {
      serverBadge.textContent = "Server: starting…";
      serverBadge.className = "badge badge-boot";
    } else {
      serverBadge.textContent = `Server: ready (pid ${status.pid || "?"})`;
      serverBadge.className = "badge badge-on";
    }
  }

  async function refreshStatus() {
    try {
      const res = await fetch("/api/server/status");
      const data = await res.json();
      setServerBadge(data);
    } catch {
      serverBadge.textContent = "Server: unknown";
      serverBadge.className = "badge badge-off";
    }
  }

  async function refreshSettingsUi() {
    try {
      const res = await fetch("/api/settings");
      const data = await res.json();
      $("setBaseUrl").value = data.base_url || "";
      $("setModel").value = data.model || "";
      $("setNote").value = data.system_note || "";
      $("setApiKey").value = "";
      $("keyHint").textContent = data.api_key_set
        ? `Saved key: ${data.api_key_masked}`
        : "No API key saved yet";
      if (data.api_key_set) {
        aiBadge.textContent = `AI: ${data.model}`;
        aiBadge.className = "badge badge-ok";
      } else {
        aiBadge.textContent = "AI: not configured";
        aiBadge.className = "badge badge-muted";
      }
    } catch {
      aiBadge.textContent = "AI: error";
    }
  }

  async function refreshInfo() {
    try {
      const res = await fetch("/api/info");
      const data = await res.json();
      mcInfo = {
        port: data.minecraft_port || 25565,
        address: data.minecraft_address || `?:${data.minecraft_port || 25565}`,
        local: data.minecraft_address_local || `127.0.0.1:${data.minecraft_port || 25565}`,
      };
      if (mcPortBadge) {
        mcPortBadge.textContent = `MC join: ${mcInfo.local}`;
        mcPortBadge.title =
          `Адрес для Multiplayer → Direct Connection\n` +
          `Этот ПК: ${mcInfo.local}\n` +
          `По сети: ${mcInfo.address}\n` +
          `Порт: ${mcInfo.port}`;
        mcPortBadge.className = "badge badge-ok";
      }
      if ($("infoBox")) {
        $("infoBox").textContent =
          `Web UI:     ${data.web_url}\n` +
          `Web local:  ${data.local_url}\n` +
          `MC version: ${data.minecraft_version} ${data.loader}\n` +
          `MC join:    ${mcInfo.local}\n` +
          `MC LAN:     ${mcInfo.address}\n` +
          `MC port:    ${mcInfo.port}\n` +
          `Status:     running=${data.server.running} ready=${data.server.ready}`;
      }
    } catch {
      if ($("infoBox")) $("infoBox").textContent = "Could not load info";
    }
  }

  function appendConsoleLine(line) {
    if (line === "__ping__") return;
    const atBottom =
      consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 40;
    consoleEl.textContent += (consoleEl.textContent ? "\n" : "") + line;
    // keep DOM reasonable
    const maxChars = 400_000;
    if (consoleEl.textContent.length > maxChars) {
      consoleEl.textContent = consoleEl.textContent.slice(-maxChars);
    }
    if (autoScrollConsole || atBottom) {
      consoleEl.scrollTop = consoleEl.scrollHeight;
    }
  }

  function connectLogsWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/logs`);
    ws.onmessage = (ev) => appendConsoleLine(ev.data);
    ws.onclose = () => {
      appendConsoleLine("[ui] log websocket closed, reconnecting in 2s…");
      setTimeout(connectLogsWs, 2000);
    };
    ws.onerror = () => ws.close();
  }

  // ---- events
  $("chatForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (sending) return;
    const text = chatInput.value.trim();
    if (!text) return;

    appendChat("user", text);
    history.push({ role: "user", content: text });
    chatInput.value = "";
    sending = true;
    btnSend.disabled = true;
    btnSend.textContent = "Thinking…";

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });
      const data = await res.json();
      if (!res.ok) {
        const detail = data.detail || JSON.stringify(data);
        appendChat("assistant", `Error: ${detail}`, { error: true });
      } else {
        appendChat("assistant", data.content || "(empty response)", {
          tool_trace: data.tool_trace || [],
        });
        history.push({ role: "assistant", content: data.content || "" });
      }
    } catch (err) {
      appendChat("assistant", `Network error: ${err}`, { error: true });
    } finally {
      sending = false;
      btnSend.disabled = false;
      btnSend.textContent = "Send";
    }
  });

  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("chatForm").requestSubmit();
    }
  });

  $("btnClearChat").addEventListener("click", () => {
    history = [];
    chatEl.innerHTML = "";
    appendChat("system", "Chat cleared. Ask the AI to run server tasks.");
  });

  $("cmdForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const command = $("cmdInput").value.trim();
    if (!command) return;
    $("cmdInput").value = "";
    try {
      const res = await fetch("/api/server/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command }),
      });
      const data = await res.json();
      if (!res.ok) appendConsoleLine(`[ui] command failed: ${data.detail || JSON.stringify(data)}`);
    } catch (err) {
      appendConsoleLine(`[ui] command error: ${err}`);
    }
  });

  $("btnStart").addEventListener("click", async () => {
    const res = await fetch("/api/server/start", { method: "POST" });
    const data = await res.json();
    appendConsoleLine(`[ui] start: ${data.message || JSON.stringify(data)}`);
    refreshStatus();
  });

  $("btnStop").addEventListener("click", async () => {
    if (!confirm("Stop the Minecraft server?")) return;
    const res = await fetch("/api/server/stop", { method: "POST" });
    const data = await res.json();
    appendConsoleLine(`[ui] stop: ${data.message || JSON.stringify(data)}`);
    refreshStatus();
  });

  $("btnRefreshLogs").addEventListener("click", async () => {
    const res = await fetch("/api/server/logs?lines=300");
    const data = await res.json();
    consoleEl.textContent = (data.lines || []).join("\n");
    consoleEl.scrollTop = consoleEl.scrollHeight;
  });

  $("btnScrollEnd").addEventListener("click", () => {
    autoScrollConsole = true;
    consoleEl.scrollTop = consoleEl.scrollHeight;
  });

  consoleEl.addEventListener("scroll", () => {
    const atBottom =
      consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 40;
    autoScrollConsole = atBottom;
  });

  $("btnSettings").addEventListener("click", () => {
    $("settingsModal").classList.remove("hidden");
    refreshSettingsUi();
    refreshInfo();
  });
  $("btnCloseSettings").addEventListener("click", () => {
    $("settingsModal").classList.add("hidden");
  });
  $("settingsModal").addEventListener("click", (e) => {
    if (e.target === $("settingsModal")) $("settingsModal").classList.add("hidden");
  });

  $("settingsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      base_url: $("setBaseUrl").value.trim(),
      model: $("setModel").value.trim(),
      api_key: $("setApiKey").value,
      system_note: $("setNote").value,
    };
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.ok) {
      $("settingsModal").classList.add("hidden");
      await refreshSettingsUi();
      appendChat("system", "Settings saved.");
    } else {
      alert("Failed to save settings");
    }
  });

  // init
  appendChat(
    "system",
    "Готово. Настройте API в Settings, дождитесь Server: ready, затем пишите задачи (например: «кто онлайн», «дай алмазы», «сделай день»)."
  );
  refreshStatus();
  refreshSettingsUi();
  refreshInfo();
  connectLogsWs();
  setInterval(refreshStatus, 3000);
  setInterval(refreshInfo, 15000);
})();
