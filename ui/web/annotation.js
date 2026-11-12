(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const root = $("annotation-view");
  if (!root) return;
  const state = {
    projects: [], tasks: [], project: null, task: null, image: null, imageUrl: null,
    camera: null, tool: "box", boxes: [], polygons: [], draft: [], pointer: null,
    preview: null, claimed: false, resizeObserver: null,
    undoStack: [], redoStack: [], lastHistorySnapshot: null
  };
  const draftSessions = new Map();
  const draftSession = (taskId) => {
    if (!draftSessions.has(taskId)) draftSessions.set(taskId, {
      revision: 0, chain: Promise.resolve(), timer: null, dirty: false,
      payload: null, history: null, conflict: false
    });
    return draftSessions.get(taskId);
  };
  const draftPayload = () => {
    const family = state.project?.task_type;
    let payload;
    if (family === "bounding_boxes") payload = { boxes: state.boxes };
    else if (family === "polygon_segmentation") payload = {
      polygons: state.polygons, draft_points: state.draft.map((point) => [point.x, point.y])
    };
    else if (family === "image_classification" || family === "text_classification") {
      payload = { label: $("annotation-label").value };
    } else {
      try {
        payload = JSON.parse($("annotation-family-json").value || "{}");
        if (!payload || Array.isArray(payload) || typeof payload !== "object") return null;
      } catch (_) { return null; }
    }
    return payload;
  };
  const cloneJson = (value) => JSON.parse(JSON.stringify(value));
  const historySnapshot = () => cloneJson(draftPayload() || {});
  const pushUndo = () => {
    if (!state.task || !state.claimed) return;
    const snapshot = historySnapshot();
    if (state.undoStack.length >= 20) state.undoStack.shift();
    state.undoStack.push(snapshot);
    state.redoStack = [];
    state.lastHistorySnapshot = cloneJson(snapshot);
  };
  const applyHistorySnapshot = (snapshot) => {
    const payload = snapshot || {}, family = state.project?.task_type;
    if (family === "bounding_boxes") state.boxes = Array.isArray(payload.boxes) ? cloneJson(payload.boxes) : [];
    else if (family === "polygon_segmentation") {
      state.polygons = Array.isArray(payload.polygons) ? cloneJson(payload.polygons) : [];
      state.draft = Array.isArray(payload.draft_points) ? payload.draft_points.map((p) => ({ x: p[0], y: p[1] })) : [];
    } else if (family === "image_classification" || family === "text_classification") {
      $("annotation-label").value = typeof payload.label === "string" ? payload.label : "";
    } else $("annotation-family-json").value = JSON.stringify(payload, null, 2);
    state.lastHistorySnapshot = historySnapshot();
    renderCanvas();
    scheduleDraftSave();
  };
  const draftStoragePayload = (payload, snapshot) => {
    const history = snapshot ? cloneJson(snapshot) : { undo: [], redo: [] };
    const encodedSize = () => new TextEncoder().encode(JSON.stringify({ ...payload, _history: history })).length;
    while (encodedSize() > 64 * 1024 - 512 && (history.undo.length || history.redo.length)) {
      if (history.undo.length) history.undo.shift(); else history.redo.shift();
    }
    return { ...payload, _history: history };
  };
  const flushDraftSave = (taskId) => {
    const session = draftSessions.get(taskId);
    if (!session) return Promise.resolve(true);
    if (session.timer) { clearTimeout(session.timer); session.timer = null; }
    session.chain = session.chain.catch(() => {}).then(async () => {
      while (session.dirty && !session.conflict) {
        const payload = session.payload, history = session.history;
        session.dirty = false;
        const result = await api("/v1/annotation/tasks/" + encodeURIComponent(taskId) + "/draft", {
          method: "PUT", body: JSON.stringify({ payload: draftStoragePayload(payload, history), expected_revision: session.revision })
        });
        session.revision = result.draft.revision;
        if (state.task?.task_id === taskId) setStatus("Draft saved · revision " + session.revision);
      }
    });
    return session.chain.catch((error) => {
      if (error.message === "version_conflict") session.conflict = true;
      else {
        session.dirty = true;
        if (!session.timer) session.timer = setTimeout(() => flushDraftSave(taskId), 1800);
      }
      if (state.task?.task_id === taskId) setStatus(
        session.conflict ? "Draft changed in another session. Refresh before continuing to avoid overwriting it." :
          "Draft save failed; your changes are retained locally and will retry: " + error.message, true
      );
      return false;
    }).then(() => !session.dirty && !session.conflict);
  };
  const scheduleDraftSave = () => {
    if (!state.task || !state.claimed) return;
    const taskId = state.task.task_id, session = draftSession(taskId);
    const payload = draftPayload();
    state.lastHistorySnapshot = historySnapshot();
    if (!payload) {
      setStatus("Draft not saved: finish entering valid JSON first.", true);
      return;
    }
    const history = { undo: cloneJson(state.undoStack), redo: cloneJson(state.redoStack) };
    const stored = draftStoragePayload(payload, history);
    if (new TextEncoder().encode(JSON.stringify(stored)).length > 64 * 1024) {
      setStatus("Draft exceeds the 64 KiB storage limit; reduce the annotation before continuing.", true);
      return;
    }
    state.undoStack = stored._history.undo;
    state.redoStack = stored._history.redo;
    session.payload = payload;
    session.history = stored._history;
    session.dirty = true;
    if (session.timer) clearTimeout(session.timer);
    session.timer = setTimeout(() => flushDraftSave(taskId), 650);
    setStatus("Changes pending autosave…");
  };
  const restoreDraft = async (taskId) => {
    const result = await api("/v1/annotation/tasks/" + encodeURIComponent(taskId) + "/draft");
    if (state.task?.task_id !== taskId) return;
    const draft = result.draft;
    const session = draftSession(taskId);
    session.revision = draft ? draft.revision : 0;
    session.conflict = false; session.dirty = false; session.history = { undo: [], redo: [] };
    if (!draft) {
      state.undoStack = []; state.redoStack = []; state.lastHistorySnapshot = historySnapshot();
      return;
    }
    const payload = cloneJson(draft.payload || {});
    const history = payload._history || {};
    delete payload._history;
    state.undoStack = Array.isArray(history.undo) ? history.undo.slice(-20) : [];
    state.redoStack = Array.isArray(history.redo) ? history.redo.slice(-20) : [];
    const family = state.project?.task_type;
    if (family === "bounding_boxes") state.boxes = Array.isArray(payload.boxes) ? payload.boxes : [];
    else if (family === "polygon_segmentation") {
      state.polygons = Array.isArray(payload.polygons) ? payload.polygons : [];
      state.draft = Array.isArray(payload.draft_points) ? payload.draft_points.map((p) => ({ x: p[0], y: p[1] })) : [];
    } else if (family === "image_classification" || family === "text_classification") {
      $("annotation-label").value = typeof payload.label === "string" ? payload.label : "";
    } else $("annotation-family-json").value = JSON.stringify(payload, null, 2);
    state.lastHistorySnapshot = historySnapshot();
    setStatus("Recovered saved draft · revision " + draft.revision);
  };
  const api = async (path, options = {}) => {
    const headers = { ...(options.headers || {}), Authorization: "Bearer " + sessionStorage.getItem("yuki_token") };
    if (options.body && !(options.body instanceof Blob)) headers["Content-Type"] = "application/json";
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || "Request failed (" + response.status + ")");
    }
    if (response.status === 204) return {};
    const type = response.headers.get("content-type") || "";
    return type.includes("application/json") ? response.json() : response.blob();
  };
  const setStatus = (message, error = false) => {
    $("annotation-status").textContent = message;
    $("annotation-status").dataset.error = error ? "true" : "false";
  };
  const selectedProject = () => state.projects.find((item) => item.project_id === $("annotation-project").value) || null;
  const selectedTask = () => state.tasks.find((item) => item.task_id === $("annotation-task").value) || null;
  const setView = (open) => {
    root.hidden = !open;
    $("messages").hidden = open;
    $("composer-wrap").hidden = open;
    $("open-annotation").setAttribute("aria-pressed", open ? "true" : "false");
    if (open) {
      $("annotation-project").focus();
      resizeCanvas();
    }
  };
  const loadProjects = async () => {
    const data = await api("/v1/annotation/projects");
    state.projects = data.projects || [];
    const select = $("annotation-project");
    select.replaceChildren();
    state.projects.forEach((project) => {
      const option = document.createElement("option");
      option.value = project.project_id;
      option.textContent = project.name + " · " + project.task_type;
      select.append(option);
    });
    if (!state.projects.length) {
      const option = document.createElement("option");
      option.textContent = "No annotation projects available";
      option.value = "";
      select.append(option);
    }
    if (state.project && state.projects.some((p) => p.project_id === state.project.project_id)) {
      select.value = state.project.project_id;
    }
    state.project = selectedProject();
    await loadTasks();
  };
  const loadTasks = async () => {
    if (state.task?.task_id) {
      const previousTaskId = state.task.task_id;
      const saved = await flushDraftSave(previousTaskId);
      if (!saved) throw new Error("Save or resolve the current draft before changing projects or refreshing tasks.");
      draftSessions.delete(previousTaskId);
    }
    state.task = null; state.tasks = []; state.claimed = false; state.boxes = []; state.polygons = []; state.draft = [];
    clearImage();
    const select = $("annotation-task");
    select.replaceChildren();
    if (!state.project) {
      select.add(new Option("Select a project first", ""));
      renderEditor();
      return;
    }
    const data = await api("/v1/annotation/projects/" + encodeURIComponent(state.project.project_id) + "/tasks");
    state.tasks = data.tasks || [];
    state.tasks.forEach((task) => {
      const option = document.createElement("option");
      option.value = task.task_id;
      option.textContent = task.external_ref + " · " + task.status + " · v" + task.version;
      select.append(option);
    });
    if (!state.tasks.length) select.add(new Option("No tasks in this project", ""));
    state.task = selectedTask();
    await selectTask();
  };
  const clearImage = () => {
    if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
    state.imageUrl = null; state.image = null; state.camera = null;
    renderCanvas();
  };
  const loadImage = async () => {
    clearImage();
    if (!state.task || !state.task.asset_ref) {
      setStatus("This task has no attached image. Use the task-family editor below.", true);
      return;
    }
    if (!/^[a-f0-9]{32}$/.test(state.task.asset_ref)) {
      setStatus("The task asset reference is invalid; no image was requested.", true);
      return;
    }
    const requestedTaskId = state.task.task_id;
    const requestedProjectId = state.project.project_id;
    try {
      const blob = await api("/v1/annotation/projects/" + encodeURIComponent(requestedProjectId) +
        "/assets/" + encodeURIComponent(state.task.asset_ref) + "/preview");
      if (state.task?.task_id !== requestedTaskId || state.project?.project_id !== requestedProjectId) return;
      if (!(blob instanceof Blob) || blob.type !== "image/png") throw new Error("invalid_preview_response");
      const url = URL.createObjectURL(blob);
      const image = new Image();
      image.onload = () => {
        if (state.task?.task_id !== requestedTaskId || state.project?.project_id !== requestedProjectId) {
          URL.revokeObjectURL(url); return;
        }
        if (state.imageUrl && state.imageUrl !== url) URL.revokeObjectURL(state.imageUrl);
        state.imageUrl = url; state.image = image;
        state.camera = fitCamera(image.naturalWidth, image.naturalHeight, canvasSize().width, canvasSize().height);
        renderCanvas();
        setStatus("Secure preview loaded. Annotation coordinates are stored relative to the original image.");
      };
      image.onerror = () => { URL.revokeObjectURL(url); setStatus("The preview image could not be decoded.", true); };
      image.src = url;
    } catch (error) {
      setStatus("Image preview unavailable: " + error.message, true);
    }
  };
  const selectTask = async () => {
    const previousTaskId = state.task?.task_id;
    const nextTaskId = $("annotation-task").value;
    if (previousTaskId && previousTaskId !== nextTaskId) {
      const saved = await flushDraftSave(previousTaskId);
      if (!saved) { $("annotation-task").value = previousTaskId; return; }
      draftSessions.delete(previousTaskId);
    }
    state.task = selectedTask();
    state.claimed = Boolean(state.task && state.project?.status === "active" &&
      ["in_progress", "rework"].includes(state.task.status));
    state.boxes = []; state.polygons = []; state.draft = [];
    state.undoStack = []; state.redoStack = []; state.lastHistorySnapshot = null;
    $("annotation-label").value = "";
    $("annotation-family-json").value = "{}";
    if (!state.task) {
      clearImage(); renderEditor(); setStatus("Choose an annotation task.");
      return;
    }
    $("annotation-task-meta").textContent = "Task " + state.task.external_ref + " · " + state.task.status + " · version " + state.task.version;
    $("claim-annotation-task").disabled = !["pending", "rework"].includes(state.task.status) || state.project.status !== "active";
    setTool(state.project.task_type === "polygon_segmentation" ? "polygon" : state.project.task_type === "image_classification" ? "pan" : "box");
    $("submit-annotation").disabled = !state.claimed;
    if (["in_progress", "rework"].includes(state.task.status)) await restoreDraft(state.task.task_id);
    if (state.task?.task_id !== selectedTask()?.task_id) return;
    await loadImage();
    renderEditor();
  };
  const canvas = $("annotation-canvas");
  const canvasSize = () => ({ width: Math.max(1, canvas.clientWidth), height: Math.max(1, canvas.clientHeight) });
  const fitCamera = (iw, ih, vw, vh) => {
    const scale = Math.min(vw / iw, vh / ih);
    return { iw, ih, vw, vh, base: scale, zoom: 1, x: (vw - iw * scale) / 2, y: (vh - ih * scale) / 2 };
  };
  const imagePoint = (x, y) => {
    const c = state.camera;
    if (!c) return null;
    const width = c.iw * c.base * c.zoom, height = c.ih * c.base * c.zoom;
    if (x < c.x || y < c.y || x > c.x + width || y > c.y + height) return null;
    return { x: (x - c.x) / width, y: (y - c.y) / height };
  };
  const viewportPoint = (point) => {
    const c = state.camera;
    return { x: c.x + point.x * c.iw * c.base * c.zoom, y: c.y + point.y * c.ih * c.base * c.zoom };
  };
  const resizeCanvas = () => {
    if (!canvas || root.hidden) return;
    const size = canvasSize(), ratio = Math.min(window.devicePixelRatio || 1, 3);
    const width = Math.round(size.width * ratio), height = Math.round(size.height * ratio);
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    if (state.image && state.camera) {
      const old = state.camera;
      const next = fitCamera(old.iw, old.ih, size.width, size.height);
      next.zoom = old.zoom;
      const center = { x: size.width / 2, y: size.height / 2 };
      const source = { x: (center.x - old.x) / (old.base * old.zoom * old.iw), y: (center.y - old.y) / (old.base * old.zoom * old.ih) };
      next.x = center.x - source.x * next.iw * next.base * next.zoom;
      next.y = center.y - source.y * next.ih * next.base * next.zoom;
      state.camera = clampCamera(next);
    }
    renderCanvas();
  };
  const clampCamera = (c) => {
    const w = c.iw * c.base * c.zoom, h = c.ih * c.base * c.zoom;
    const mx = Math.min(32, w, c.vw), my = Math.min(32, h, c.vh);
    c.x = Math.min(c.vw - mx, Math.max(mx - w, c.x));
    c.y = Math.min(c.vh - my, Math.max(my - h, c.y));
    return c;
  };
  const drawBox = (ctx, box, color) => {
    const p = viewportPoint({ x: box.x, y: box.y });
    ctx.strokeStyle = color; ctx.lineWidth = 2;
    ctx.strokeRect(p.x, p.y, box.width * state.camera.iw * state.camera.base * state.camera.zoom,
      box.height * state.camera.ih * state.camera.base * state.camera.zoom);
    ctx.fillStyle = color; ctx.font = "12px system-ui";
    ctx.fillText(box.label, p.x + 4, Math.max(14, p.y - 5));
  };
  const renderCanvas = () => {
    if (!canvas) return;
    $("annotation-undo").disabled = !state.undoStack.length;
    $("annotation-redo").disabled = !state.redoStack.length;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 3);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const size = canvasSize();
    ctx.clearRect(0, 0, size.width, size.height);
    ctx.fillStyle = "#101216"; ctx.fillRect(0, 0, size.width, size.height);
    if (!state.image || !state.camera) {
      ctx.fillStyle = "#9aa0ad"; ctx.textAlign = "center"; ctx.font = "13px system-ui";
      ctx.fillText("Select a task with a clean image asset to begin", size.width / 2, size.height / 2);
      return;
    }
    const c = state.camera, scale = c.base * c.zoom;
    ctx.drawImage(state.image, c.x, c.y, c.iw * scale, c.ih * scale);
    state.boxes.forEach((box) => drawBox(ctx, box, "#f2c879"));
    state.polygons.forEach((poly) => {
      ctx.beginPath(); poly.points.forEach((point, index) => {
        const p = viewportPoint({ x: point[0], y: point[1] });
        if (!index) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
      }); ctx.closePath(); ctx.strokeStyle = "#a997ff"; ctx.lineWidth = 2; ctx.stroke();
    });
    if (state.tool === "box" && state.pointer && state.pointer.start && state.pointer.current) {
      const a = state.pointer.start, b = state.pointer.current;
      ctx.strokeStyle = "#f2c879"; ctx.setLineDash([5, 4]);
      ctx.strokeRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
      ctx.setLineDash([]);
    }
    if (state.draft.length) {
      ctx.beginPath(); state.draft.forEach((point, index) => {
        const p = viewportPoint(point); if (!index) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
      });
      ctx.strokeStyle = "#a997ff"; ctx.lineWidth = 2; ctx.stroke();
      state.draft.forEach((point) => { const p = viewportPoint(point); ctx.fillStyle = "#a997ff"; ctx.beginPath(); ctx.arc(p.x, p.y, 4, 0, Math.PI * 2); ctx.fill(); });
    }
    $("annotation-zoom-level").textContent = Math.round(c.zoom * 100) + "%";
  };
  const pointerPosition = (event) => {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const zoomAt = (point, factor) => {
    const c = state.camera; if (!c) return;
    const zoom = Math.min(16, Math.max(.25, c.zoom * factor));
    if (zoom === c.zoom) return;
    const sourceX = (point.x - c.x) / (c.base * c.zoom), sourceY = (point.y - c.y) / (c.base * c.zoom);
    c.zoom = zoom; c.x = point.x - sourceX * c.base * zoom; c.y = point.y - sourceY * c.base * zoom;
    clampCamera(c); renderCanvas();
  };
  const setTool = (tool) => {
    state.tool = tool; state.pointer = null;
    ["box", "polygon", "pan"].forEach((name) => $("tool-" + name).setAttribute("aria-pressed", name === tool ? "true" : "false"));
    $("finish-polygon").hidden = tool !== "polygon";
    canvas.style.cursor = tool === "pan" ? "grab" : tool === "polygon" ? "crosshair" : "crosshair";
    renderCanvas();
  };
  const addBox = (start, end) => {
    const c = state.camera;
    const a = imagePoint(start.x, start.y), b = imagePoint(end.x, end.y);
    if (!a || !b) throw new Error("Drag must begin and end inside the image.");
    const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y), width = Math.abs(a.x - b.x), height = Math.abs(a.y - b.y);
    if (width * c.iw < 1 || height * c.ih < 1) throw new Error("Selection is too small.");
    state.boxes.push({ label: requiredLabel(), x, y, width, height });
  };
  const requiredLabel = () => {
    const label = $("annotation-label").value.trim();
    if (!label || label.length > 256) throw new Error("Enter a label (maximum 256 characters).");
    return label;
  };
  canvas.addEventListener("pointerdown", (event) => {
    if (!state.image || !state.claimed || !["bounding_boxes", "polygon_segmentation", "image_classification"].includes(state.project?.task_type)) return;
    const p = pointerPosition(event);
    if (state.tool === "pan" || event.button === 1 || event.button === 2) {
      state.pointer = { id: event.pointerId, mode: "pan", x: p.x, y: p.y };
    } else if (state.tool === "box") {
      if (!imagePoint(p.x, p.y)) return;
      state.pointer = { id: event.pointerId, mode: "box", start: p, current: p };
    } else if (state.tool === "polygon") {
      const point = imagePoint(p.x, p.y);
      if (!point) return;
      if (state.draft.length >= 2048) { setStatus("Polygon vertex limit reached.", true); return; }
      pushUndo();
      state.draft.push(point);
      scheduleDraftSave();
    }
    if (state.pointer) { canvas.setPointerCapture(event.pointerId); event.preventDefault(); }
    renderCanvas();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!state.pointer || state.pointer.id !== event.pointerId) return;
    const p = pointerPosition(event);
    if (state.pointer.mode === "pan") {
      const dx = p.x - state.pointer.x, dy = p.y - state.pointer.y;
      state.camera.x += dx; state.camera.y += dy; clampCamera(state.camera);
      state.pointer.x = p.x; state.pointer.y = p.y;
    } else state.pointer.current = p;
    renderCanvas();
  });
  const finishPointer = (event, commit) => {
    if (!state.pointer || state.pointer.id !== event.pointerId) return;
    const pointer = state.pointer; state.pointer = null;
    if (pointer.mode === "box" && commit) {
      try { pushUndo(); addBox(pointer.start, pointerPosition(event)); setStatus("Bounding box added. Review it before submitting."); scheduleDraftSave(); }
      catch (error) { setStatus(error.message, true); }
    }
    renderCanvas();
  };
  canvas.addEventListener("pointerup", (event) => finishPointer(event, true));
  canvas.addEventListener("pointercancel", (event) => finishPointer(event, false));
  canvas.addEventListener("lostpointercapture", (event) => finishPointer(event, false));
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  canvas.addEventListener("wheel", (event) => {
    if (!state.camera) return;
    event.preventDefault(); zoomAt(pointerPosition(event), event.deltaY < 0 ? 1.15 : 1 / 1.15);
  }, { passive: false });
  canvas.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { state.pointer = null; state.draft = []; renderCanvas(); setStatus("Uncommitted gesture cancelled."); }
    if (event.key === "Enter" && state.tool === "polygon") $("finish-polygon").click();
    if (event.key === "+" || event.key === "=") zoomAt({ x: canvas.clientWidth / 2, y: canvas.clientHeight / 2 }, 1.2);
    if (event.key === "-") zoomAt({ x: canvas.clientWidth / 2, y: canvas.clientHeight / 2 }, 1 / 1.2);
    if (event.key.startsWith("Arrow") && state.camera) {
      const delta = event.shiftKey ? 48 : 16;
      state.camera.x += event.key === "ArrowLeft" ? delta : event.key === "ArrowRight" ? -delta : 0;
      state.camera.y += event.key === "ArrowUp" ? delta : event.key === "ArrowDown" ? -delta : 0;
      clampCamera(state.camera); renderCanvas(); event.preventDefault();
    }
  });
  const renderEditor = () => {
    const task = state.task;
    const family = state.project?.task_type;
    const classification = ["image_classification", "text_classification"].includes(family);
    $("annotation-family-editor").hidden = !task || ["bounding_boxes", "polygon_segmentation", "image_classification", "text_classification"].includes(family);
    $("annotation-label-wrap").hidden = !task || !(["bounding_boxes", "polygon_segmentation"].includes(family) || classification);
    $("annotation-tools").hidden = !task || !["bounding_boxes", "polygon_segmentation"].includes(family);
    $("annotation-canvas-panel").hidden = !task || !["bounding_boxes", "polygon_segmentation", "image_classification"].includes(family);
    $("annotation-upload-row").hidden = !state.project || state.project.status !== "active" || !["bounding_boxes", "polygon_segmentation", "image_classification"].includes(family);
    $("annotation-manual-task-row").hidden = !state.project || state.project.status !== "active";
    $("annotation-task-input").textContent = task
      ? (state.project.instructions || "") + "\\n\\nRubric: " + JSON.stringify(state.project.rubric || {}, null, 2) + "\\n\\nInput: " + JSON.stringify(task.input_data || {}, null, 2)
      : "Select a task to view its input.";
    $("annotation-family-json").disabled = !state.claimed;
    $("annotation-label").disabled = !state.claimed;
    $("submit-annotation").disabled = !state.claimed;
    if (task && state.project?.task_type === "image_classification") $("annotation-label-caption").textContent = "Classification label";
    else $("annotation-label-caption").textContent = "Object / polygon label";
    if (task && state.project?.task_type === "image_classification") $("annotation-tools").hidden = true;
  };
  $("claim-annotation-task").addEventListener("click", async () => {
    if (!state.task) return;
    const button = $("claim-annotation-task"); button.disabled = true;
    try {
      const data = await api("/v1/annotation/tasks/" + encodeURIComponent(state.task.task_id) + "/claim", {
        method: "POST", body: JSON.stringify({ expected_version: state.task.version })
      });
      state.task = data.task; state.claimed = true;
      await restoreDraft(state.task.task_id);
      renderCanvas();
      $("annotation-task-meta").textContent = "Task " + state.task.external_ref + " · in progress · version " + state.task.version;
      $("submit-annotation").disabled = false; setStatus("Task claimed. Complete the annotation and review it before submission.");
      renderEditor();
      $("annotation-task").value = state.task.task_id;
    } catch (error) { setStatus(error.message, true); await loadTasks(); }
    finally { button.disabled = !state.task || state.project?.status !== "active" || !["pending", "rework"].includes(state.task.status); }
  });
  $("submit-annotation").addEventListener("click", async () => {
    if (!state.task || !state.claimed) return;
    let payload;
    try {
      const type = state.project.task_type;
      if (type === "bounding_boxes") {
        if (!state.boxes.length) throw new Error("Add at least one bounding box.");
        payload = { boxes: state.boxes };
      } else if (type === "polygon_segmentation") {
        if (state.draft.length) throw new Error("Finish or cancel the polygon draft first.");
        if (!state.polygons.length) throw new Error("Add at least one polygon.");
        payload = { polygons: state.polygons };
      } else if (type === "image_classification" || type === "text_classification") payload = { label: requiredLabel() };
      else {
        payload = JSON.parse($("annotation-family-json").value);
        if (!payload || Array.isArray(payload) || typeof payload !== "object") throw new Error("Annotation payload must be a JSON object.");
      }
      if (!window.confirm("Submit this annotation for review? This records your human confirmation. Yuki will not send it to a marketplace.")) return;
      const saved = await flushDraftSave(state.task.task_id);
      const session = draftSessions.get(state.task.task_id);
      if (!saved || session?.dirty || session?.conflict) throw new Error("Draft must be saved successfully before submission.");
      const button = $("submit-annotation"); button.disabled = true;
      const result = await api("/v1/annotation/tasks/" + encodeURIComponent(state.task.task_id) + "/submit", {
        method: "POST", body: JSON.stringify({
          payload, expected_version: state.task.version, ai_assisted: false, human_confirmed: true
        })
      });
      draftSessions.delete(state.task.task_id);
      await loadTasks();
      setStatus("Annotation submitted for review · " + result.submission.submission_id);
    } catch (error) { setStatus(error.message, true); }
    finally { $("submit-annotation").disabled = !state.claimed; }
  });
  $("finish-polygon").addEventListener("click", () => {
    try {
      if (state.draft.length < 3) throw new Error("A polygon needs at least three vertices.");
      const points = state.draft.map((p) => [p.x, p.y]);
      const area = points.reduce((sum, p, i) => sum + p[0] * points[(i + 1) % points.length][1] - points[(i + 1) % points.length][0] * p[1], 0) / 2;
      if (Math.abs(area) < 1e-8) throw new Error("Polygon area is too small.");
      const cross = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
      const onSegment = (a, b, p) => Math.min(a[0], b[0]) - 1e-9 <= p[0] && p[0] <= Math.max(a[0], b[0]) + 1e-9 && Math.min(a[1], b[1]) - 1e-9 <= p[1] && p[1] <= Math.max(a[1], b[1]) + 1e-9;
      const intersects = (a, b, c, d) => {
        const ab1 = cross(a, b, c), ab2 = cross(a, b, d), cd1 = cross(c, d, a), cd2 = cross(c, d, b);
        if (((ab1 > 1e-9 && ab2 < -1e-9) || (ab1 < -1e-9 && ab2 > 1e-9)) && ((cd1 > 1e-9 && cd2 < -1e-9) || (cd1 < -1e-9 && cd2 > 1e-9))) return true;
        return Math.abs(ab1) <= 1e-9 && onSegment(a, b, c) || Math.abs(ab2) <= 1e-9 && onSegment(a, b, d) || Math.abs(cd1) <= 1e-9 && onSegment(c, d, a) || Math.abs(cd2) <= 1e-9 && onSegment(c, d, b);
      };
      for (let i = 0; i < points.length; i++) {
        const a = points[i], b = points[(i + 1) % points.length];
        if (Math.hypot(a[0] - b[0], a[1] - b[1]) <= 1e-9) throw new Error("Polygon has duplicate adjacent vertices.");
        for (let j = i + 1; j < points.length; j++) {
          const c = points[j], d = points[(j + 1) % points.length];
          const adjacent = j === i + 1 || (i === 0 && j === points.length - 1);
          if (adjacent) {
            const shared = j === i + 1 ? b : a;
            const otherA = j === i + 1 ? a : b;
            const otherB = j === i + 1 ? d : c;
            if (Math.abs(cross(otherA, shared, otherB)) <= 1e-9 &&
                (onSegment(otherA, shared, otherB) || onSegment(shared, otherB, otherA))) {
              throw new Error("Adjacent polygon edges overlap.");
            }
            continue;
          }
          if (intersects(a, b, c, d)) throw new Error("Polygon edges intersect.");
        }
      }
      pushUndo();
      state.polygons.push({ label: requiredLabel(), points }); state.draft = [];
      setStatus("Polygon added. Review it before submitting."); renderCanvas(); scheduleDraftSave();
    } catch (error) { setStatus(error.message, true); }
  });
  $("cancel-annotation-draft").addEventListener("click", () => {
    pushUndo();
    state.boxes = []; state.polygons = []; state.draft = []; state.pointer = null;
    setStatus("Unsubmitted geometry cleared."); renderCanvas(); scheduleDraftSave();
  });
  $("annotation-project").addEventListener("change", async () => {
    const previousProjectId = state.project?.project_id;
    state.project = selectedProject();
    try { await loadTasks(); }
    catch (error) {
      if (previousProjectId) $("annotation-project").value = previousProjectId;
      state.project = state.projects.find((item) => item.project_id === previousProjectId) || null;
      setStatus(error.message, true);
    }
  });
  $("annotation-task").addEventListener("change", () => selectTask().catch((error) => setStatus(error.message, true)));
  $("refresh-annotation").addEventListener("click", () => loadProjects().then(() => setStatus("Annotation projects refreshed.")).catch((error) => setStatus(error.message, true)));
  $("annotation-create-project-button").addEventListener("click", () => {
    $("annotation-create-project").hidden = !$("annotation-create-project").hidden;
    if (!$("annotation-create-project").hidden) $("annotation-new-name").focus();
  });
  $("annotation-cancel-project").addEventListener("click", () => { $("annotation-create-project").hidden = true; });
  $("annotation-create-project").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const rubric = JSON.parse($("annotation-new-rubric").value);
      if (!rubric || Array.isArray(rubric) || typeof rubric !== "object") throw new Error("Rubric must be a JSON object.");
      const data = await api("/v1/annotation/projects", {
        method: "POST", body: JSON.stringify({
          name: $("annotation-new-name").value.trim(),
          task_type: $("annotation-new-type").value,
          instructions: $("annotation-new-instructions").value.trim(),
          rubric, ai_assistance_mode: "human_only"
        })
      });
      state.project = data.project;
      $("annotation-create-project").reset();
      $("annotation-new-rubric").value = "{}";
      $("annotation-create-project").hidden = true;
      await loadProjects();
      setStatus("Human-only annotation project created.");
    } catch (error) { setStatus(error.message, true); }
  });
  $("annotation-create-task").addEventListener("click", async () => {
    if (!state.project || state.project.status !== "active") { setStatus("Choose an active project first.", true); return; }
    const externalRef = $("annotation-new-task-ref").value.trim();
    if (!externalRef || externalRef.length > 256) { setStatus("Enter a task reference (maximum 256 characters).", true); return; }
    let inputData;
    try {
      inputData = JSON.parse($("annotation-new-task-input").value);
      if (!inputData || Array.isArray(inputData) || typeof inputData !== "object") throw new Error("Task input must be a JSON object.");
    } catch (error) { setStatus(error.message, true); return; }
    const button = $("annotation-create-task"); button.disabled = true;
    try {
      const created = await api("/v1/annotation/projects/" + encodeURIComponent(state.project.project_id) + "/tasks", {
        method: "POST", body: JSON.stringify({ external_ref: externalRef, input_data: inputData })
      });
      $("annotation-new-task-ref").value = "";
      $("annotation-new-task-input").value = "{}";
      await loadTasks();
      if (created.task?.task_id) {
        $("annotation-task").value = created.task.task_id;
        await selectTask();
      }
      setStatus("Task created. Claim it when you are ready to annotate.");
    } catch (error) { setStatus(error.message, true); }
    finally { button.disabled = false; }
  });
  $("annotation-upload-button").addEventListener("click", async () => {
    const file = $("annotation-upload-file").files[0];
    if (!state.project) { setStatus("Create or select a project first.", true); return; }
    if (!file) { setStatus("Choose an image file first.", true); return; }
    if (file.size < 1 || file.size > 25 * 1024 * 1024) { setStatus("Image must be between 1 byte and 25 MiB.", true); return; }
    if (file.type && !["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type)) { setStatus("Choose a PNG, JPEG, WebP or GIF image.", true); return; }
    const button = $("annotation-upload-button"); button.disabled = true;
    try {
      const uploaded = await api("/v1/annotation/projects/" + encodeURIComponent(state.project.project_id) + "/assets", {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file
      });
      if (uploaded.scan_status !== "clean") {
        setStatus("Image stored in quarantine. A configured scanner must mark it clean before it can become a task.", true);
        return;
      }
      let created;
      try {
        created = await api("/v1/annotation/projects/" + encodeURIComponent(state.project.project_id) + "/tasks", {
          method: "POST", body: JSON.stringify({
            external_ref: "upload-" + uploaded.asset_id.slice(0, 16),
            asset_ref: uploaded.asset_id,
            input_data: { filename: file.name.slice(0, 200) }
          })
        });
      } catch (error) {
        try {
          await api("/v1/annotation/projects/" + encodeURIComponent(state.project.project_id) +
            "/assets/" + encodeURIComponent(uploaded.asset_id), { method: "DELETE" });
        } catch (_) {}
        throw error;
      }
      $("annotation-upload-file").value = "";
      await loadTasks();
      if (created.task?.task_id) {
        $("annotation-task").value = created.task.task_id;
        await selectTask();
      }
      setStatus("Clean image uploaded and annotation task created.");
    } catch (error) { setStatus(error.message, true); }
    finally { button.disabled = false; }
  });
  $("open-annotation").addEventListener("click", async () => {
    const open = root.hidden; setView(open);
    if (open) try { await loadProjects(); } catch (error) { setStatus(error.message, true); }
  });
  $("close-annotation").addEventListener("click", () => setView(false));
  $("tool-box").addEventListener("click", () => setTool("box"));
  $("tool-polygon").addEventListener("click", () => setTool("polygon"));
  $("tool-pan").addEventListener("click", () => setTool("pan"));
  $("zoom-in").addEventListener("click", () => zoomAt({ x: canvas.clientWidth / 2, y: canvas.clientHeight / 2 }, 1.25));
  $("zoom-out").addEventListener("click", () => zoomAt({ x: canvas.clientWidth / 2, y: canvas.clientHeight / 2 }, .8));
  $("zoom-reset").addEventListener("click", () => {
    if (state.camera) state.camera = fitCamera(state.camera.iw, state.camera.ih, canvas.clientWidth, canvas.clientHeight);
    renderCanvas();
  });
  const trackTextHistory = () => {
    const current = historySnapshot();
    if (state.lastHistorySnapshot && JSON.stringify(current) !== JSON.stringify(state.lastHistorySnapshot)) {
      if (state.undoStack.length >= 20) state.undoStack.shift();
      state.undoStack.push(state.lastHistorySnapshot);
      state.redoStack = [];
    }
    state.lastHistorySnapshot = current;
    renderCanvas();
    scheduleDraftSave();
  };
  $("annotation-label").addEventListener("input", () => { renderCanvas(); trackTextHistory(); });
  $("annotation-family-json").addEventListener("input", trackTextHistory);
  $("annotation-undo").addEventListener("click", () => {
    if (!state.undoStack.length) return;
    const current = historySnapshot(), previous = state.undoStack.pop();
    if (state.redoStack.length >= 20) state.redoStack.shift();
    state.redoStack.push(current);
    applyHistorySnapshot(previous);
  });
  $("annotation-redo").addEventListener("click", () => {
    if (!state.redoStack.length) return;
    const current = historySnapshot(), next = state.redoStack.pop();
    if (state.undoStack.length >= 20) state.undoStack.shift();
    state.undoStack.push(current);
    applyHistorySnapshot(next);
  });
  if (window.ResizeObserver) {
    state.resizeObserver = new ResizeObserver(resizeCanvas);
    state.resizeObserver.observe(canvas);
  } else window.addEventListener("resize", resizeCanvas);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && state.task) flushDraftSave(state.task.task_id);
  });
  window.addEventListener("pagehide", () => {
    if (state.task) flushDraftSave(state.task.task_id);
    if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
    if (state.resizeObserver) state.resizeObserver.disconnect();
  });
  const loadReviewerQueue = async () => {
    const projectId = $("annotation-review-project-id").value.trim();
    const queue = $("annotation-review-queue");
    if (!/^[0-9a-f]{32}$/.test(projectId)) {
      $("annotation-review-status").textContent = "Enter a valid 32-character project ID.";
      queue.replaceChildren();
      return;
    }
    $("annotation-review-status").textContent = "Loading authorized review queue…";
    queue.replaceChildren();
    try {
      const result = await api("/v1/annotation/projects/" + encodeURIComponent(projectId) + "/review-queue");
      queue.replaceChildren();
      if (!result.submissions?.length) {
        const empty = document.createElement("p");
        empty.className = "annotation-hint";
        empty.textContent = "No submissions are waiting for review.";
        queue.append(empty);
      }
      for (const item of result.submissions || []) {
        const submission = item.submission;
        const card = document.createElement("article");
        card.className = "annotation-review-card";
        const heading = document.createElement("h4");
        heading.textContent = "Task " + submission.task_id;
        const meta = document.createElement("p");
        meta.className = "annotation-hint";
        meta.textContent = "Task " + item.external_ref + " · Submission " + submission.submission_id +
          " · " + (submission.ai_assisted ? "AI-assisted (disclosed)" : "Human-only");
        const instructions = document.createElement("p");
        instructions.textContent = item.instructions;
        const input = document.createElement("pre");
        input.className = "annotation-review-payload";
        input.textContent = "Task input\n" + JSON.stringify(item.input_data, null, 2);
        const rubric = document.createElement("pre");
        rubric.className = "annotation-review-payload";
        rubric.textContent = "Rubric\n" + JSON.stringify(item.rubric, null, 2);
        const payload = document.createElement("pre");
        payload.className = "annotation-review-payload";
        payload.textContent = JSON.stringify(submission.payload, null, 2);
        const notesLabel = document.createElement("label");
        notesLabel.className = "annotation-field";
        notesLabel.textContent = "Reviewer notes";
        const notes = document.createElement("textarea");
        notes.rows = 3; notes.maxLength = 4000;
        notes.placeholder = "Explain the decision or requested corrections";
        notesLabel.append(notes);
        const actions = document.createElement("div");
        actions.className = "annotation-toolbar";
        for (const decision of ["accepted", "rework"]) {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = decision === "accepted" ? "Accept" : "Request rework";
          button.addEventListener("click", async () => {
            button.disabled = true;
            try {
              await api("/v1/annotation/submissions/" + encodeURIComponent(submission.submission_id) + "/review", {
                method: "POST",
                body: JSON.stringify({ decision, notes: notes.value, project_id: projectId })
              });
              $("annotation-review-status").textContent =
                decision === "accepted" ? "Submission accepted." : "Rework requested.";
              await loadReviewerQueue();
            } catch (error) {
              $("annotation-review-status").textContent = "Review failed: " + error.message;
              button.disabled = false;
            }
          });
          actions.append(button);
        }
        card.append(heading, meta, instructions, input, rubric, payload, notesLabel, actions);
        queue.append(card);
      }
      $("annotation-review-status").textContent = "Loaded " + (result.submissions || []).length + " pending submission(s).";
    } catch (error) {
      $("annotation-review-status").textContent = "Reviewer access unavailable: " + error.message;
    }
  };
  $("annotation-review-load").addEventListener("click", loadReviewerQueue);
  $("annotation-review-project-id").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); loadReviewerQueue(); }
  });
  setTool("box");
  renderCanvas();
})();