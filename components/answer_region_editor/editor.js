function cloneValue(value) {
  if (Array.isArray(value)) {
    return value.map(cloneValue);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneValue(item)]),
    );
  }
  return value;
}

export function reconcileRegionsByUuid(previous, incoming) {
  const previousByUuid = new Map(
    previous.map((region) => [region.region_uuid, region]),
  );
  return incoming.map((region) => {
    const existing = previousByUuid.get(region.region_uuid);
    return cloneValue(existing ? { ...existing, ...region } : region);
  });
}

export function removeRegionByUuid(regions, regionUuid) {
  return regions
    .filter((region) => region.region_uuid !== regionUuid)
    .map(cloneValue);
}

export function clampRegion(region, imageSize, tolerance = 8) {
  const result = cloneValue(region);
  const width = Number(imageSize?.width);
  const height = Number(imageSize?.height);
  const x = Number(region?.x);
  const y = Number(region?.y);
  const w = Number(region?.w);
  const h = Number(region?.h);

  if (
    ![width, height, x, y, w, h].every(Number.isFinite)
    || width <= 0
    || height <= 0
    || w <= 0
    || h <= 0
  ) {
    return result;
  }

  const right = x + w;
  const bottom = y + h;
  if (right <= 0 || bottom <= 0 || x >= width || y >= height) {
    return result;
  }

  const overflow = [-x, -y, right - width, bottom - height];
  if (overflow.some((amount) => amount > tolerance)) {
    return result;
  }

  const left = Math.max(0, x);
  const top = Math.max(0, y);
  const clampedRight = Math.min(width, right);
  const clampedBottom = Math.min(height, bottom);
  return {
    ...result,
    x: left,
    y: top,
    w: clampedRight - left,
    h: clampedBottom - top,
  };
}

export function imagePointFromClient(clientPoint, svgRect, viewBox) {
  const width = Number(svgRect?.width);
  const height = Number(svgRect?.height);
  const viewBoxX = Number(viewBox?.x) || 0;
  const viewBoxY = Number(viewBox?.y) || 0;
  const viewBoxWidth = Number(viewBox?.width) || 0;
  const viewBoxHeight = Number(viewBox?.height) || 0;
  if (width <= 0 || height <= 0) {
    return { x: viewBoxX, y: viewBoxY };
  }
  return {
    x: viewBoxX + ((Number(clientPoint?.x) - Number(svgRect?.x)) / width) * viewBoxWidth,
    y: viewBoxY + ((Number(clientPoint?.y) - Number(svgRect?.y)) / height) * viewBoxHeight,
  };
}

export function pushHistory(history, regions, limit = 30) {
  const safeLimit = Math.max(0, Math.floor(Number(limit) || 0));
  if (safeLimit === 0) {
    return [];
  }
  return [...cloneValue(history), cloneValue(regions)].slice(-safeLimit);
}

const SVG_NS = "http://www.w3.org/2000/svg";
const XLINK_NS = "http://www.w3.org/1999/xlink";
const HISTORY_LIMIT = 30;
const MIN_ZOOM = 0.05;
const MAX_ZOOM = 4;
const INVALID_ISSUES = new Set([
  "duplicate_uuid",
  "region_out_of_bounds",
  "region_too_small",
]);
const WARNING_ISSUES = new Set([
  "unbound_question",
  "unconfirmed_multi_region",
]);
const VALIDATION_ISSUE_TEXT = Object.freeze({
  unbound_question: {
    label: "未绑定题目",
    message: "请选择对应题目。",
  },
  region_out_of_bounds: {
    label: "题框超出图像范围",
    message: "请调整到页面内。",
  },
  region_too_small: {
    label: "题框尺寸过小",
    message: "请扩大题框。",
  },
  duplicate_uuid: {
    label: "题框标识重复",
    message: "请删除后重新创建该题框。",
  },
  unconfirmed_multi_region: {
    label: "同题多框尚未确认",
    message: "请确认这些题框属于同一道题。",
  },
  template_mismatch: {
    label: "试卷模板不匹配",
    message: "请重新检查当前标定。",
  },
});
const COMPLETED_OPERATIONS = new Set([
  "regions_changed",
  "mapping_changed",
  "multi_region_confirmed",
]);

function numberOr(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function pageImageUrl(data, page) {
  const image = data.images?.[page];
  if (image && typeof image === "object") {
    return image.src || image.url || "";
  }
  return (
    (typeof image === "string" ? image : "")
    || data[`${page}_image_data_url`]
    || data[`${page}_image_url`]
    || ""
  );
}

function pageImageSize(data, page) {
  const value = data.image_sizes?.[page] || data.images?.[page];
  const width = Array.isArray(value) ? value[0] : value?.width;
  const height = Array.isArray(value) ? value[1] : value?.height;
  return {
    width: Math.max(0, numberOr(width)),
    height: Math.max(0, numberOr(height)),
  };
}

function createSvgElement(name, attributes = {}) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function createButton(label, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "tool-button";
  button.dataset.action = action;
  button.textContent = label;
  return button;
}

function roundedRegion(region) {
  return {
    ...cloneValue(region),
    x: Math.round(numberOr(region.x)),
    y: Math.round(numberOr(region.y)),
    w: Math.round(numberOr(region.w)),
    h: Math.round(numberOr(region.h)),
  };
}

function normalizedRect(start, end) {
  const left = Math.min(start.x, end.x);
  const top = Math.min(start.y, end.y);
  return {
    x: left,
    y: top,
    w: Math.abs(end.x - start.x),
    h: Math.abs(end.y - start.y),
  };
}

function resizedRegion(region, handle, point) {
  const right = numberOr(region.x) + numberOr(region.w);
  const bottom = numberOr(region.y) + numberOr(region.h);
  let left = numberOr(region.x);
  let top = numberOr(region.y);
  let nextRight = right;
  let nextBottom = bottom;

  if (handle.includes("w")) {
    left = Math.min(point.x, right - 1);
  }
  if (handle.includes("e")) {
    nextRight = Math.max(point.x, left + 1);
  }
  if (handle.includes("n")) {
    top = Math.min(point.y, bottom - 1);
  }
  if (handle.includes("s")) {
    nextBottom = Math.max(point.y, top + 1);
  }
  return {
    ...cloneValue(region),
    x: left,
    y: top,
    w: nextRight - left,
    h: nextBottom - top,
  };
}

function newRegionUuid() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

function formatValidationIssue(issue) {
  const known = VALIDATION_ISSUE_TEXT[issue?.code];
  const backendMessage = typeof issue?.message === "string"
    ? issue.message.trim()
    : "";
  if (known) {
    const chineseMessage = `${known.label}：${known.message}`;
    return backendMessage && backendMessage !== known.message
      ? `${chineseMessage}（${backendMessage}）`
      : chineseMessage;
  }
  return backendMessage || "未知校验问题";
}

function isTypingTarget(target) {
  return Boolean(target?.closest?.("input, select, textarea, button"));
}

export default function answerRegionEditor(component) {
  const data = component.data || {};
  const readOnly = Boolean(data.read_only);
  const state = data.editor_state || {};
  const root = component.parentElement.querySelector('[data-role="editor-root"]');
  if (!root) {
    throw new Error("Answer region editor root was not found.");
  }

  const shell = root.querySelector('[data-role="canvas-shell"]');
  const svg = root.querySelector('[data-role="canvas"]');
  const pageImage = root.querySelector('[data-role="page-image"]');
  const regionsLayer = root.querySelector('[data-role="regions-layer"]');
  const draftLayer = root.querySelector('[data-role="draft-layer"]');
  const emptyPage = root.querySelector('[data-role="empty-page"]');
  const drawer = root.querySelector('[data-role="drawer"]');
  const drawerBody = root.querySelector('[data-role="drawer-body"]');
  const drawerSummary = root.querySelector('[data-role="drawer-summary"]');
  const zoomValue = root.querySelector('[data-role="zoom-value"]');
  const statusCounts = root.querySelector('[data-role="status-counts"]');
  const statusMode = root.querySelector('[data-role="status-mode"]');
  const statusNext = root.querySelector('[data-role="status-next"]');
  const statusSave = root.querySelector('[data-role="status-save"]');

  let regions = cloneValue(Array.isArray(state.regions) ? state.regions : []);
  let undoStack = cloneValue(
    Array.isArray(state.undo_stack) ? state.undo_stack.slice(-HISTORY_LIMIT) : [],
  );
  let redoStack = cloneValue(
    Array.isArray(state.redo_stack) ? state.redo_stack.slice(-HISTORY_LIMIT) : [],
  );
  let revision = Math.max(0, Math.floor(numberOr(state.revision)));
  let activePage = state.active_page === "back" ? "back" : "front";
  let selectedUuid = null;
  let mode = "select";
  let drawerOpen = Boolean(state.drawer_open || data.drawer_open_requested);
  let zoom = 1;
  let zoomMode = "fit";
  let spacePressed = false;
  let interaction = null;
  let draftRegion = null;
  let disposed = false;
  const listeners = [];
  const validationIssues = Array.isArray(data.validation_issues)
    ? data.validation_issues
    : [];
  const manualOptions = Array.isArray(data.manual_question_options)
    ? data.manual_question_options
    : [];

  pageImage.classList.add("page-image");

  function listen(target, type, handler, options) {
    target.addEventListener(type, handler, options);
    listeners.push(() => target.removeEventListener(type, handler, options));
  }

  function currentImageSize() {
    return pageImageSize(data, activePage);
  }

  function hasPage(page) {
    const size = pageImageSize(data, page);
    return Boolean(pageImageUrl(data, page) && size.width > 0 && size.height > 0);
  }

  function fitZoom() {
    const size = currentImageSize();
    if (size.width <= 0) {
      return 1;
    }
    return Math.min(
      MAX_ZOOM,
      Math.max(MIN_ZOOM, (shell.clientWidth - 32) / size.width),
    );
  }

  function pointFromEvent(event) {
    const size = currentImageSize();
    return imagePointFromClient(
      { x: event.clientX, y: event.clientY },
      svg.getBoundingClientRect(),
      { x: 0, y: 0, width: size.width, height: size.height },
    );
  }

  function issuesForRegion(region) {
    return validationIssues.filter(
      (issue) => (
        issue.region_uuid === region.region_uuid
        || (
          issue.question_id
          && issue.question_id === region.mapped_question_id
        )
      ),
    );
  }

  function issueState(region) {
    const relevant = issuesForRegion(region);
    if (relevant.some((issue) => INVALID_ISSUES.has(issue.code))) {
      return "invalid";
    }
    if (
      !region.mapped_question_id
      || relevant.some((issue) => WARNING_ISSUES.has(issue.code))
    ) {
      return "warning";
    }
    return "normal";
  }

  function renderRegion(region) {
    const group = createSvgElement("g");
    const stateClass = issueState(region);
    group.classList.add("region", stateClass);
    group.dataset.regionUuid = region.region_uuid;
    if (region.region_uuid === selectedUuid) {
      group.classList.add("selected");
    }

    const x = numberOr(region.x);
    const y = numberOr(region.y);
    const w = Math.max(1, numberOr(region.w, 1));
    const h = Math.max(1, numberOr(region.h, 1));
    const box = createSvgElement("rect", {
      class: "region-box",
      x,
      y,
      width: w,
      height: h,
    });
    group.append(box);

    const label = `${region.region_order || "?"} · ${region.mapped_question_id || "未绑定"}`;
    const labelHeight = 22 / zoom;
    const labelWidth = Math.max(58, label.length * 8) / zoom;
    const labelX = x + 2 / zoom;
    const labelY = y + 2 / zoom;
    group.append(
      createSvgElement("rect", {
        class: "region-label-bg",
        x: labelX,
        y: labelY,
        width: labelWidth,
        height: labelHeight,
        rx: 4 / zoom,
      }),
    );
    const text = createSvgElement("text", {
      class: "region-label",
      x: labelX + 6 / zoom,
      y: labelY + labelHeight / 2,
      "font-size": 12 / zoom,
    });
    text.textContent = label;
    group.append(text);

    if (region.region_uuid === selectedUuid) {
      const handleSize = 10 / zoom;
      const half = handleSize / 2;
      for (const [handle, handleX, handleY] of [
        ["nw", x, y],
        ["ne", x + w, y],
        ["sw", x, y + h],
        ["se", x + w, y + h],
      ]) {
        const handleElement = createSvgElement("rect", {
          class: "resize-handle",
          x: handleX - half,
          y: handleY - half,
          width: handleSize,
          height: handleSize,
          rx: 2 / zoom,
        });
        handleElement.dataset.handle = handle;
        group.append(handleElement);
      }
    }
    return group;
  }

  function renderRegions() {
    const fragment = document.createDocumentFragment();
    for (const region of regions.filter((region) => region.page === activePage)) {
      fragment.append(renderRegion(region));
    }
    regionsLayer.replaceChildren(fragment);
  }

  function renderDraft() {
    draftLayer.replaceChildren();
    if (!draftRegion) {
      return;
    }
    draftLayer.append(
      createSvgElement("rect", {
        class: "draft-box",
        x: draftRegion.x,
        y: draftRegion.y,
        width: draftRegion.w,
        height: draftRegion.h,
      }),
    );
  }

  function nextMappingLabel() {
    const supplied = data.next_mapping || data.next_question_id;
    if (supplied && typeof supplied === "object") {
      return supplied.label || supplied.value || "";
    }
    if (supplied) {
      return String(supplied);
    }
    const candidates = Array.isArray(data.automatic_candidates)
      ? data.automatic_candidates
      : [];
    const used = new Set(
      regions.map((region) => region.mapped_question_id).filter(Boolean),
    );
    return candidates.find((candidate) => !used.has(candidate)) || "无可用自动映射";
  }

  function renderStatus() {
    const currentCount = regions.filter((region) => region.page === activePage).length;
    const formattedIssues = validationIssues.map(formatValidationIssue);
    statusCounts.textContent = `当前页 ${currentCount} 个 · 全部 ${regions.length} 个`;
    statusMode.textContent = mode === "create"
      ? "连续新增模式：拖动画框，Esc 退出"
      : "选择模式：拖动题框，拖动角点缩放";
    statusNext.textContent = formattedIssues.length > 0
      ? `校验：${formattedIssues.join("；")}`
      : `下一个预计映射：${nextMappingLabel()}`;
    statusSave.textContent = `${data.save_status || "草稿"} · 修订 ${revision}`;
  }

  function renderToolbar() {
    root.classList.toggle("is-create-mode", mode === "create");
    for (const button of root.querySelectorAll("[data-page]")) {
      button.classList.toggle("is-active", button.dataset.page === activePage);
      button.disabled = !hasPage(button.dataset.page);
    }
    root.querySelector('[data-action="create"]').classList.toggle(
      "is-active",
      mode === "create",
    );
    root.querySelector('[data-action="create"]').disabled = readOnly;
    root.querySelector('[data-action="delete"]').disabled = readOnly || !selectedUuid;
    root.querySelector('[data-action="undo"]').disabled = readOnly || undoStack.length === 0;
    root.querySelector('[data-action="redo"]').disabled = readOnly || redoStack.length === 0;
    root.querySelector('[data-action="finish"]').disabled = readOnly;
    root.querySelector('[data-action="drawer"]').classList.toggle(
      "is-active",
      drawerOpen,
    );
    zoomValue.textContent = `${Math.round(zoom * 100)}%`;
  }

  function appendMappingOptions(select, region) {
    const options = manualOptions.map((option) => ({
      value: String(option?.value ?? ""),
      label: String(option?.label ?? option?.value ?? ""),
    }));
    if (!options.some((option) => option.value === "")) {
      options.unshift({ value: "", label: "未绑定" });
    }
    const current = String(region.mapped_question_id || "");
    if (current && !options.some((option) => option.value === current)) {
      options.push({ value: current, label: current });
    }
    for (const item of options) {
      const option = document.createElement("option");
      option.value = item.value;
      option.textContent = item.label || (item.value ? item.value : "未绑定");
      select.append(option);
    }
    select.value = current;
  }

  function renderDrawer() {
    drawer.hidden = !drawerOpen;
    drawer.classList.toggle("is-open", drawerOpen);
    drawerSummary.textContent = `当前页 ${
      regions.filter((region) => region.page === activePage).length
    } 个，全部 ${regions.length} 个`;
    if (!drawerOpen) {
      return;
    }

    const fragment = document.createDocumentFragment();
    if (regions.length === 0) {
      const empty = document.createElement("div");
      empty.className = "drawer-empty";
      empty.textContent = "尚未创建题框。点击“新增框”，然后在试卷图像上拖动。";
      fragment.append(empty);
    }

    for (const region of regions) {
      const row = document.createElement("section");
      const stateClass = issueState(region);
      const formattedIssues = issuesForRegion(region).map(formatValidationIssue);
      row.className = `region-row ${stateClass}`;
      row.classList.toggle("is-selected", region.region_uuid === selectedUuid);
      row.dataset.regionUuid = region.region_uuid;

      const top = document.createElement("div");
      top.className = "region-row-top";
      const title = document.createElement("span");
      title.className = "region-row-title";
      title.textContent = `${region.page === "back" ? "反面" : "正面"} · 题框 ${region.region_order || "?"}`;
      const stateText = document.createElement("span");
      stateText.className = "region-state";
      stateText.textContent = stateClass === "invalid"
        ? "异常"
        : stateClass === "warning"
          ? "待处理"
          : region.mapping_status === "auto"
            ? "自动映射"
            : "手动映射";
      top.append(title, stateText);

      const select = document.createElement("select");
      select.className = "mapping-select";
      select.dataset.regionUuid = region.region_uuid;
      select.setAttribute("aria-label", `题框 ${region.region_order || ""} 映射`);
      appendMappingOptions(select, region);
      select.disabled = readOnly;

      const issueList = document.createElement("ul");
      issueList.className = "region-issues";
      issueList.hidden = formattedIssues.length === 0;
      for (const message of formattedIssues) {
        const item = document.createElement("li");
        item.textContent = message;
        issueList.append(item);
      }

      const actions = document.createElement("div");
      actions.className = "region-row-actions";
      const locate = createButton("定位", "locate");
      locate.dataset.regionUuid = region.region_uuid;
      const remove = createButton("删除", "delete-row");
      remove.dataset.regionUuid = region.region_uuid;
      remove.disabled = readOnly;
      actions.append(locate, remove);
      row.append(top, issueList, select, actions);
      fragment.append(row);
    }

    const groups = new Map();
    for (const region of regions) {
      if (!region.mapped_question_id) {
        continue;
      }
      const group = groups.get(region.mapped_question_id) || [];
      group.push(region);
      groups.set(region.mapped_question_id, group);
    }
    const multiGroups = [...groups.entries()].filter(([, group]) => group.length > 1);
    if (multiGroups.length > 0) {
      const heading = document.createElement("div");
      heading.className = "drawer-section-title";
      heading.textContent = "同题多框确认";
      fragment.append(heading);
      for (const [questionId, group] of multiGroups) {
        const row = document.createElement("div");
        row.className = "group-row";
        const label = document.createElement("span");
        label.textContent = `${questionId} · ${group.length} 个题框`;
        const confirmed = group.every((region) => region.multi_region_confirmed);
        const button = createButton(confirmed ? "已确认" : "确认同题多框", "confirm-group");
        button.dataset.questionId = questionId;
        button.disabled = readOnly || confirmed;
        row.append(label, button);
        fragment.append(row);
      }
    }
    drawerBody.replaceChildren(fragment);
  }

  function renderCanvas() {
    const imageUrl = pageImageUrl(data, activePage);
    const size = currentImageSize();
    const available = Boolean(imageUrl && size.width > 0 && size.height > 0);
    svg.hidden = !available;
    emptyPage.hidden = available;
    if (!available) {
      regionsLayer.replaceChildren();
      draftLayer.replaceChildren();
      return;
    }

    if (zoomMode === "fit") {
      zoom = fitZoom();
    }
    svg.setAttribute("viewBox", `0 0 ${size.width} ${size.height}`);
    svg.style.width = `${Math.max(1, size.width * zoom)}px`;
    svg.style.height = `${Math.max(1, size.height * zoom)}px`;
    pageImage.setAttribute("x", "0");
    pageImage.setAttribute("y", "0");
    pageImage.setAttribute("width", String(size.width));
    pageImage.setAttribute("height", String(size.height));
    pageImage.setAttribute("href", imageUrl);
    pageImage.setAttributeNS(XLINK_NS, "href", imageUrl);
    renderRegions();
    renderDraft();
  }

  function renderAll() {
    renderCanvas();
    renderToolbar();
    renderDrawer();
    renderStatus();
  }

  function persist(lastOperation) {
    if (!COMPLETED_OPERATIONS.has(lastOperation)) {
      throw new Error(`Unsupported completed operation: ${lastOperation}`);
    }
    component.setStateValue("editor_state", {
      revision,
      last_operation: lastOperation,
      active_page: activePage,
      regions: cloneValue(regions),
      undo_stack: cloneValue(undoStack),
      redo_stack: cloneValue(redoStack),
      drawer_open: drawerOpen,
    });
  }

  function completeRegions(nextRegions, lastOperation, previousRegions = regions) {
    undoStack = pushHistory(undoStack, previousRegions, HISTORY_LIMIT);
    redoStack = [];
    regions = cloneValue(nextRegions);
    revision += 1;
    interaction = null;
    draftRegion = null;
    renderAll();
    persist(lastOperation);
  }

  function applyUndo() {
    if (undoStack.length === 0) {
      return;
    }
    const target = cloneValue(undoStack.at(-1));
    undoStack = cloneValue(undoStack.slice(0, -1));
    redoStack = pushHistory(redoStack, regions, HISTORY_LIMIT);
    regions = target;
    if (selectedUuid && !regions.some((region) => region.region_uuid === selectedUuid)) {
      selectedUuid = null;
    }
    revision += 1;
    renderAll();
    persist("regions_changed");
  }

  function applyRedo() {
    if (redoStack.length === 0) {
      return;
    }
    const target = cloneValue(redoStack.at(-1));
    redoStack = cloneValue(redoStack.slice(0, -1));
    undoStack = pushHistory(undoStack, regions, HISTORY_LIMIT);
    regions = target;
    if (selectedUuid && !regions.some((region) => region.region_uuid === selectedUuid)) {
      selectedUuid = null;
    }
    revision += 1;
    renderAll();
    persist("regions_changed");
  }

  function deleteRegion(regionUuid) {
    if (!regionUuid || !regions.some((region) => region.region_uuid === regionUuid)) {
      return;
    }
    const previous = cloneValue(regions);
    regions = removeRegionByUuid(regions, regionUuid);
    if (selectedUuid === regionUuid) {
      selectedUuid = null;
    }
    completeRegions(regions, "regions_changed", previous);
  }

  function setDrawerOpen(open) {
    drawerOpen = Boolean(open);
    renderDrawer();
    renderToolbar();
  }

  function setActivePage(page) {
    if (!hasPage(page) || page === activePage) {
      return;
    }
    cancelInteraction();
    activePage = page;
    selectedUuid = null;
    zoomMode = "fit";
    shell.scrollLeft = 0;
    shell.scrollTop = 0;
    renderAll();
  }

  function setZoom(nextZoom, nextMode = "manual") {
    const oldWidth = Math.max(1, svg.getBoundingClientRect().width);
    const oldHeight = Math.max(1, svg.getBoundingClientRect().height);
    const centerX = (shell.scrollLeft + shell.clientWidth / 2) / oldWidth;
    const centerY = (shell.scrollTop + shell.clientHeight / 2) / oldHeight;
    zoomMode = nextMode;
    zoom = nextMode === "fit"
      ? fitZoom()
      : Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom));
    renderCanvas();
    renderToolbar();
    if (disposed) {
      return;
    }
    requestAnimationFrame(() => {
      if (disposed) {
        return;
      }
      const newWidth = svg.getBoundingClientRect().width;
      const newHeight = svg.getBoundingClientRect().height;
      shell.scrollLeft = Math.max(0, centerX * newWidth - shell.clientWidth / 2);
      shell.scrollTop = Math.max(0, centerY * newHeight - shell.clientHeight / 2);
    });
  }

  function locateRegion(regionUuid) {
    const region = regions.find((item) => item.region_uuid === regionUuid);
    if (!region) {
      return;
    }
    if (region.page !== activePage && hasPage(region.page)) {
      activePage = region.page;
      zoomMode = "fit";
    }
    selectedUuid = regionUuid;
    renderAll();
    if (disposed) {
      return;
    }
    requestAnimationFrame(() => {
      if (disposed) {
        return;
      }
      shell.scrollLeft = Math.max(
        0,
        (numberOr(region.x) + numberOr(region.w) / 2) * zoom - shell.clientWidth / 2,
      );
      shell.scrollTop = Math.max(
        0,
        (numberOr(region.y) + numberOr(region.h) / 2) * zoom - shell.clientHeight / 2,
      );
    });
  }

  function cancelInteraction() {
    if (!interaction) {
      draftRegion = null;
      return;
    }
    if (interaction.previousRegions) {
      regions = cloneValue(interaction.previousRegions);
    }
    interaction = null;
    draftRegion = null;
    shell.classList.remove("is-panning");
    renderCanvas();
  }

  function completePointerInteraction() {
    const current = interaction;
    if (!current) {
      return;
    }
    interaction = null;
    shell.classList.remove("is-panning");

    if (current.kind === "pan") {
      return;
    }
    if (current.kind === "create") {
      if (!current.moved || !draftRegion) {
        draftRegion = null;
        renderCanvas();
        return;
      }
      const size = currentImageSize();
      const geometry = roundedRegion(clampRegion(draftRegion, size));
      const regionUuid = newRegionUuid();
      const maxOrder = regions.reduce(
        (maximum, region) => Math.max(maximum, numberOr(region.region_order)),
        0,
      );
      const created = {
        region_uuid: regionUuid,
        page: activePage,
        region_order: maxOrder + 1,
        ...geometry,
        mapped_question_id: null,
        mapping_status: "unbound",
        multi_region_confirmed: false,
      };
      selectedUuid = regionUuid;
      completeRegions([...regions, created], "regions_changed", current.previousRegions);
      return;
    }
    if (!current.moved) {
      renderAll();
      return;
    }

    const size = currentImageSize();
    const next = regions.map((region) => (
      region.region_uuid === current.uuid
        ? roundedRegion(clampRegion(region, size))
        : cloneValue(region)
    ));
    completeRegions(next, "regions_changed", current.previousRegions);
  }

  function handlePointerDown(event) {
    if (event.button === 1 || (spacePressed && event.button === 0)) {
      interaction = {
        kind: "pan",
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        scrollLeft: shell.scrollLeft,
        scrollTop: shell.scrollTop,
      };
      shell.classList.add("is-panning");
      svg.setPointerCapture(event.pointerId);
      event.preventDefault();
      return;
    }
    if (readOnly) {
      return;
    }
    if (event.button !== 0) {
      return;
    }
    root.focus({ preventScroll: true });
    const regionElement = event.target.closest?.("[data-region-uuid]");
    if (regionElement) {
      const regionUuid = regionElement.dataset.regionUuid;
      const region = regions.find((item) => item.region_uuid === regionUuid);
      if (!region) {
        return;
      }
      selectedUuid = regionUuid;
      mode = "select";
      const handle = event.target.dataset?.handle;
      interaction = {
        kind: handle ? "resize" : "move",
        pointerId: event.pointerId,
        uuid: regionUuid,
        handle,
        startPoint: pointFromEvent(event),
        startRegion: cloneValue(region),
        previousRegions: cloneValue(regions),
        moved: false,
      };
      svg.setPointerCapture(event.pointerId);
      renderAll();
      event.preventDefault();
      return;
    }

    if (mode === "create") {
      const point = pointFromEvent(event);
      interaction = {
        kind: "create",
        pointerId: event.pointerId,
        startPoint: point,
        previousRegions: cloneValue(regions),
        moved: false,
      };
      draftRegion = { x: point.x, y: point.y, w: 0, h: 0 };
      svg.setPointerCapture(event.pointerId);
      renderCanvas();
      event.preventDefault();
      return;
    }

    selectedUuid = null;
    renderAll();
  }

  function handlePointerMove(event) {
    if (!interaction || event.pointerId !== interaction.pointerId) {
      return;
    }
    if (interaction.kind === "pan") {
      shell.scrollLeft = interaction.scrollLeft - (event.clientX - interaction.clientX);
      shell.scrollTop = interaction.scrollTop - (event.clientY - interaction.clientY);
      event.preventDefault();
      return;
    }

    const point = pointFromEvent(event);
    if (interaction.kind === "create") {
      draftRegion = normalizedRect(interaction.startPoint, point);
      interaction.moved = draftRegion.w > 0.5 || draftRegion.h > 0.5;
      renderCanvas();
      event.preventDefault();
      return;
    }

    if (interaction.kind === "move") {
      const dx = point.x - interaction.startPoint.x;
      const dy = point.y - interaction.startPoint.y;
      interaction.moved = Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5;
      regions = regions.map((region) => (
        region.region_uuid === interaction.uuid
          ? {
            ...cloneValue(interaction.startRegion),
            x: numberOr(interaction.startRegion.x) + dx,
            y: numberOr(interaction.startRegion.y) + dy,
          }
          : cloneValue(region)
      ));
      renderCanvas();
      event.preventDefault();
      return;
    }

    if (interaction.kind === "resize") {
      const resized = resizedRegion(interaction.startRegion, interaction.handle, point);
      interaction.moved = (
        Math.abs(numberOr(resized.x) - numberOr(interaction.startRegion.x)) > 0.5
        || Math.abs(numberOr(resized.y) - numberOr(interaction.startRegion.y)) > 0.5
        || Math.abs(numberOr(resized.w) - numberOr(interaction.startRegion.w)) > 0.5
        || Math.abs(numberOr(resized.h) - numberOr(interaction.startRegion.h)) > 0.5
      );
      regions = regions.map((region) => (
        region.region_uuid === interaction.uuid
          ? resized
          : cloneValue(region)
      ));
      renderCanvas();
      event.preventDefault();
    }
  }

  function handlePointerUp(event) {
    if (!interaction || event.pointerId !== interaction.pointerId) {
      return;
    }
    if (svg.hasPointerCapture(event.pointerId)) {
      svg.releasePointerCapture(event.pointerId);
    }
    completePointerInteraction();
  }

  function handlePointerCancel(event) {
    if (!interaction || event.pointerId !== interaction.pointerId) {
      return;
    }
    cancelInteraction();
    renderAll();
  }

  function handleMappingChange(event) {
    if (readOnly) {
      return;
    }
    const select = event.target.closest?.(".mapping-select");
    if (!select) {
      return;
    }
    const regionUuid = select.dataset.regionUuid;
    const target = regions.find((region) => region.region_uuid === regionUuid);
    if (!target) {
      return;
    }
    const previous = cloneValue(regions);
    const oldQuestionId = target.mapped_question_id || null;
    const newQuestionId = select.value.trim() || null;
    const affectedQuestions = new Set([oldQuestionId, newQuestionId].filter(Boolean));
    const next = regions.map((region) => {
      if (region.region_uuid === regionUuid) {
        return {
          ...cloneValue(region),
          mapped_question_id: newQuestionId,
          mapping_status: newQuestionId ? "manual" : "unbound",
          multi_region_confirmed: false,
        };
      }
      if (affectedQuestions.has(region.mapped_question_id)) {
        return { ...cloneValue(region), multi_region_confirmed: false };
      }
      return cloneValue(region);
    });
    completeRegions(next, "mapping_changed", previous);
  }

  function handleDrawerClick(event) {
    const button = event.target.closest?.("[data-action]");
    if (!button) {
      return;
    }
    if (button.dataset.action === "drawer-close") {
      setDrawerOpen(false);
      return;
    }
    if (button.dataset.action === "locate") {
      locateRegion(button.dataset.regionUuid);
      return;
    }
    if (readOnly) {
      return;
    }
    if (button.dataset.action === "delete-row") {
      deleteRegion(button.dataset.regionUuid);
      return;
    }
    if (button.dataset.action === "confirm-group") {
      const questionId = button.dataset.questionId;
      const previous = cloneValue(regions);
      const next = regions.map((region) => (
        region.mapped_question_id === questionId
          ? { ...cloneValue(region), multi_region_confirmed: true }
          : cloneValue(region)
      ));
      completeRegions(next, "multi_region_confirmed", previous);
    }
  }

  function handleToolbarClick(event) {
    const pageButton = event.target.closest?.("[data-page]");
    if (pageButton) {
      setActivePage(pageButton.dataset.page);
      return;
    }
    const button = event.target.closest?.("[data-action]");
    const action = button?.dataset.action;
    if (!action || button.disabled) {
      return;
    }
    if (action === "exit") {
      component.setTriggerValue("exit_requested", { revision });
    } else if (action === "finish") {
      component.setTriggerValue("finish_requested", { revision });
    } else if (action === "create") {
      mode = mode === "create" ? "select" : "create";
      cancelInteraction();
      renderAll();
    } else if (action === "delete") {
      deleteRegion(selectedUuid);
    } else if (action === "undo") {
      applyUndo();
    } else if (action === "redo") {
      applyRedo();
    } else if (action === "fit") {
      setZoom(fitZoom(), "fit");
    } else if (action === "actual-size") {
      setZoom(1);
    } else if (action === "zoom-out") {
      setZoom(zoom / 1.2);
    } else if (action === "zoom-in") {
      setZoom(zoom * 1.2);
    } else if (action === "drawer") {
      setDrawerOpen(!drawerOpen);
    }
  }

  function handleKeyDown(event) {
    if (event.code === "Space" && !isTypingTarget(event.target)) {
      spacePressed = true;
      event.preventDefault();
      return;
    }
    if (event.key === "Escape") {
      if (interaction) {
        cancelInteraction();
      } else if (drawerOpen) {
        setDrawerOpen(false);
      } else {
        mode = "select";
        renderAll();
      }
      event.preventDefault();
      return;
    }
    if (isTypingTarget(event.target)) {
      return;
    }
    if (event.key === "Delete") {
      if (readOnly) {
        return;
      }
      deleteRegion(selectedUuid);
      event.preventDefault();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
      if (readOnly) {
        return;
      }
      if (event.shiftKey) {
        applyRedo();
      } else {
        applyUndo();
      }
      event.preventDefault();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
      if (readOnly) {
        return;
      }
      applyRedo();
      event.preventDefault();
    }
  }

  function handleKeyUp(event) {
    if (event.code === "Space") {
      resetSpacePressed();
    }
  }

  function resetSpacePressed() {
    spacePressed = false;
  }

  function handleVisibilityChange() {
    if (document.hidden) {
      resetSpacePressed();
    }
  }

  listen(root.querySelector('[data-role="toolbar"]'), "click", handleToolbarClick);
  listen(drawer, "click", handleDrawerClick);
  listen(drawer, "change", handleMappingChange);
  listen(svg, "pointerdown", handlePointerDown);
  listen(svg, "pointermove", handlePointerMove);
  listen(svg, "pointerup", handlePointerUp);
  listen(svg, "pointercancel", handlePointerCancel);
  listen(root, "keydown", handleKeyDown);
  listen(window, "keyup", handleKeyUp);
  listen(window, "blur", resetSpacePressed);
  listen(document, "visibilitychange", handleVisibilityChange);

  let resizeObserver = null;
  if (typeof ResizeObserver === "function") {
    resizeObserver = new ResizeObserver(() => {
      if (!disposed && zoomMode === "fit") {
        renderCanvas();
        renderToolbar();
      }
    });
    resizeObserver.observe(shell);
  } else {
    listen(window, "resize", () => {
      if (!disposed && zoomMode === "fit") {
        renderCanvas();
        renderToolbar();
      }
    });
  }

  if (!hasPage(activePage)) {
    activePage = hasPage("front") ? "front" : "back";
  }
  renderAll();

  return () => {
    disposed = true;
    resizeObserver?.disconnect();
    for (const removeListener of listeners.splice(0)) {
      removeListener();
    }
  };
}
