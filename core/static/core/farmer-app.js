(() => {
  "use strict";

  const app = document.querySelector("#farmer-app");
  if (!app) return;

  const form = app.querySelector("#field-form");
  const alertBox = app.querySelector("#app-alert");
  const csrfToken = form.querySelector("[name='csrfmiddlewaretoken']").value;
  const language = document.documentElement.lang.toLowerCase().startsWith("pt") ? "pt" : "en";
  const svgNamespace = "http://www.w3.org/2000/svg";
  const copy = app.dataset;
  const runtimeCopy = language === "pt"
    ? {
        confidence: "de confiança",
        point: "Ponto",
        longitude: "Longitude",
        latitude: "Latitude",
        zonesFound: "zonas de desenvolvimento relativo foram encontradas em",
        sceneSingular: "imagem sem nuvens relevantes",
        scenePlural: "imagens sem nuvens relevantes",
        source: "Fonte",
        processing: "processamento",
        dateInvalid: "A data final deve ser posterior ao plantio e a safra não pode passar de 365 dias.",
        coordinateInvalid: "Informe uma latitude e longitude válidas.",
        geometryInvalid: "Revise as coordenadas do polígono.",
        preparedDemoLoaded: "Resultado Sentinel preparado carregado.",
        lower: "Desenvolvimento relativo menor",
        similar: "Desenvolvimento relativo semelhante",
        higher: "Desenvolvimento relativo maior",
        regroupTitle: "Compare outro agrupamento",
        regroupBody: "Use as mesmas imagens e veja o talhão dividido em outra quantidade de zonas.",
        zoneCount: "Quantidade de zonas",
        regroup: "Reagrupar zonas",
        regroupBusy: "Reagrupando…",
        regroupDemo: "O reagrupamento fica disponível no resultado processado e persistido.",
        assistantTitle: "Pergunte ao Gemini",
        assistantBody: "O coordenador do Google ADK consulta os especialistas e as evidências do talhão antes de responder.",
        assistantWelcome: "Pergunte o que as diferenças significam ou qual zona merece uma visita de campo primeiro.",
        assistantPlaceholder: "Ex.: qual zona devo inspecionar primeiro?",
        send: "Enviar",
        sending: "Consultando Gemini…",
        listen: "Falar",
        listening: "Ouvindo…",
        you: "Você",
        gemini: "Gemini",
        trace: "Tecnologia usada nesta resposta",
        assistantFailure: "Não foi possível consultar o Gemini agora. Tente novamente.",
        feedbackTitle: "Esta explicação ajudou?",
        feedbackBody: "Seu retorno fica vinculado à análise para melhorar a demonstração.",
        helpful: "Ajudou",
        unclear: "Ficou confuso",
        notHelpful: "Não ajudou",
        feedbackThanks: "Obrigado. O retorno foi registrado.",
        feedbackBusy: "Registrando…",
        indices: {
          NDVI: "vigor e cobertura vegetal",
          NDRE: "clorofila e variações do dossel",
          NDMI: "umidade relativa da vegetação",
        },
      }
    : {
        confidence: "confidence",
        point: "Point",
        longitude: "Longitude",
        latitude: "Latitude",
        zonesFound: "relative development zones were found across",
        sceneSingular: "image without relevant cloud cover",
        scenePlural: "images without relevant cloud cover",
        source: "Source",
        processing: "processing",
        dateInvalid: "The end date must follow planting and the season cannot exceed 365 days.",
        coordinateInvalid: "Enter a valid latitude and longitude.",
        geometryInvalid: "Review the polygon coordinates.",
        preparedDemoLoaded: "Prepared Sentinel result loaded.",
        lower: "Lower relative development",
        similar: "Similar relative development",
        higher: "Higher relative development",
        regroupTitle: "Compare another grouping",
        regroupBody: "Use the same images and view the field divided into a different number of zones.",
        zoneCount: "Number of zones",
        regroup: "Regroup zones",
        regroupBusy: "Regrouping…",
        regroupDemo: "Regrouping is available for a processed and persisted result.",
        assistantTitle: "Ask Gemini",
        assistantBody: "The Google ADK coordinator consults specialists and field evidence before answering.",
        assistantWelcome: "Ask what the differences mean or which zone deserves a field visit first.",
        assistantPlaceholder: "For example: which zone should I inspect first?",
        send: "Send",
        sending: "Consulting Gemini…",
        listen: "Speak",
        listening: "Listening…",
        you: "You",
        gemini: "Gemini",
        trace: "Technology used for this response",
        assistantFailure: "Gemini could not be reached right now. Please try again.",
        feedbackTitle: "Was this explanation helpful?",
        feedbackBody: "Your response is linked to the analysis to improve the demonstration.",
        helpful: "Helpful",
        unclear: "Unclear",
        notHelpful: "Not helpful",
        feedbackThanks: "Thank you. Your feedback was recorded.",
        feedbackBusy: "Saving…",
        indices: {
          NDVI: "vegetation vigor and cover",
          NDRE: "chlorophyll and canopy variation",
          NDMI: "relative vegetation moisture",
        },
      };

  const state = {
    field: null,
    boundary: null,
    boundaryProjection: null,
    analysis: null,
    persistedAnalysis: null,
    guidedResult: false,
    agentSession: null,
    agentSessionPromise: null,
    nextAgentChannel: "text",
    resultInteractionsBuilt: false,
    idempotencyKeys: new Map(),
    draggingVertex: null,
  };

  class ApiRequestError extends Error {
    constructor(status, payload) {
      super(payload?.error?.message || copy.copyGenericError);
      this.name = "ApiRequestError";
      this.status = status;
      this.code = payload?.error?.code || "request_failed";
      this.details = payload?.error?.details || null;
    }
  }

  function createElement(tag, options = {}) {
    const node = document.createElement(tag);
    if (options.className) node.className = options.className;
    if (options.text !== undefined) node.textContent = String(options.text);
    for (const [name, value] of Object.entries(options.attributes || {})) {
      node.setAttribute(name, String(value));
    }
    return node;
  }

  function createSvgElement(tag, attributes = {}) {
    const node = document.createElementNS(svgNamespace, tag);
    for (const [name, value] of Object.entries(attributes)) {
      node.setAttribute(name, String(value));
    }
    return node;
  }

  function actionKey(action) {
    if (!state.idempotencyKeys.has(action)) {
      state.idempotencyKeys.set(action, crypto.randomUUID());
    }
    return state.idempotencyKeys.get(action);
  }

  async function apiRequest(url, options = {}) {
    const method = options.method || "GET";
    const headers = { Accept: "application/json" };
    const request = { method, headers, credentials: "same-origin" };
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      headers["X-CSRFToken"] = csrfToken;
      request.body = JSON.stringify(options.body);
    }
    if (options.action) headers["Idempotency-Key"] = actionKey(options.action);

    const response = await fetch(url, request);
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (!response.ok) throw new ApiRequestError(response.status, payload);
    return payload?.data;
  }

  function errorMessage(error) {
    if (!(error instanceof ApiRequestError)) return copy.copyGenericError;
    if (error.status === 401) return copy.copySessionExpired;
    if (error.status === 409) return copy.copyConflict;
    if (error.status === 422) return copy.copyInvalid;
    if (error.status === 429) return copy.copyRateLimit;
    return error.message || copy.copyGenericError;
  }

  function showAlert(message, tone = "error") {
    alertBox.textContent = message;
    alertBox.dataset.tone = tone;
    alertBox.hidden = false;
    alertBox.focus({ preventScroll: true });
  }

  function clearAlert() {
    alertBox.textContent = "";
    alertBox.hidden = true;
    delete alertBox.dataset.tone;
  }

  function setBusy(button, busy, busyText = copy.copyLoading) {
    if (!button.dataset.idleLabel) button.dataset.idleLabel = button.textContent.trim();
    button.disabled = busy;
    button.textContent = busy ? busyText : button.dataset.idleLabel;
  }

  function setStep(step) {
    clearAlert();
    app.querySelectorAll(".app-step").forEach((panel) => {
      const selected = Number(panel.dataset.step) === step;
      panel.hidden = !selected;
      panel.classList.toggle("is-active", selected);
    });
    app.querySelectorAll("[data-step-indicator]").forEach((indicator) => {
      const indicatorStep = Number(indicator.dataset.stepIndicator);
      indicator.classList.toggle("is-active", indicatorStep === step);
      indicator.classList.toggle("is-complete", indicatorStep < step);
      if (indicatorStep === step) indicator.setAttribute("aria-current", "step");
      else indicator.removeAttribute("aria-current");
    });
    const panel = app.querySelector(`[data-step="${step}"]`);
    panel?.querySelector("h2")?.focus({ preventScroll: true });
    window.scrollTo({ top: app.offsetTop - 20, behavior: "smooth" });
  }

  function setFieldValue(name, value) {
    const input = form.elements.namedItem(name);
    if (input) input.value = value ?? "";
  }

  function populateFieldForm(field) {
    setFieldValue("name", field.name);
    setFieldValue("crop", field.crop);
    setFieldValue("season_start", field.season_start);
    setFieldValue("season_end", field.season_end);
    setFieldValue("estimated_area_ha", field.estimated_area_ha);
    setFieldValue("longitude", field.reference_location.coordinates[0]);
    setFieldValue("latitude", field.reference_location.coordinates[1]);
  }

  async function loadDemonstrationField() {
    const button = app.querySelector("#use-demo");
    setBusy(button, true);
    clearAlert();
    try {
      let preparedDemo = null;
      try {
        preparedDemo = await apiRequest(copy.preparedDemoUrl);
      } catch (error) {
        const preparedDemoUnavailable = error instanceof ApiRequestError
          && error.status === 404
          && error.code === "prepared_demo_unavailable";
        if (!preparedDemoUnavailable) throw error;
      }

      if (preparedDemo) {
        state.field = preparedDemo.field;
        state.analysis = preparedDemo.analysis;
        state.persistedAnalysis = preparedDemo.analysis;
        state.guidedResult = false;
        state.boundary = null;
        state.boundaryProjection = null;
        state.agentSession = null;
        state.agentSessionPromise = null;
        populateFieldForm(preparedDemo.field);
        updateProgress(preparedDemo.analysis);
        app.querySelector("#location-status").textContent = runtimeCopy.preparedDemoLoaded;
        renderResult(preparedDemo.analysis);
        return;
      }

      const fixture = await apiRequest(copy.fixtureFieldUrl);
      populateFieldForm(fixture);
      app.querySelector("#location-status").textContent = copy.copyDemoLoaded;
    } catch (error) {
      showAlert(errorMessage(error));
    } finally {
      setBusy(button, false);
    }
  }

  function requestCurrentLocation() {
    const button = app.querySelector("#use-location");
    const status = app.querySelector("#location-status");
    clearAlert();
    setBusy(button, true, copy.copyLocationWait);
    status.textContent = copy.copyLocationWait;

    if (!navigator.geolocation) {
      status.textContent = copy.copyLocationDenied;
      setBusy(button, false);
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (position) => {
        setFieldValue("latitude", position.coords.latitude.toFixed(6));
        setFieldValue("longitude", position.coords.longitude.toFixed(6));
        status.textContent = copy.copyLocationOk;
        setBusy(button, false);
      },
      () => {
        status.textContent = copy.copyLocationDenied;
        setBusy(button, false);
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 },
    );
  }

  function validateFieldForm() {
    for (const control of form.querySelectorAll("input, select")) {
      if (control.type !== "checkbox") control.setCustomValidity("");
    }

    const startInput = form.elements.namedItem("season_start");
    const endInput = form.elements.namedItem("season_end");
    if (startInput.value && endInput.value) {
      const duration = (new Date(`${endInput.value}T00:00:00Z`) - new Date(`${startInput.value}T00:00:00Z`)) / 86400000;
      if (duration < 1 || duration > 365) endInput.setCustomValidity(runtimeCopy.dateInvalid);
    }

    const latitude = Number(form.elements.namedItem("latitude").value);
    const longitude = Number(form.elements.namedItem("longitude").value);
    if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
      form.elements.namedItem("latitude").setCustomValidity(runtimeCopy.coordinateInvalid);
    }
    if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) {
      form.elements.namedItem("longitude").setCustomValidity(runtimeCopy.coordinateInvalid);
    }

    const valid = form.checkValidity();
    for (const control of form.querySelectorAll("input, select")) {
      if (control.type !== "checkbox") control.setAttribute("aria-invalid", String(!control.validity.valid));
    }
    if (!valid) form.reportValidity();
    return valid;
  }

  function fieldPayload() {
    return {
      name: form.elements.namedItem("name").value.trim(),
      crop: form.elements.namedItem("crop").value,
      season_start: form.elements.namedItem("season_start").value,
      season_end: form.elements.namedItem("season_end").value,
      estimated_area_ha: Number(form.elements.namedItem("estimated_area_ha").value),
      reference_location: {
        type: "Point",
        coordinates: [
          Number(form.elements.namedItem("longitude").value),
          Number(form.elements.namedItem("latitude").value),
        ],
      },
    };
  }

  function normalizeRing(boundary) {
    const source = boundary.coordinates[0].map((position) => [Number(position[0]), Number(position[1])]);
    const unique = source.slice(0, -1);
    return [...unique, [...unique[0]]];
  }

  function createProjection(rings) {
    const positions = rings.flat();
    const longitudes = positions.map((position) => position[0]);
    const latitudes = positions.map((position) => position[1]);
    const minimumLongitude = Math.min(...longitudes);
    const maximumLongitude = Math.max(...longitudes);
    const minimumLatitude = Math.min(...latitudes);
    const maximumLatitude = Math.max(...latitudes);
    const longitudeSpan = Math.max(maximumLongitude - minimumLongitude, 0.0002);
    const latitudeSpan = Math.max(maximumLatitude - minimumLatitude, 0.0002);
    const centerLongitude = (minimumLongitude + maximumLongitude) / 2;
    const centerLatitude = (minimumLatitude + maximumLatitude) / 2;
    const scale = Math.min(540 / longitudeSpan, 270 / latitudeSpan);

    return {
      project(position) {
        return [
          320 + (position[0] - centerLongitude) * scale,
          180 - (position[1] - centerLatitude) * scale,
        ];
      },
      inverse(point) {
        return [
          centerLongitude + (point[0] - 320) / scale,
          centerLatitude - (point[1] - 180) / scale,
        ];
      },
    };
  }

  function drawMapGrid(svg) {
    for (let position = 40; position < 640; position += 60) {
      svg.append(createSvgElement("line", { x1: position, y1: 0, x2: position, y2: 360, class: "map-grid" }));
    }
    for (let position = 30; position < 360; position += 55) {
      svg.append(createSvgElement("line", { x1: 0, y1: position, x2: 640, y2: position, class: "map-grid" }));
    }
  }

  function ringToPoints(ring, projection) {
    return ring.map((position) => projection.project(position).map((value) => value.toFixed(2)).join(",")).join(" ");
  }

  function zoneGeometryPolygons(boundary) {
    if (boundary.type === "Polygon") return [boundary.coordinates];
    if (boundary.type === "MultiPolygon") return boundary.coordinates;
    return [];
  }

  function zoneGeometryRings(boundary) {
    return zoneGeometryPolygons(boundary).flatMap((polygon) => polygon);
  }

  function ringToPath(ring, projection) {
    return ring.map((position, index) => {
      const [x, y] = projection.project(position);
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(" ") + " Z";
  }

  function zoneGeometryPath(boundary, projection) {
    return zoneGeometryRings(boundary)
      .map((ring) => ringToPath(ring, projection))
      .join(" ");
  }

  function ringArea(ring) {
    return Math.abs(ring.slice(0, -1).reduce((total, position, index) => {
      const next = ring[index + 1];
      return total + position[0] * next[1] - next[0] * position[1];
    }, 0) / 2);
  }

  function updateBoundaryFromEditor() {
    const rows = [...app.querySelectorAll(".boundary-point")];
    const unique = rows.map((row) => [
      Number(row.querySelector("[data-coordinate='longitude']").value),
      Number(row.querySelector("[data-coordinate='latitude']").value),
    ]);
    if (unique.length < 3 || unique.flat().some((value) => !Number.isFinite(value))) {
      showAlert(runtimeCopy.geometryInvalid);
      return false;
    }
    state.boundary.boundary.coordinates[0] = [...unique, [...unique[0]]];
    return true;
  }

  function renderBoundaryEditor() {
    const container = app.querySelector("#boundary-points");
    container.replaceChildren();
    const unique = state.boundary.boundary.coordinates[0].slice(0, -1);
    unique.forEach((position, index) => {
      const row = createElement("div", { className: "boundary-point" });
      row.append(createElement("strong", { text: `${runtimeCopy.point} ${index + 1}` }));
      const longitude = createElement("input", {
        attributes: {
          type: "number",
          step: "0.000001",
          value: position[0].toFixed(6),
          "data-coordinate": "longitude",
          "aria-label": `${runtimeCopy.point} ${index + 1} · ${runtimeCopy.longitude}`,
        },
      });
      const latitude = createElement("input", {
        attributes: {
          type: "number",
          step: "0.000001",
          value: position[1].toFixed(6),
          "data-coordinate": "latitude",
          "aria-label": `${runtimeCopy.point} ${index + 1} · ${runtimeCopy.latitude}`,
        },
      });
      row.append(longitude, latitude);
      container.append(row);
    });
  }

  function renderBoundaryMap(recalculateProjection = true) {
    const svg = app.querySelector("#boundary-map");
    const ring = state.boundary.boundary.coordinates[0];
    if (recalculateProjection || !state.boundaryProjection) {
      state.boundaryProjection = createProjection([ring]);
    }
    const projection = state.boundaryProjection;
    svg.replaceChildren();
    drawMapGrid(svg);
    const polygon = createSvgElement("polygon", {
      points: ringToPoints(ring, projection),
      class: "boundary-fill",
    });
    svg.append(polygon);
    ring.slice(0, -1).forEach((position, index) => {
      const [x, y] = projection.project(position);
      const vertex = createSvgElement("circle", {
        cx: x,
        cy: y,
        r: 9,
        class: "vertex",
        tabindex: "0",
        role: "button",
        "aria-label": `${runtimeCopy.point} ${index + 1}`,
        "data-vertex": index,
      });
      vertex.addEventListener("pointerdown", (event) => {
        state.draggingVertex = index;
        vertex.setPointerCapture(event.pointerId);
      });
      vertex.addEventListener("pointermove", (event) => {
        if (state.draggingVertex !== index) return;
        const bounds = svg.getBoundingClientRect();
        const point = [
          Math.min(620, Math.max(20, ((event.clientX - bounds.left) / bounds.width) * 640)),
          Math.min(340, Math.max(20, ((event.clientY - bounds.top) / bounds.height) * 360)),
        ];
        const coordinate = projection.inverse(point).map((value) => Number(value.toFixed(6)));
        const coordinates = state.boundary.boundary.coordinates[0];
        coordinates[index] = coordinate;
        coordinates[coordinates.length - 1] = [...coordinates[0]];
        polygon.setAttribute("points", ringToPoints(coordinates, projection));
        const [x, y] = projection.project(coordinate);
        vertex.setAttribute("cx", String(x));
        vertex.setAttribute("cy", String(y));
        const editor = app.querySelectorAll(".boundary-point")[index];
        editor.querySelector("[data-coordinate='longitude']").value = coordinate[0].toFixed(6);
        editor.querySelector("[data-coordinate='latitude']").value = coordinate[1].toFixed(6);
      });
      const stopDragging = () => { state.draggingVertex = null; };
      vertex.addEventListener("pointerup", stopDragging);
      vertex.addEventListener("pointercancel", stopDragging);
      svg.append(vertex);
    });
  }

  function renderBoundary() {
    state.boundary.boundary.coordinates[0] = normalizeRing(state.boundary.boundary);
    const confidence = Math.round(state.boundary.confidence * 100);
    app.querySelector("#boundary-confidence").textContent = `${confidence}% ${runtimeCopy.confidence}`;
    app.querySelector("#boundary-area").textContent = `${state.boundary.estimated_area_ha.toFixed(1)} ${copy.copyHectares}`;
    renderBoundaryEditor();
    renderBoundaryMap();
  }

  function updateProgress(analysis) {
    const percent = analysis.progress?.percent ?? 0;
    const message = language === "pt" ? analysis.progress?.message_pt : analysis.progress?.message_en;
    app.querySelector("#analysis-progress").setAttribute("aria-valuenow", String(percent));
    app.querySelector("#progress-bar").style.width = `${percent}%`;
    app.querySelector("#progress-value").textContent = `${percent}%`;
    app.querySelector("#progress-message").textContent = message || copy.copyAnalysisPoll;
  }

  function labelFor(relativeLabel) {
    if (relativeLabel === "lower_than_field") return runtimeCopy.lower;
    if (relativeLabel === "higher_than_field") return runtimeCopy.higher;
    return runtimeCopy.similar;
  }

  function classFor(relativeLabel) {
    if (relativeLabel === "lower_than_field") return "zone-lower";
    if (relativeLabel === "higher_than_field") return "zone-higher";
    return "zone-similar";
  }

  function renderZoneMap(zones) {
    const svg = app.querySelector("#zone-map");
    const rings = zones.flatMap((zone) => zoneGeometryRings(zone.boundary));
    const projection = createProjection(rings);
    svg.replaceChildren();
    drawMapGrid(svg);
    zones.forEach((zone, index) => {
      const exteriorRings = zoneGeometryPolygons(zone.boundary).map((polygon) => polygon[0]);
      const ring = exteriorRings.reduce((largest, candidate) => (
        ringArea(candidate) > ringArea(largest) ? candidate : largest
      ));
      svg.append(createSvgElement("path", {
        d: zoneGeometryPath(zone.boundary, projection),
        class: `zone-shape ${classFor(zone.relative_label)}`,
        "fill-rule": "evenodd",
      }));
      const unique = ring.slice(0, -1);
      const center = unique.reduce((total, position) => [total[0] + position[0], total[1] + position[1]], [0, 0]).map((value) => value / unique.length);
      const [x, y] = projection.project(center);
      svg.append(createSvgElement("circle", { cx: x, cy: y, r: 15, class: "zone-label" }));
      const number = createSvgElement("text", { x, y, class: "zone-number" });
      number.textContent = String(index + 1);
      svg.append(number);
    });
  }

  function renderZoneLegend(zones) {
    const legend = app.querySelector("#zone-legend");
    legend.replaceChildren();
    const labels = [...new Set(zones.map((zone) => zone.relative_label))];
    labels.forEach((label) => {
      const item = createElement("span");
      item.append(createElement("i", { className: classFor(label) }), document.createTextNode(labelFor(label)));
      legend.append(item);
    });
  }

  function renderZoneCards(zones) {
    const container = app.querySelector("#zone-cards");
    container.replaceChildren();
    zones.forEach((zone, index) => {
      const card = createElement("article", { className: "zone-card" });
      const header = createElement("header");
      header.append(
        createElement("h3", { text: `${copy.copyZone} ${index + 1}` }),
        createElement("span", { text: `${zone.area_percent.toFixed(1)}%` }),
      );
      const area = createElement("strong", { text: `${zone.area_ha.toFixed(1)} ${copy.copyHectares} · ${labelFor(zone.relative_label)}` });
      const summary = createElement("p", { text: language === "pt" ? zone.summary_pt : zone.summary_en });
      card.append(header, area, summary);
      container.append(card);
    });
  }

  function renderEvidence(result) {
    const pills = app.querySelector("#index-pills");
    pills.replaceChildren();
    result.provenance.indices.forEach((index) => {
      pills.append(createElement("span", { text: `${index} · ${runtimeCopy.indices[index] || index}` }));
    });

    const rows = app.querySelector("#scene-rows");
    rows.replaceChildren();
    const dateFormatter = new Intl.DateTimeFormat(language === "pt" ? "pt-BR" : "en", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    });
    result.scenes.forEach((scene) => {
      const row = createElement("tr");
      const values = [
        dateFormatter.format(new Date(scene.captured_at)),
        scene.field_indices.ndvi.toFixed(2),
        scene.field_indices.ndre.toFixed(2),
        scene.field_indices.ndmi.toFixed(2),
        `${scene.cloud_cover_percent.toFixed(1)}%`,
      ];
      values.forEach((value) => row.append(createElement("td", { text: value })));
      rows.append(row);
    });

    const provenance = result.provenance;
    app.querySelector("#result-provenance").textContent = `${runtimeCopy.source}: ${provenance.provider} · ${provenance.mission} ${provenance.product_level} · ${provenance.bands.join(", ")} · ${runtimeCopy.processing} ${provenance.processing_version}`;
  }

  function appendChatMessage(role, text) {
    const log = app.querySelector("#agent-chat-log");
    const message = createElement("article", {
      className: `chat-message chat-message-${role}`,
    });
    message.append(
      createElement("strong", { text: role === "user" ? runtimeCopy.you : runtimeCopy.gemini }),
      createElement("p", { text }),
    );
    log.append(message);
    log.scrollTop = log.scrollHeight;
  }

  function renderAgentTrace(trace) {
    const panel = app.querySelector("#agent-trace");
    const list = app.querySelector("#agent-trace-list");
    list.replaceChildren();
    const values = [
      trace?.provider && trace?.model ? `${trace.provider} · ${trace.model}` : null,
      trace?.framework,
      ...(trace?.agents || []).map((agent) => `Agent · ${agent}`),
      ...(trace?.tools || []).map((tool) => `Tool · ${tool}`),
    ].filter(Boolean);
    [...new Set(values)].forEach((value) => {
      list.append(createElement("span", { text: value }));
    });
    panel.hidden = values.length === 0;
  }

  function agentContextAnalysis() {
    if (state.guidedResult || state.persistedAnalysis?.status !== "completed") return null;
    return state.persistedAnalysis;
  }

  async function ensureAgentSession(channel = "text") {
    if (state.agentSession) return state.agentSession;
    if (state.agentSessionPromise) return state.agentSessionPromise;

    const contextualAnalysis = agentContextAnalysis();
    const body = {
      language: language === "pt" ? "pt-BR" : "en",
      channel,
      field_id: state.field.id,
    };
    if (contextualAnalysis) body.analysis_id = contextualAnalysis.id;

    const contextKey = contextualAnalysis?.id || state.field.id;
    state.agentSessionPromise = apiRequest(copy.agentSessionsUrl, {
      method: "POST",
      action: `create-agent-session-${contextKey}-${channel}`,
      body,
    })
      .then((session) => {
        state.agentSession = session;
        return session;
      })
      .finally(() => {
        state.agentSessionPromise = null;
      });
    return state.agentSessionPromise;
  }

  async function sendAgentMessage(event) {
    event.preventDefault();
    const input = app.querySelector("#agent-question");
    const button = app.querySelector("#send-agent-question");
    const message = input.value.trim();
    if (!message) {
      input.focus();
      return;
    }

    appendChatMessage("user", message);
    input.value = "";
    input.disabled = true;
    setBusy(button, true, runtimeCopy.sending);
    try {
      const session = await ensureAgentSession(state.nextAgentChannel);
      state.nextAgentChannel = "text";
      const result = await apiRequest(`${copy.agentSessionsUrl}${session.id}/turns/`, {
        method: "POST",
        body: { message },
      });
      state.agentSession = result.session;
      appendChatMessage("assistant", result.message.text);
      renderAgentTrace(result.trace);
    } catch (_error) {
      appendChatMessage("assistant", runtimeCopy.assistantFailure);
    } finally {
      input.disabled = false;
      setBusy(button, false);
      input.focus();
    }
  }

  function listenForQuestion(SpeechRecognition, button, input) {
    const recognition = new SpeechRecognition();
    recognition.lang = language === "pt" ? "pt-BR" : "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    button.disabled = true;
    button.textContent = runtimeCopy.listening;
    recognition.addEventListener("result", (event) => {
      input.value = event.results[0][0].transcript;
      state.nextAgentChannel = "voice";
      input.focus();
    });
    recognition.addEventListener("end", () => {
      button.disabled = false;
      button.textContent = runtimeCopy.listen;
    });
    recognition.addEventListener("error", () => {
      button.disabled = false;
      button.textContent = runtimeCopy.listen;
    });
    recognition.start();
  }

  async function reclusterAnalysis(event) {
    event.preventDefault();
    const status = app.querySelector("#recluster-status");
    const select = app.querySelector("#zone-count");
    const button = app.querySelector("#recluster-submit");
    const source = agentContextAnalysis();
    if (!source) {
      status.textContent = runtimeCopy.regroupDemo;
      return;
    }

    const zoneCount = Number(select.value);
    setBusy(button, true, runtimeCopy.regroupBusy);
    try {
      const analysis = await apiRequest(`${copy.analysesUrl}${source.id}/recluster/`, {
        method: "POST",
        action: `recluster-${source.id}-${zoneCount}`,
        body: { zone_count: zoneCount },
      });
      state.analysis = analysis;
      state.persistedAnalysis = analysis;
      state.guidedResult = false;
      state.agentSession = null;
      state.agentSessionPromise = null;
      updateProgress(analysis);
      setStep(3);
      await pollAnalysis(analysis.id);
    } catch (error) {
      setStep(4);
      showAlert(errorMessage(error));
    } finally {
      setBusy(button, false);
    }
  }

  async function submitFeedback(rating) {
    const buttons = [...app.querySelectorAll("[data-feedback-rating]")];
    const status = app.querySelector("#feedback-status");
    if (!state.persistedAnalysis) return;
    buttons.forEach((button) => {
      button.disabled = true;
    });
    status.textContent = runtimeCopy.feedbackBusy;
    try {
      const session = await ensureAgentSession();
      await apiRequest(copy.feedbackUrl, {
        method: "POST",
        action: `feedback-${state.persistedAnalysis.id}-${rating}`,
        body: {
          analysis_id: state.persistedAnalysis.id,
          session_id: session.id,
          rating,
        },
      });
      status.textContent = runtimeCopy.feedbackThanks;
    } catch (error) {
      status.textContent = errorMessage(error);
      buttons.forEach((button) => {
        button.disabled = false;
      });
    }
  }

  function buildResultInteractions() {
    if (state.resultInteractionsBuilt) return;

    const zonePanel = app.querySelector("#zone-controls");
    zonePanel.className = "interaction-card zone-control-panel";
    const zoneForm = createElement("form", {
      className: "inline-control",
      attributes: { id: "recluster-form" },
    });
    const zoneLabel = createElement("label", {
      text: runtimeCopy.zoneCount,
      attributes: { for: "zone-count" },
    });
    const zoneSelect = createElement("select", {
      attributes: { id: "zone-count", name: "zone_count" },
    });
    for (let count = 2; count <= 7; count += 1) {
      zoneSelect.append(createElement("option", { text: count, attributes: { value: count } }));
    }
    const zoneButton = createElement("button", {
      className: "secondary-button",
      text: runtimeCopy.regroup,
      attributes: { id: "recluster-submit", type: "submit" },
    });
    zoneForm.append(zoneLabel, zoneSelect, zoneButton);
    zonePanel.append(
      createElement("h3", { text: runtimeCopy.regroupTitle }),
      createElement("p", { text: runtimeCopy.regroupBody }),
      zoneForm,
      createElement("p", {
        className: "interaction-status",
        attributes: { id: "recluster-status", role: "status" },
      }),
    );
    zoneForm.addEventListener("submit", reclusterAnalysis);

    const assistant = app.querySelector("#gemini-assistant");
    assistant.className = "interaction-card assistant-panel";
    const chatLog = createElement("div", {
      className: "chat-log",
      attributes: { id: "agent-chat-log", role: "log", "aria-live": "polite" },
    });
    const welcome = createElement("article", { className: "chat-message chat-message-assistant" });
    welcome.append(
      createElement("strong", { text: runtimeCopy.gemini }),
      createElement("p", { text: runtimeCopy.assistantWelcome }),
    );
    chatLog.append(welcome);

    const agentForm = createElement("form", {
      className: "agent-form",
      attributes: { id: "agent-form" },
    });
    const question = createElement("textarea", {
      attributes: {
        id: "agent-question",
        name: "message",
        maxlength: 2000,
        rows: 2,
        required: "required",
        placeholder: runtimeCopy.assistantPlaceholder,
        "aria-label": runtimeCopy.assistantPlaceholder,
      },
    });
    const agentActions = createElement("div", { className: "agent-actions" });
    const speech = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (speech) {
      const voiceButton = createElement("button", {
        className: "secondary-button voice-button",
        text: runtimeCopy.listen,
        attributes: { type: "button", id: "agent-voice" },
      });
      voiceButton.addEventListener("click", () => {
        listenForQuestion(speech, voiceButton, question);
      });
      agentActions.append(voiceButton);
    }
    agentActions.append(createElement("button", {
      className: "primary-button app-primary",
      text: runtimeCopy.send,
      attributes: { type: "submit", id: "send-agent-question" },
    }));
    agentForm.append(question, agentActions);
    agentForm.addEventListener("submit", sendAgentMessage);
    const trace = createElement("aside", {
      className: "agent-trace",
      attributes: { id: "agent-trace", hidden: "hidden" },
    });
    trace.append(
      createElement("strong", { text: runtimeCopy.trace }),
      createElement("div", { className: "trace-list", attributes: { id: "agent-trace-list" } }),
    );
    assistant.append(
      createElement("h3", { text: runtimeCopy.assistantTitle }),
      createElement("p", { text: runtimeCopy.assistantBody }),
      chatLog,
      agentForm,
      trace,
    );

    const feedback = app.querySelector("#feedback-panel");
    feedback.className = "interaction-card feedback-panel";
    const feedbackActions = createElement("div", { className: "feedback-actions" });
    [
      ["helpful", runtimeCopy.helpful],
      ["unclear", runtimeCopy.unclear],
      ["not_helpful", runtimeCopy.notHelpful],
    ].forEach(([rating, label]) => {
      const button = createElement("button", {
        className: "choice-button",
        text: label,
        attributes: { type: "button", "data-feedback-rating": rating },
      });
      button.addEventListener("click", () => submitFeedback(rating));
      feedbackActions.append(button);
    });
    feedback.append(
      createElement("h3", { text: runtimeCopy.feedbackTitle }),
      createElement("p", { text: runtimeCopy.feedbackBody }),
      feedbackActions,
      createElement("p", {
        className: "interaction-status",
        attributes: { id: "feedback-status", role: "status" },
      }),
    );

    state.resultInteractionsBuilt = true;
    app.querySelector("#result-interactions").hidden = false;
  }

  function updateResultInteractions(result) {
    buildResultInteractions();
    const canRecluster = Boolean(agentContextAnalysis());
    const zoneSelect = app.querySelector("#zone-count");
    const button = app.querySelector("#recluster-submit");
    zoneSelect.value = String(result.selected_zone_count);
    zoneSelect.disabled = !canRecluster;
    button.disabled = !canRecluster;
    app.querySelector("#recluster-status").textContent = canRecluster
      ? ""
      : runtimeCopy.regroupDemo;
  }

  function renderResult(analysis) {
    state.analysis = analysis;
    const result = analysis.result;
    const sceneWord = result.scenes.length === 1 ? runtimeCopy.sceneSingular : runtimeCopy.scenePlural;
    app.querySelector("#results-summary").textContent = `${result.selected_zone_count} ${runtimeCopy.zonesFound} ${result.scenes.length} ${sceneWord}.`;
    app.querySelector("#result-disclaimer").textContent = language === "pt" ? result.scope.disclaimer_pt : result.scope.disclaimer_en;
    renderZoneMap(result.zones);
    renderZoneLegend(result.zones);
    renderZoneCards(result.zones);
    renderEvidence(result);
    updateResultInteractions(result);
    setStep(4);
  }

  function delay(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  async function runGuidedAnalysis() {
    await delay(600);
    const running = await apiRequest(copy.fixtureRunningUrl);
    updateProgress(running);
    await delay(950);
    const completed = await apiRequest(copy.fixtureResultUrl);
    updateProgress(completed);
    await delay(350);
    state.guidedResult = true;
    renderResult(completed);
  }

  async function pollAnalysis(analysisId) {
    for (let attempt = 0; attempt < 150; attempt += 1) {
      await delay(2000);
      const analysis = await apiRequest(`${copy.analysesUrl}${analysisId}/`);
      state.persistedAnalysis = analysis;
      updateProgress(analysis);
      if (analysis.status === "completed") {
        state.guidedResult = false;
        renderResult(analysis);
        return;
      }
      if (analysis.status === "failed") {
        throw new Error(analysis.error?.message || copy.copyAnalysisFailed);
      }
    }
    throw new Error(copy.copyAnalysisPoll);
  }

  async function queueAnalysis() {
    const analysis = await apiRequest(copy.analysesUrl, {
      method: "POST",
      action: "create-analysis",
      body: { field_id: state.field.id, requested_zone_count: 4 },
    });
    state.analysis = analysis;
    state.persistedAnalysis = analysis;
    state.guidedResult = false;
    updateProgress(analysis);
    if (app.querySelector("#guided-demo").checked) await runGuidedAnalysis();
    else await pollAnalysis(analysis.id);
  }

  async function submitField(event) {
    event.preventDefault();
    clearAlert();
    if (!validateFieldForm()) return;
    const button = app.querySelector("#create-field");
    setBusy(button, true, copy.copyFieldSaving);
    try {
      state.field = await apiRequest(copy.fieldsUrl, {
        method: "POST",
        action: "create-field",
        body: fieldPayload(),
      });
      state.boundary = await apiRequest(`${copy.fieldsUrl}${state.field.id}/boundary-suggestions/`, {
        method: "POST",
        action: "suggest-boundary",
        body: {},
      });
      renderBoundary();
      setStep(2);
      showAlert(copy.copyBoundaryReady, "success");
    } catch (error) {
      showAlert(errorMessage(error));
    } finally {
      setBusy(button, false);
    }
  }

  async function confirmBoundary() {
    clearAlert();
    if (!updateBoundaryFromEditor()) return;
    renderBoundaryMap();
    const button = app.querySelector("#confirm-boundary");
    setBusy(button, true, copy.copyBoundarySaving);
    try {
      state.field = await apiRequest(`${copy.fieldsUrl}${state.field.id}/`, {
        method: "PATCH",
        body: {
          boundary: state.boundary.boundary,
          boundary_confirmed: true,
        },
      });
      setStep(3);
      app.querySelector("#progress-message").textContent = copy.copyAnalysisQueue;
      await queueAnalysis();
    } catch (error) {
      setStep(2);
      showAlert(errorMessage(error));
    } finally {
      setBusy(button, false);
    }
  }

  function downloadGeoJson() {
    const result = state.analysis?.result;
    if (!result) return;
    const featureCollection = {
      type: "FeatureCollection",
      features: result.zones.map((zone) => ({
        type: "Feature",
        geometry: zone.boundary,
        properties: {
          zone_id: zone.zone_id,
          relative_label: zone.relative_label,
          area_ha: zone.area_ha,
          area_percent: zone.area_percent,
        },
      })),
    };
    const blob = new Blob([JSON.stringify(featureCollection, null, 2)], { type: "application/geo+json" });
    const url = URL.createObjectURL(blob);
    const link = createElement("a", { attributes: { href: url, download: "agentic-agriculture-zones.geojson" } });
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  form.addEventListener("submit", submitField);
  form.addEventListener("input", (event) => {
    if (event.target.matches("input, select")) {
      event.target.setCustomValidity("");
      event.target.removeAttribute("aria-invalid");
    }
  });
  app.querySelector("#use-location").addEventListener("click", requestCurrentLocation);
  app.querySelector("#use-demo").addEventListener("click", loadDemonstrationField);
  app.querySelector("#redraw-boundary").addEventListener("click", () => {
    clearAlert();
    if (updateBoundaryFromEditor()) renderBoundaryMap();
  });
  app.querySelector("#confirm-boundary").addEventListener("click", confirmBoundary);
  app.querySelector("#download-geojson").addEventListener("click", downloadGeoJson);
  app.querySelector("#restart-analysis").addEventListener("click", () => window.location.reload());
  app.querySelectorAll("[data-back-to]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = Number(button.dataset.backTo);
      if (target === 1) {
        state.idempotencyKeys.delete("create-field");
        state.idempotencyKeys.delete("suggest-boundary");
      }
      setStep(target);
    });
  });
})();
