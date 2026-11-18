(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let initialized = false;
  const state = { page: 1, pageSize: 25, totalPages: 1, department: "", search: "", status: "" };
  const token = () => sessionStorage.getItem("yuki_token");
  const request = async (path) => {
    const response = await fetch(path, { headers: { Authorization: "Bearer " + (token() || ""), Accept: "application/json" }, cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Workforce request failed (" + response.status + ")");
    return data;
  };
  const el = (tag, className, value) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value);
    return item;
  };
  const showDashboard = (visible) => {
    $("workforce-dashboard").hidden = !visible;
    $("messages").hidden = visible;
    $("composer-wrap").hidden = visible;
    $("workforce-nav").setAttribute("aria-pressed", visible ? "true" : "false");
  };
  const metric = (label, value) => {
    const card = el("div", "workforce-metric");
    card.append(el("span", "", label), el("strong", "", value));
    return card;
  };
  const renderSnapshot = (data) => {
    const w = data.workforce || {}, t = data.tasks || {};
    $("workforce-metrics").replaceChildren(
      metric("Employees", w.total_employees || 0), metric("Departments", w.departments || 0),
      metric("Available employees", w.available || 0), metric("Active tasks", w.active_tasks || 0),
      metric("Queued tasks", t.queued || 0), metric("Running tasks", t.running || 0),
      metric("Succeeded tasks", t.succeeded || 0), metric("Failed tasks", t.failed || 0)
    );
    const departments = $("workforce-departments");
    departments.replaceChildren();
    (data.departments || []).forEach((item) => {
      const button = el("button", "workforce-department");
      button.type = "button";
      button.append(el("strong", "", item.name), el("span", "", item.employees + " employees · " + item.available + " available"));
      button.addEventListener("click", () => { state.department = item.name; state.page = 1; loadDirectory(); });
      departments.append(button);
    });
    if (!(data.departments || []).length) departments.append(el("p", "workforce-empty", "No department data available."));
    const goals = $("workforce-goals");
    goals.replaceChildren();
    (data.goals || []).slice(0, 12).forEach((goal) => {
      const row = el("div", "workforce-goal");
      row.append(el("strong", "", goal.goal_id), el("span", "", goal.state + " · " + goal.total_tasks + " tasks"));
      goals.append(row);
    });
    if (!(data.goals || []).length) goals.append(el("p", "workforce-empty", "No persisted workforce goals yet."));
  };
  const renderDirectory = (data) => {
    const body = $("workforce-employees");
    body.replaceChildren();
    (data.employees || []).forEach((employee) => {
      const row = el("tr");
      const identity = el("td");
      identity.append(el("span", "workforce-employee-name", employee.name), el("span", "workforce-employee-id", employee.employee_id + " · " + employee.role));
      const status = el("td");
      status.append(el("span", "workforce-state " + employee.status, employee.status));
      row.append(identity, el("td", "", employee.department), status,
        el("td", "", employee.active_tasks + " active · " + employee.succeeded_tasks + " done"),
        el("td", "", employee.available_slots + " / " + employee.capacity + " slots"),
        el("td", "", "Tier " + employee.autonomy_tier));
      body.append(row);
    });
    if (!(data.employees || []).length) {
      const row = el("tr"), cell = el("td", "workforce-empty", "No employees match these filters.");
      cell.colSpan = 6; row.append(cell); body.append(row);
    }
    state.totalPages = data.total_pages || 1;
    $("workforce-page-label").textContent = "Page " + data.page + " of " + state.totalPages + " · " + data.total + " employees";
    $("workforce-prev").disabled = data.page <= 1;
    $("workforce-next").disabled = data.page >= state.totalPages;
  };
  const loadDirectory = async () => {
    const params = new URLSearchParams({ page: String(state.page), page_size: String(state.pageSize) });
    if (state.department) params.set("department", state.department);
    if (state.search) params.set("search", state.search);
    if (state.status) params.set("status", state.status);
    renderDirectory(await request("/v1/workforce/employees?" + params.toString()));
  };
  const refresh = async () => {
    $("workforce-status").textContent = "Refreshing workforce data…";
    try {
      renderSnapshot(await request("/v1/workforce/dashboard?max_goals=100"));
      await loadDirectory();
      $("workforce-status").textContent = "Data refreshed. This view is read-only; task execution and approvals remain in Yuki’s guarded runtime.";
    } catch (error) { $("workforce-status").textContent = "Unable to load workforce data: " + error.message; }
  };
  const initialize = async () => {
    if (initialized || !token()) return;
    initialized = true;
    try {
      const identity = await request("/v1/auth/me");
      if (!identity.user || !["admin", "operator"].includes(identity.user.role)) return;
      await request("/v1/workforce/dashboard?max_goals=100");
      $("workforce-nav").hidden = false;
      $("workforce-nav").addEventListener("click", () => { showDashboard(true); refresh(); });
      $("workforce-refresh").addEventListener("click", refresh);
      $("workforce-prev").addEventListener("click", () => { state.page = Math.max(1, state.page - 1); loadDirectory().catch((e) => $("workforce-status").textContent = e.message); });
      $("workforce-next").addEventListener("click", () => { state.page = Math.min(state.totalPages, state.page + 1); loadDirectory().catch((e) => $("workforce-status").textContent = e.message); });
      $("workforce-search").addEventListener("input", () => { state.search = $("workforce-search").value.trim(); state.page = 1; loadDirectory().catch((e) => $("workforce-status").textContent = e.message); });
      $("workforce-status-filter").addEventListener("change", () => { state.status = $("workforce-status-filter").value; state.page = 1; loadDirectory().catch((e) => $("workforce-status").textContent = e.message); });
      $("workforce-clear-filter").addEventListener("click", () => {
        state.department = ""; state.search = ""; state.status = ""; state.page = 1;
        $("workforce-search").value = ""; $("workforce-status-filter").value = "";
        loadDirectory().catch((e) => $("workforce-status").textContent = e.message);
      });
      $("new-chat").addEventListener("click", () => showDashboard(false));
      $("conversation-list").addEventListener("click", () => showDashboard(false));
    } catch (_) { $("workforce-nav").hidden = true; }
  };
  window.addEventListener("yuki:session-ended", () => { initialized = false; $("workforce-nav").hidden = true; showDashboard(false); });
  window.addEventListener("yuki:session-ready", initialize);
  if (token()) initialize();
})();