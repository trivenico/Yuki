(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { token: sessionStorage.getItem("yuki_token"), user: null, conversations: [], active: null, busy: false, abort: null, version: 0, requestId: null };
  const api = async (path, options = {}) => {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (state.token) headers.Authorization = "Bearer " + state.token;
    const response = await fetch(path, { ...options, headers });
    const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) signOut(false);
      throw new Error(data.error || "Request failed (" + response.status + ")");
    }
    return data;
  };
  const toast = (message) => { $("toast").textContent = message; $("toast").classList.add("visible"); setTimeout(() => $("toast").classList.remove("visible"), 3200); };
  const showLogin = () => { $("login-view").hidden = false; $("chat-view").hidden = true; };
  const showChat = () => { $("login-view").hidden = true; $("chat-view").hidden = false; };
  const signOut = (notify = true) => {
    state.token = null; state.user = null; state.active = null; state.version = 0;
    sessionStorage.removeItem("yuki_token"); showLogin();
    if (notify) toast("You have signed out.");
  };
  const makeMessage = (role, content) => {
    const row = document.createElement("article"); row.className = "message " + role;
    const avatar = document.createElement("div"); avatar.className = "message-avatar"; avatar.textContent = role === "assistant" ? "雪" : "You";
    const body = document.createElement("div"); body.className = "message-body";
    const label = document.createElement("div"); label.className = "message-label"; label.textContent = role === "assistant" ? "Yuki" : "You";
    const text = document.createElement("div"); text.className = "message-text"; text.textContent = content;
    body.append(label, text); row.append(avatar, body); $("messages").append(row); return text;
  };
  const showWelcome = () => {
    $("messages").replaceChildren();
    const welcome = document.createElement("div"); welcome.className = "welcome";
    const mark = document.createElement("div"); mark.className = "brand-mark"; mark.textContent = "雪";
    const eyebrow = document.createElement("p"); eyebrow.className = "eyebrow"; eyebrow.textContent = "A NEW WAY TO THINK";
    const title = document.createElement("h1"); title.textContent = "What can I help you with?";
    const sub = document.createElement("p"); sub.className = "muted"; sub.textContent = "Ask a question, explore an idea, or get something done.";
    welcome.append(mark, eyebrow, title, sub); $("messages").append(welcome);
  };
  const loadProviderStatus = async () => {
    const label = $("provider-label");
    const select = $("provider-select");
    if (!label) return;
    try {
      const data = await api("/v1/providers");
      const providers = data.providers || [];
      window.__yukiProviders = providers;
      const selected = providers.find((item) => item.selected);
      if (!selected) {
        label.textContent = "Model status unavailable";
        label.title = "No provider is selected in server configuration.";
        if (select) select.hidden = true;
        return;
      }
      const model = selected.configured_model || "model not configured";
      label.textContent = (selected.label || selected.name) + " · " + model;
      label.title = [
        "Status: " + (selected.ready ? "configured (connectivity not verified)" : selected.status),
        "Privacy: " + selected.privacy,
        "Credential configured: " + (selected.credential_configured ? "yes" : "no"),
        "Provider changes affect every user of this Yuki server."
      ].join("\n");
      label.dataset.ready = selected.ready ? "true" : "false";
      if (select) {
        select.replaceChildren();
        providers.filter((item) => item.implemented).forEach((item) => {
          const option = document.createElement("option");
          option.value = item.name;
          option.textContent = (item.label || item.name) + (item.ready ? "" : " — not ready");
          option.disabled = !item.ready && !item.selected;
          option.selected = Boolean(item.selected);
          select.append(option);
        });
        select.hidden = !state.user || state.user.role !== "admin";
      }
    } catch (_) {
      label.textContent = "Model status unavailable";
      label.title = "Unable to load provider metadata.";
      if (select) select.hidden = true;
    }
  };
  const loadConversations = async () => {
    const data = await api("/v1/conversations");
    state.conversations = data.conversations || [];
    const nav = $("conversation-list"); nav.replaceChildren();
    state.conversations.forEach((item) => {
      const button = document.createElement("button"); button.className = "conversation-item" + (item.conversation_id === state.active ? " active" : "");
      button.textContent = item.title || "New conversation";
      button.title = item.title || "New conversation";
      button.addEventListener("click", () => openConversation(item.conversation_id));
      nav.append(button);
    });
  };
  const openConversation = async (id) => {
    state.active = id; await loadConversations();
    const data = await api("/v1/conversations/" + encodeURIComponent(id) + "/messages");
    $("messages").replaceChildren();
    (data.messages || []).forEach((item) => makeMessage(item.role, item.content));
    state.version = state.conversations.find((item) => item.conversation_id === id)?.version ?? 0;
    if (!(data.messages || []).length) showWelcome();
    $("messages").scrollTop = $("messages").scrollHeight;
  };
  const newConversation = async () => {
    const data = await api("/v1/conversations", { method: "POST", body: JSON.stringify({ title: "New conversation" }) });
    state.active = data.conversation_id; state.version = data.version || 0;
    await loadConversations(); showWelcome(); $("message-input").focus();
  };
  const setBusy = (busy) => {
    state.busy = busy; $("send-button").hidden = busy; $("cancel-button").hidden = !busy;
    $("message-input").disabled = busy; $("connection-label").textContent = busy ? "Yuki is thinking…" : "Connected";
  };
  const sendMessage = async (text) => {
    if (!text.trim() || state.busy) return;
    if (!state.active) await newConversation();
    const userText = text.trim(); $("message-input").value = "";
    makeMessage("user", userText);
    const assistantText = makeMessage("assistant", "");
    const requestId = crypto.randomUUID(); state.requestId = requestId;
    state.abort = new AbortController(); setBusy(true);
    try {
      const response = await fetch("/v1/conversations/" + encodeURIComponent(state.active) + "/messages/stream", {
        method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + state.token },
        body: JSON.stringify({ content: userText, request_id: requestId, expected_version: state.version }),
        signal: state.abort.signal
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || "Unable to send message (" + response.status + ")");
      }
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
          const eventLine = frame.split("\n").find((line) => line.startsWith("event: "));
          const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
          if (!dataLine) continue;
          const event = JSON.parse(dataLine.slice(6));
          if (event.type === "delta") assistantText.textContent += event.text;
          if (event.type === "completed" || event.type === "cancelled") {
            state.version = event.result.version; if (event.type === "cancelled") toast("Generation stopped.");
          }
          if (event.type === "error") throw new Error(event.error || "Generation failed.");
          $("messages").scrollTop = $("messages").scrollHeight;
        }
      }
      if (!assistantText.textContent) await openConversation(state.active);
      await loadConversations();
    } catch (error) {
      if (error.name === "AbortError") toast("Connection closed.");
      else { assistantText.textContent = assistantText.textContent || "I couldn't complete that request."; toast(error.message); }
      try { await openConversation(state.active); } catch (_) {}
    } finally { state.abort = null; state.requestId = null; setBusy(false); $("message-input").focus(); }
  };
  const memoryStatus = (message) => { $("memory-status").textContent = message || ""; };
  const memoryActionButton = (label, action, memory) => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "memory-action";
    button.textContent = label;
    button.addEventListener("click", () => action(memory).catch((error) => toast(error.message)));
    return button;
  };
  const loadMemory = async () => {
    memoryStatus("Loading your private notes…");
    const data = await api("/v1/memory");
    const list = $("memory-list"); list.replaceChildren();
    const memories = data.memories || [];
    if (!memories.length) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.textContent = "No saved memories yet. Add a note above to give Yuki useful context.";
      list.append(empty);
    }
    memories.forEach((memory) => {
      const card = document.createElement("article"); card.className = "memory-card";
      const content = document.createElement("p"); content.className = "memory-content";
      content.textContent = memory.content;
      const meta = document.createElement("p"); meta.className = "memory-meta";
      meta.textContent = (memory.lifecycle_status || "active") + " · " +
        (memory.verified ? "owner-approved" : "unverified reference") +
        (memory.created_at ? " · " + new Date(memory.created_at).toLocaleDateString() : "");
      const actions = document.createElement("div"); actions.className = "memory-actions";
      if (memory.lifecycle_status !== "approved") {
        actions.append(memoryActionButton("Approve", async (item) => {
          const reason = window.prompt("Why is this memory accurate?");
          if (!reason || !reason.trim()) return;
          await api("/v1/memory/" + encodeURIComponent(item.memory_id), {
            method: "PATCH", body: JSON.stringify({ status: "approved", reason: reason.trim() })
          });
          await loadMemory(); toast("Memory approved.");
        }, memory));
      }
      if (memory.lifecycle_status !== "rejected") {
        actions.append(memoryActionButton("Reject", async (item) => {
          const reason = window.prompt("Why should this memory not be trusted?");
          if (!reason || !reason.trim()) return;
          await api("/v1/memory/" + encodeURIComponent(item.memory_id), {
            method: "PATCH", body: JSON.stringify({ status: "rejected", reason: reason.trim() })
          });
          await loadMemory(); toast("Memory rejected.");
        }, memory));
      }
      actions.append(memoryActionButton("Correct", async (item) => {
        const content = window.prompt("Enter the corrected memory:");
        if (!content || !content.trim()) return;
        const reason = window.prompt("Why does this need correction?");
        if (!reason || !reason.trim()) return;
        await api("/v1/memory/" + encodeURIComponent(item.memory_id) + "/corrections", {
          method: "POST", body: JSON.stringify({
            content: content.trim(), source: "owner_memory_panel", reason: reason.trim()
          })
        });
        await loadMemory(); toast("Correction saved; prior evidence retained.");
      }, memory));
      actions.append(memoryActionButton("History", async (item) => {
        const history = await api("/v1/memory/" + encodeURIComponent(item.memory_id) + "/history");
        const events = (history.lifecycle_events || []).map((entry) =>
          entry.previous_status + " → " + entry.new_status + ": " + entry.reason
        ).concat((history.corrections || []).map((entry) =>
          "Corrected: " + entry.reason
        ));
        toast(events.length ? events.join(" · ").slice(0, 900) : "No audit events recorded yet.");
      }, memory));
      actions.append(memoryActionButton("Retire", async (item) => {
        const reason = window.prompt("Why should Yuki stop using this memory?");
        if (!reason || !reason.trim()) return;
        await api("/v1/memory/" + encodeURIComponent(item.memory_id), {
          method: "PATCH", body: JSON.stringify({ status: "retired", reason: reason.trim() })
        });
        await loadMemory(); toast("Memory retired.");
      }, memory));
      actions.append(memoryActionButton("Delete", async (item) => {
        if (!window.confirm("Permanently delete this memory and its cached embedding?")) return;
        await api("/v1/memory/" + encodeURIComponent(item.memory_id), { method: "DELETE" });
        await loadMemory(); toast("Memory deleted.");
      }, memory));
      card.append(content, meta, actions); list.append(card);
    });
    memoryStatus(memories.length + " private note" + (memories.length === 1 ? "" : "s"));
  };
  $("open-memory").addEventListener("click", async () => {
    $("memory-panel").hidden = false;
    try { await loadMemory(); } catch (error) { memoryStatus(error.message); }
  });
  $("close-memory").addEventListener("click", () => { $("memory-panel").hidden = true; });
  $("memory-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const content = $("memory-content").value.trim();
    if (!content) return;
    const submit = event.currentTarget.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await api("/v1/memory", { method: "POST", body: JSON.stringify({ content }) });
      $("memory-content").value = "";
      await loadMemory(); toast("Private note saved as unverified evidence.");
    } catch (error) { memoryStatus(error.message); }
    finally { submit.disabled = false; }
  });
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault(); $("login-error").textContent = "";
    try {
      const data = await api("/v1/auth/login", { method: "POST", body: JSON.stringify({ username: $("username").value, password: $("password").value }) });
      state.token = data.access_token; state.user = data.user; sessionStorage.setItem("yuki_token", state.token);
      $("account-label").textContent = data.user.username; showChat(); await loadProviderStatus(); await loadConversations();
      if (state.conversations.length) await openConversation(state.conversations[0].conversation_id); else showWelcome();
    } catch (error) { $("login-error").textContent = error.message === "invalid_credentials" ? "Incorrect username or password." : error.message; }
  });
  $("provider-select").addEventListener("change", async () => {
    const select = $("provider-select");
    const provider = select.value;
    const target = (window.__yukiProviders || []).find((item) => item.name === provider);
    const cloud = !target || target.privacy !== "local_runtime";
    const warning = "This changes the active provider for EVERY user on this Yuki server. Existing in-flight requests may finish on their current provider. Continue?" +
      (cloud ? "\n\nThis provider may send conversation content to a cloud service or configured remote endpoint. Confirm that this data-routing change is permitted." : "");
    if (!window.confirm(warning)) { await loadProviderStatus(); return; }
    select.disabled = true;
    try {
      await api("/v1/providers/active", {
        method: "PUT",
        body: JSON.stringify({ provider, confirm_global: true, confirm_cloud: cloud })
      });
      toast("Active provider changed. New requests will use the selected provider.");
      await loadProviderStatus();
    } catch (error) {
      toast(error.message);
      await loadProviderStatus();
    } finally { select.disabled = false; }
  });
  $("new-chat").addEventListener("click", () => newConversation().catch((error) => toast(error.message)));
  $("logout").addEventListener("click", async () => { try { await api("/v1/auth/logout", { method: "POST", body: "{}" }); } catch (_) {} signOut(); });
  $("composer").addEventListener("submit", (event) => { event.preventDefault(); sendMessage($("message-input").value).catch((error) => toast(error.message)); });
  $("cancel-button").addEventListener("click", async () => {
    if (!state.active || !state.requestId) return;
    try { await api("/v1/conversations/" + encodeURIComponent(state.active) + "/messages/cancel", { method: "POST", body: JSON.stringify({ request_id: state.requestId }) }); }
    catch (error) { toast(error.message); }
  });
  $("message-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("composer").requestSubmit(); }
  });
  const boot = async () => {
    if (!state.token) return showLogin();
    try {
      const identity = await api("/v1/auth/me"); state.user = identity.user;
      const data = await api("/v1/conversations"); state.conversations = data.conversations || [];
      showChat(); $("account-label").textContent = state.user.username;
      await loadProviderStatus(); await loadConversations();
      if (state.conversations.length) await openConversation(state.conversations[0].conversation_id); else showWelcome();
    } catch (_) { signOut(false); }
  };
  boot();
})();
