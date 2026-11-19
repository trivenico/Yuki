(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let initialized = false;
  const state = { page: 1, pageSize: 25, totalPages: 1, department: "", search: "", status: "", goalId: "", nextEventId: 0 };
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
      const row = el("button", "workforce-goal");
      row.type = "button";
      row.append(el("strong", "", goal.goal_id), el("span", "", goal.state + " · " + goal.total_tasks + " tasks"));
      row.addEventListener("click", () => loadGoalDetail(goal.goal_id).catch((e) => $("workforce-status").textContent = e.message));
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
  const loadGoalDetail = async (goalId, afterEventId = 0, append = false) => {
    state.goalId = goalId;
    const params = new URLSearchParams({ after_event_id: String(afterEventId), event_limit: "50" });
    const data = await request("/v1/workforce/goals/" + encodeURIComponent(goalId) + "?" + params.toString());
    $("workforce-goal-detail").hidden = false;
    $("workforce-goal-meta").textContent = "Goal " + data.goal.goal_id + " · " + data.goal.state + " · " + data.tasks.length + " workforce tasks";
    const tasks = $("workforce-goal-tasks");
    if (!append) tasks.replaceChildren();
    data.tasks.forEach((task) => {
      const row = el("div", "workforce-timeline-row");
      row.append(el("strong", "", task.description || task.task_id), el("span", "", (task.employee_name || task.employee_id || "Unassigned") + " · " + task.state));
      tasks.append(row);
    });
    const events = $("workforce-goal-events");
    if (!append) events.replaceChildren();
    data.events.forEach((event) => {
      const row = el("div", "workforce-timeline-event");
      row.append(el("span", "", "#" + event.id + " · " + event.event_type), el("time", "", new Date(event.created_at * 1000).toLocaleString()));
      events.append(row);
    });
    state.nextEventId = data.next_event_id;
    $("workforce-events-more").hidden = !data.has_more_events;
  };

  const loadApprovalCenter = async () => {
    const status = $("workforce-approval-status");
    status.textContent = "Loading approval inbox and audit…";
    try {
      const [inbox, audit] = await Promise.all([
        request("/v1/workforce/approvals?limit=100"),
        request("/v1/workforce/approvals/audit?limit=50"),
      ]);
      const pending = $("workforce-approval-pending");
      pending.replaceChildren();
      inbox.pending.forEach((item) => {
        const row = el("div", "workforce-approval-row");
        row.append(el("strong", "", item.skill_id), el("span", "", "Expires " + new Date(item.expires_at).toLocaleString()));
        row.append(el("small", "", "Your approval reference: " + item.pending_id));
        pending.append(row);
      });
      if (!inbox.pending.length) pending.append(el("p", "workforce-empty", "No pending approvals."));
      const events = $("workforce-approval-audit");
      events.replaceChildren();
      audit.events.forEach((item) => {
        const row = el("div", "workforce-approval-row");
        row.append(el("strong", "", item.event_type + " · " + item.skill_id), el("span", "", new Date(item.created_at).toLocaleString()));
        row.append(el("small", "", "Event #" + item.event_id + " · actor " + item.actor_hash.slice(0, 12) + "…"));
        events.append(row);
      });
      if (!audit.events.length) events.append(el("p", "workforce-empty", "No approval audit events yet."));
      $("workforce-audit-integrity").textContent = audit.chain_valid ? "Audit chain verified" : "WARNING: audit chain verification failed";
      $("workforce-audit-integrity").className = "workforce-audit-integrity " + (audit.chain_valid ? "valid" : "invalid");
      $("workforce-approval-center").hidden = false;
      status.textContent = inbox.count + " pending approval(s) · " + audit.count + " recent audit event(s). Inspection only; no action has been approved or executed here.";
    } catch (error) {
      status.textContent = "Approval center unavailable: " + error.message;
      $("workforce-approval-center").hidden = false;
    }
  };

  const refresh = async () => {
    $("workforce-status").textContent = "Refreshing workforce data…";
    try {
      renderSnapshot(await request("/v1/workforce/dashboard?max_goals=100"));
      await loadDirectory();
      await loadApprovalCenter();
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
      $("workforce-approval-refresh").addEventListener("click", loadApprovalCenter);
      $("workforce-goal-close").addEventListener("click", () => { $("workforce-goal-detail").hidden = true; state.goalId = ""; });
      $("workforce-events-more").addEventListener("click", () => {
        if (state.goalId) loadGoalDetail(state.goalId, state.nextEventId, true).catch((e) => $("workforce-status").textContent = e.message);
      });
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