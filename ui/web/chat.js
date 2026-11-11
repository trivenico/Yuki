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
  const loadIntegrations = async () => {
    const list = $("integration-list");
    if (!list || !state.user) return;
    list.replaceChildren();
    try {
      const [catalog, connected] = await Promise.all([
        api("/v1/integrations/oauth/providers"),
        api("/v1/integrations/oauth/connections")
      ]);
      const records = connected.connections || [];
      const providers = catalog.providers || [];
      const heading = document.createElement("p");
      heading.className = "integration-subheading";
      heading.textContent = "CONNECTED";
      list.append(heading);
      if (!records.length) {
        const empty = document.createElement("p");
        empty.className = "integration-empty";
        empty.textContent = "No services connected.";
        list.append(empty);
      }
      records.forEach((record) => {
        const row = document.createElement("div");
        row.className = "integration-row";
        const detail = document.createElement("div");
        detail.className = "integration-detail";
        const name = document.createElement("strong");
        name.textContent = record.provider_id;
        const scopes = document.createElement("span");
        scopes.textContent = (record.scopes || []).join(", ") || "No scopes reported";
        detail.append(name, scopes);
        const disconnect = document.createElement("button");
        disconnect.className = "integration-action danger-action";
        disconnect.type = "button";
        disconnect.textContent = "Disconnect";
        disconnect.addEventListener("click", async () => {
          if (!window.confirm("Disconnect " + record.provider_id + "? This removes its saved Yuki credentials.")) return;
          disconnect.disabled = true;
          try {
            await api("/v1/integrations/oauth/connections/" +
              encodeURIComponent(record.provider_id) + "/" + encodeURIComponent(record.account_id),
              { method: "DELETE" });
            toast(record.provider_id + " disconnected.");
            await loadIntegrations();
          } catch (error) { toast(error.message); disconnect.disabled = false; }
        });
        row.append(detail, disconnect);
        list.append(row);
      });
      const available = providers.filter((item) => item.available === true);
      if (available.length) {
        const heading2 = document.createElement("p");
        heading2.className = "integration-subheading";
        heading2.textContent = "AVAILABLE";
        list.append(heading2);
      }
      available.forEach((provider) => {
        if (records.some((record) => record.provider_id === provider.provider_id)) return;
        const row = document.createElement("div");
        row.className = "integration-row";
        const detail = document.createElement("div");
        detail.className = "integration-detail";
        const name = document.createElement("strong");
        name.textContent = provider.display_name || provider.provider_id;
        const description = document.createElement("span");
        description.textContent = provider.description || "Owner-controlled connection";
        detail.append(name, description);
        const connect = document.createElement("button");
        connect.className = "integration-action";
        connect.type = "button";
        connect.textContent = "Connect";
        connect.addEventListener("click", async () => {
          connect.disabled = true;
          try {
            const result = await api("/v1/integrations/oauth/providers/" +
              encodeURIComponent(provider.provider_id) + "/authorize",
              { method: "POST", body: "{}" });
            const target = new URL(result.authorization_url);
            if (target.protocol !== "https:") throw new Error("Provider authorization URL must use HTTPS.");
            window.location.assign(target.href);
          } catch (error) { toast(error.message); connect.disabled = false; }
        });
        row.append(detail, connect);
        list.append(row);
      });
      if (!records.length && !available.length) {
        const empty = document.createElement("p");
        empty.className = "integration-empty";
        empty.textContent = "No OAuth providers are configured by the Yuki host.";
        list.append(empty);
      }
    } catch (error) {
      const message = document.createElement("p");
      message.className = "integration-empty";
      message.textContent = "Integrations unavailable: " + error.message;
      list.append(message);
    }
  };
  const handleOAuthCallback = async () => {
    const segments = window.location.pathname.split("/");
    const provider = segments.length === 4 && segments[1] === "oauth" &&
      segments[2] === "callback" && /^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$/.test(segments[3])
      ? segments[3] : null;
    if (!provider) return false;
    const callbackUrl = window.location.href;
    if (!state.token) {
      toast("Sign in with the account that started this connection, then retry the callback.");
      return true;
    }
    try {
      await api("/v1/integrations/oauth/providers/" + encodeURIComponent(provider) + "/callback", {
        method: "POST", body: JSON.stringify({ callback_url: callbackUrl })
      });
      window.history.replaceState({}, document.title, "/" + window.location.hash);
      toast(provider + " connected successfully.");
      await loadIntegrations();
    } catch (error) {
      if (!state.token) {
        toast("Session expired. Sign in with the account that started this connection to retry.");
      } else {
        window.history.replaceState({}, document.title, "/" + window.location.hash);
        toast("OAuth connection failed: " + error.message);
      }
    }
    return true;
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

  const loadPairedDevices = async () => {
    const caller = $("call-caller"), recipient = $("call-recipient");
    if (!caller || !recipient || !state.user) return;
    $("call-status").textContent = "Loading paired devices…";
    try {
      const data = await api("/v1/devices");
      const devices = data.devices || [];
      const fill = (select, placeholder) => {
        select.replaceChildren();
        const first = document.createElement("option");
        first.value = ""; first.textContent = placeholder; select.append(first);
        devices.forEach((device) => {
          const option = document.createElement("option");
          option.value = device.device_id;
          option.textContent = device.name + " · " + device.device_id;
          select.append(option);
        });
      };
      fill(caller, "Choose calling device");
      fill(recipient, "Choose recipient device");
      $("call-status").textContent = devices.length < 2
        ? "Pair at least two active devices to place a LAN call."
        : devices.length + " active paired devices available. Calls remain pending until the recipient accepts.";
    } catch (error) {
      $("call-status").textContent = "Unable to load paired devices: " + error.message;
    }
  };
  $("call-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const callerId = $("call-caller").value;
    const recipientId = $("call-recipient").value;
    const passwordInput = $("call-password");
    const password = passwordInput.value;
    const devices = [...$("call-caller").options, ...$("call-recipient").options];
    const callerName = [...$("call-caller").selectedOptions][0]?.textContent || callerId;
    const recipientName = [...$("call-recipient").selectedOptions][0]?.textContent || recipientId;
    if (!callerId || !recipientId || callerId === recipientId || !password) {
      $("call-status").textContent = "Choose two different paired devices and enter your password.";
      return;
    }
    const intent = "Place a LAN call from:\n" + callerName + "\n\nTo:\n" + recipientName +
      "\n\nThe recipient must separately accept. No microphone or camera will be activated by this invitation.";
    if (!window.confirm(intent + "\n\nDo you want to authorize this exact invitation?")) return;
    const submit = $("call-submit");
    submit.disabled = true;
    $("call-status").textContent = "Re-authenticating and creating the one-time invitation…";
    let confirmationToken = null;
    try {
      const challenge = await api("/v1/devices/calls/confirmation-challenges", {
        method: "POST",
        body: JSON.stringify({
          password,
          caller_device_id: callerId,
          recipient_device_id: recipientId
        })
      });
      confirmationToken = challenge.confirmation_token;
      const created = await api("/v1/devices/calls", {
        method: "POST",
        body: JSON.stringify({
          caller_device_id: callerId,
          recipient_device_id: recipientId,
          confirmation_token: confirmationToken
        })
      });
      $("call-status").textContent = "Invitation created. Status: " + created.session.status +
        ". The recipient device must accept before a call can proceed.";
      toast("LAN call invitation sent.");
    } catch (error) {
      $("call-status").textContent = "Call invitation not created: " + error.message;
    } finally {
      confirmationToken = null;
      passwordInput.value = "";
      submit.disabled = false;
    }
  });
  $("refresh-devices").addEventListener("click", () => loadPairedDevices());

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault(); $("login-error").textContent = "";
    try {
      const data = await api("/v1/auth/login", { method: "POST", body: JSON.stringify({ username: $("username").value, password: $("password").value }) });
      state.token = data.access_token; state.user = data.user; sessionStorage.setItem("yuki_token", state.token);
      $("account-label").textContent = data.user.username; showChat(); await loadProviderStatus(); await loadIntegrations(); await loadPairedDevices(); await handleOAuthCallback(); await loadConversations();
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
  $("refresh-integrations").addEventListener("click", () => loadIntegrations());
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
      await loadProviderStatus(); await loadIntegrations(); await loadPairedDevices(); await handleOAuthCallback(); await loadConversations();
      if (state.conversations.length) await openConversation(state.conversations[0].conversation_id); else showWelcome();
    } catch (_) { signOut(false); }
  };
  boot();
})();
