(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const root = $("annotation-view");
  if (!root) return;
  const state = {
    projects: [], tasks: [], project: null, task: null, image: null, imageUrl: null,
    camera: null, tool: "box", boxes: [], polygons: [], draft: [], pointer: null,
    preview: null, claimed: false, resizeObserver: null
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
    try {
      const blob = await api("/v1/annotation/projects/" + encodeURIComponent(state.project.project_id) +
        "/assets/" + encodeURIComponent(state.task.asset_ref) + "/preview");
      if (!(blob instanceof Blob) || blob.type !== "image/png") throw new Error("invalid_preview_response");
      const url = URL.createObjectURL(blob);
      const image = new Image();
      image.onload = () => {
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
    state.task = selectedTask(); state.claimed = Boolean(state.task && state.task.status === "in_progress");
    state.boxes = []; state.polygons = []; state.draft = [];
    $("annotation-label").value = "";
    if (!state.task) {
      clearImage(); renderEditor(); setStatus("Choose an annotation task.");
      return;
    }
    $("annotation-task-meta").textContent = "Task " + state.task.external_ref + " · " + state.task.status + " · version " + state.task.version;
    $("claim-annotation-task").disabled = state.task.status !== "pending";
    $("submit-annotation").disabled = !state.claimed;
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
    if (!state.image || !state.claimed) return;
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
      state.draft.push(point);
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
      try { addBox(pointer.start, pointerPosition(event)); setStatus("Bounding box added. Review it before submitting."); }
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
    $("annotation-family-json").value = "{}";
    $("annotation-task-input").textContent = task
      ? (state.project.instructions || "") + "\\n\\nInput: " + JSON.stringify(task.input_data || {}, null, 2)
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
      $("annotation-task-meta").textContent = "Task " + state.task.external_ref + " · in progress · version " + state.task.version;
      $("submit-annotation").disabled = false; setStatus("Task claimed. Complete the annotation and review it before submission.");
      renderEditor();
      $("annotation-task").value = state.task.task_id;
    } catch (error) { setStatus(error.message, true); await loadTasks(); }
    finally { button.disabled = !state.task || state.task.status !== "pending"; }
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
      const button = $("submit-annotation"); button.disabled = true;
      const result = await api("/v1/annotation/tasks/" + encodeURIComponent(state.task.task_id) + "/submit", {
        method: "POST", body: JSON.stringify({
          payload, expected_version: state.task.version, ai_assisted: false, human_confirmed: true
        })
      });
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
          if (j === i + 1 || (i === 0 && j === points.length - 1)) continue;
          if (intersects(a, b, points[j], points[(j + 1) % points.length])) throw new Error("Polygon edges intersect.");
        }
      }
      state.polygons.push({ label: requiredLabel(), points }); state.draft = [];
      setStatus("Polygon added. Review it before submitting."); renderCanvas();
    } catch (error) { setStatus(error.message, true); }
  });
  $("cancel-annotation-draft").addEventListener("click", () => {
    state.boxes = []; state.polygons = []; state.draft = []; state.pointer = null;
    setStatus("Unsubmitted geometry cleared."); renderCanvas();
  });
  $("annotation-project").addEventListener("change", async () => {
    state.project = selectedProject();
    try { await loadTasks(); } catch (error) { setStatus(error.message, true); }
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
  $("annotation-upload-button").addEventListener("click", async () => {
    const file = $("annotation-upload-file").files[0];
    if (!state.project) { setStatus("Create or select a project first.", true); return; }
    if (!file) { setStatus("Choose an image file first.", true); return; }
    if (file.size < 1 || file.size > 25 * 1024 * 1024) { setStatus("Image must be between 1 byte and 25 MiB.", true); return; }
    if (!["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type)) { setStatus("Choose a PNG, JPEG, WebP or GIF image.", true); return; }
    const button = $("annotation-upload-button"); button.disabled = true;
    try {
      const uploaded = await api("/v1/annotation/projects/" + encodeURIComponent(state.project.project_id) + "/assets", {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file
      });
      if (uploaded.scan_status !== "clean") {
        setStatus("Image stored in quarantine. A configured scanner must mark it clean before it can become a task.", true);
        return;
      }
      await api("/v1/annotation/projects/" + encodeURIComponent(state.project.project_id) + "/tasks", {
        method: "POST", body: JSON.stringify({
          external_ref: "upload-" + uploaded.asset_id.slice(0, 16),
          asset_ref: uploaded.asset_id,
          input_data: { filename: file.name.slice(0, 200) }
        })
      });
      $("annotation-upload-file").value = "";
      await loadTasks();
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
  $("annotation-label").addEventListener("input", renderCanvas);
  if (window.ResizeObserver) {
    state.resizeObserver = new ResizeObserver(resizeCanvas);
    state.resizeObserver.observe(canvas);
  } else window.addEventListener("resize", resizeCanvas);
  window.addEventListener("pagehide", () => {
    if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
    if (state.resizeObserver) state.resizeObserver.disconnect();
  });
  setTool("box");
  renderCanvas();
})();