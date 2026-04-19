function trimText(text, maxLength) {
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatScore(value) {
  return Number(value ?? 0).toFixed(2);
}

function normalizeRubricText(value) {
  return String(value ?? "")
    .split(/\n\s*\n/g)
    .map((paragraph) => paragraph.replace(/\s*\n\s*/g, " ").replace(/\s{2,}/g, " ").trim())
    .filter(Boolean)
    .join("\n\n");
}

function normalizeTextBreaks(value) {
  return String(value ?? "").replace(/\n{3,}/g, "\n\n").trim();
}

function renderFormattedText(value) {
  return escapeHtml(normalizeTextBreaks(value)).replace(/\n/g, "<br>");
}

function renderRubricText(value) {
  return escapeHtml(normalizeRubricText(value)).replace(/\n\n/g, "<br><br>");
}

function renderRubricPopover(observation) {
  const anchorBlocks = [5, 4, 3, 2, 1]
    .filter((score) => observation.rubricAnchors?.[score])
    .map((score) => `
      <article class="rubric-anchor">
        <div class="rubric-anchor-score">${score} 分</div>
        <div class="rubric-anchor-text">${renderRubricText(observation.rubricAnchors[score])}</div>
      </article>
    `)
    .join("");

  if (anchorBlocks) {
    return `<div class="rubric-anchor-list">${anchorBlocks}</div>`;
  }

  if (observation.rubric.length > 0) {
    return `
      <div class="rubric-fallback-list">
        ${observation.rubric.map((item) => `
          <div class="rubric-fallback-item">${renderRubricText(item)}</div>
        `).join("")}
      </div>
    `;
  }

  return '<div class="rubric-empty">当前观测点暂无 rubric anchors。</div>';
}

function hashCode(text) {
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash << 5) - hash) + text.charCodeAt(index);
    hash |= 0;
  }
  return hash;
}

function renderTextWithEvidenceHighlights(text, evidences, activeSpanId) {
  if (!text) {
    return "";
  }

  if (!evidences || evidences.length === 0) {
    return escapeHtml(text).replace(/\n/g, "<br>");
  }

  const ranges = [];

  evidences.forEach((evidence) => {
    if (!evidence.quote) {
      return;
    }

    const start = text.indexOf(evidence.quote);
    if (start === -1) {
      return;
    }

    ranges.push({
      start,
      end: start + evidence.quote.length,
      evidence,
    });
  });

  if (ranges.length === 0) {
    return escapeHtml(text).replace(/\n/g, "<br>");
  }

  ranges.sort((left, right) => {
    if (left.start !== right.start) {
      return left.start - right.start;
    }
    return (right.end - right.start) - (left.end - left.start);
  });

  const accepted = [];
  let cursor = -1;

  ranges.forEach((range) => {
    if (range.start < cursor) {
      return;
    }
    accepted.push(range);
    cursor = range.end;
  });

  let html = "";
  let lastIndex = 0;

  accepted.forEach((range) => {
    if (range.start > lastIndex) {
      html += escapeHtml(text.slice(lastIndex, range.start)).replace(/\n/g, "<br>");
    }

    const classNames = [
      "inline-evidence",
      `is-${range.evidence.observationId}`,
      range.evidence.manual ? "is-manual" : "",
      range.evidence.spanId === activeSpanId ? "is-active" : "",
    ].filter(Boolean).join(" ");

    html += `<mark id="reader-highlight-${range.evidence.spanId}" class="${classNames}" data-span-id="${range.evidence.spanId}" data-observation-id="${range.evidence.observationId}">${escapeHtml(text.slice(range.start, range.end)).replace(/\n/g, "<br>")}</mark>`;
    lastIndex = range.end;
  });

  if (lastIndex < text.length) {
    html += escapeHtml(text.slice(lastIndex)).replace(/\n/g, "<br>");
  }

  return html;
}

export function mountWorkbench({ root, selectionMenu, toastNode, state }) {
  function getActiveSample() {
    return state.data.samples.find((sample) => sample.id === state.activeStudent) || state.data.samples[0];
  }

  function getDimensionCodes(sample = getActiveSample()) {
    return sample.dimensionOrder || Object.keys(sample.dimensionMeta);
  }

  function getDimensionMeta(code = state.activeDimension, sample = getActiveSample()) {
    return sample.dimensionMeta[code];
  }

  function getObservationList(code = state.activeDimension, sample = getActiveSample()) {
    return sample.observationsByDimension[code] || [];
  }

  function getObservationById(id, sample = getActiveSample()) {
    return Object.values(sample.observationsByDimension)
      .flat()
      .find((item) => item.id === id);
  }

  function findEvidenceBySpanId(spanId, sample = getActiveSample()) {
    for (const observation of Object.values(sample.observationsByDimension).flat()) {
      const evidence = observation.evidence.find((item) => item.spanId === spanId);
      if (evidence) {
        return { observation, evidence };
      }
    }
    return null;
  }

  function findTranscriptEntryForEvidence(spanId) {
    const match = findEvidenceBySpanId(spanId);
    if (!match) {
      return null;
    }

    const { evidence } = match;
    const transcript = getActiveSample().transcript || [];
    if (evidence.transcriptId) {
      const directEntry = transcript.find((entry) => entry.id === evidence.transcriptId);
      if (directEntry) {
        return directEntry;
      }
    }

    return transcript.find((entry) => (entry.rawContent || entry.content || "").includes(evidence.quote)) || null;
  }

  function calculateDimensionScore(code) {
    const observations = getObservationList(code);
    if (observations.length === 0) return 0;
    const totalWeight = observations.reduce((sum, item) => sum + (item.weight ?? 1), 0);
    if (totalWeight === 0) return 0;
    const weightedSum = observations.reduce((sum, item) => sum + (item.score * (item.weight ?? 1)), 0);
    return Math.round((weightedSum / totalWeight) * 100) / 100;
  }

  function getOpenObservationIds(code = state.activeDimension) {
    const openIds = state.openObservationIds || [];
    const validIds = new Set(getObservationList(code).map((item) => item.id));
    return openIds.filter((id) => validIds.has(id));
  }

  function ensureObservationOpen(observationId) {
    const openIds = new Set(getOpenObservationIds());
    openIds.add(observationId);
    state.openObservationIds = [...openIds];
  }

  function activateDimension(code) {
    state.activeDimension = code;
    const observations = getObservationList(code);
    const defaultObservation = observations[0] || null;
    state.activeObservationId = defaultObservation?.id || null;
    state.openObservationIds = observations.map((item) => item.id);
    state.activeSpanId = defaultObservation?.evidence[0]?.spanId || null;
  }

  function activateStudent(studentId) {
    state.activeStudent = studentId;
    const activeSample = getActiveSample();
    const defaultDimension = getDimensionCodes(activeSample)[0];
    activateDimension(defaultDimension);
  }

  function getDimensionEvidenceMap(code) {
    const byEntry = new Map();
    getObservationList(code).forEach((observation) => {
      observation.evidence.forEach((evidence) => {
        if (!evidence.transcriptId) return;
        const current = byEntry.get(evidence.transcriptId) || [];
        current.push({ ...evidence, observationId: observation.id });
        byEntry.set(evidence.transcriptId, current);
      });
    });
    return byEntry;
  }

  function showToast(message) {
    state.toast = message;
    renderToast();

    if (state.toastTimer) {
      clearTimeout(state.toastTimer);
    }

    state.toastTimer = window.setTimeout(() => {
      state.toast = "";
      renderToast();
    }, 2400);
  }

  function renderToast() {
    if (!state.toast) {
      toastNode.className = "toast";
      toastNode.textContent = "";
      return;
    }
    toastNode.className = "toast is-visible";
    toastNode.textContent = state.toast;
  }

  function hideSelectionMenu(clearSelection = true) {
    state.selectionMenu.visible = false;
    renderSelectionMenu();
    if (clearSelection) {
      window.getSelection()?.removeAllRanges();
    }
  }

  function addManualEvidence(observationId) {
    const observation = getObservationById(observationId);
    if (!observation) return;

    const spanId = `manual-${Date.now()}-${Math.abs(hashCode(state.selectionMenu.text))}`;
    const item = {
      spanId,
      transcriptId: state.selectionMenu.entryId,
      quote: state.selectionMenu.text,
      sourceLabel: "manual_selection",
      supportType: "supporting",
      manual: true,
      observationId,
    };

    observation.manualEvidence.push(item);
    observation.evidence.push(item);
    state.activeDimension = observationId.split("_")[0].toUpperCase();
    state.activeObservationId = observationId;
    ensureObservationOpen(observationId);
    state.activeSpanId = spanId;
    getActiveSample().dimensionScores[state.activeDimension] = calculateDimensionScore(state.activeDimension);
    hideSelectionMenu(false);
    showToast(`已将选中文本补充到 ${observation.code}。`);
    render({ preserveInspectorScroll: true });
    window.requestAnimationFrame(() => {
      scrollReaderEvidenceIntoView(spanId);
      scrollInspectorEvidenceIntoView(spanId, observationId);
    });
  }

  function renderSelectionMenu() {
    const observationList = getObservationList();
    if (!state.selectionMenu.visible || observationList.length === 0) {
      selectionMenu.className = "selection-menu";
      selectionMenu.innerHTML = "";
      return;
    }

    selectionMenu.className = "selection-menu is-visible";
    selectionMenu.style.left = `${Math.max(16, state.selectionMenu.x - 120)}px`;
    selectionMenu.style.top = `${Math.max(16, state.selectionMenu.y)}px`;
    selectionMenu.innerHTML = `
      <div class="selection-title">新增证据到当前维度观测点</div>
      <div class="selection-quote">${escapeHtml(trimText(state.selectionMenu.text, 96))}</div>
      <div class="selection-actions">
        ${observationList.map((observation) => `
          <button data-add-evidence="${observation.id}">${escapeHtml(observation.code)}</button>
        `).join("")}
        <button data-cancel-selection>取消</button>
      </div>
    `;

    selectionMenu.querySelectorAll("[data-add-evidence]").forEach((node) => {
      node.addEventListener("click", () => addManualEvidence(node.dataset.addEvidence));
    });
    selectionMenu.querySelector("[data-cancel-selection]")?.addEventListener("click", () => hideSelectionMenu());
  }

  function scrollObservationIntoView(observationId) {
    const container = root.querySelector("#inspector-scroll");
    const target = document.getElementById(`observation-${observationId}`);
    if (!container || !target) {
      return;
    }

    const containerRect = container.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const nextTop = container.scrollTop + (targetRect.top - containerRect.top) - 12;
    container.scrollTo({
      top: Math.max(0, nextTop),
      behavior: "smooth",
    });
  }

  function scrollReaderEvidenceIntoView(spanId) {
    const highlightNode = document.getElementById(`reader-highlight-${spanId}`);
    const transcriptEntry = findTranscriptEntryForEvidence(spanId);
    const readerEntryNode = transcriptEntry ? document.getElementById(`reader-entry-${transcriptEntry.id}`) : null;

    if (highlightNode) {
      highlightNode.scrollIntoView({
        behavior: "smooth",
        block: "center",
        inline: "nearest",
      });
    } else if (readerEntryNode) {
      readerEntryNode.scrollIntoView({
        behavior: "smooth",
        block: "center",
        inline: "nearest",
      });
    }
  }

  function scrollInspectorEvidenceIntoView(spanId, observationId) {
    const container = root.querySelector("#inspector-scroll");
    const target = document.getElementById(`inspector-link-${spanId}`);
    if (container && target) {
      const containerRect = container.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      let nextTop = container.scrollTop;

      if (targetRect.top < containerRect.top) {
        nextTop += (targetRect.top - containerRect.top) - 12;
      } else if (targetRect.bottom > containerRect.bottom) {
        nextTop += (targetRect.bottom - containerRect.bottom) + 12;
      }

      container.scrollTo({
        top: Math.max(0, nextTop),
        behavior: "smooth",
      });
    }

    scrollObservationIntoView(observationId);
  }

  function preserveInspectorScroll() {
    state.inspectorScrollTop = root.querySelector("#inspector-scroll")?.scrollTop || 0;
  }

  function restoreInspectorScroll() {
    const inspectorScroll = root.querySelector("#inspector-scroll");
    if (inspectorScroll) {
      inspectorScroll.scrollTop = state.inspectorScrollTop || 0;
    }
  }

  function preservePageScroll() {
    state.pageScrollY = window.scrollY || window.pageYOffset || 0;
  }

  function restorePageScroll() {
    window.scrollTo(0, state.pageScrollY || 0);
  }

  function handleTextSelection() {
    window.setTimeout(() => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
        hideSelectionMenu(false);
        return;
      }

      const text = selection.toString().trim();
      if (text.length < 6) {
        hideSelectionMenu(false);
        return;
      }

      const range = selection.getRangeAt(0);
      const entry = range.startContainer.parentElement?.closest("[data-entry-id]");
      if (!entry) {
        hideSelectionMenu(false);
        return;
      }

      const rect = range.getBoundingClientRect();
      state.selectionMenu = {
        visible: true,
        x: rect.left + (rect.width / 2),
        y: rect.top - 12,
        entryId: entry.dataset.entryId,
        text,
      };
      renderSelectionMenu();
    }, 0);
  }

  function renderRail() {
    const activeSample = getActiveSample();
    const releaseDone = Boolean(state.releasedStudents[state.activeStudent]);
    return `
      <div class="rail-header">
        <div class="rail-mark">MAS</div>
        <div class="rail-copy">
          <div class="rail-eyebrow">Task / ${escapeHtml(activeSample.project)}</div>
          <div class="rail-title">沉浸式智能评估审核台</div>
        </div>
      </div>
      <div class="context-ribbon context-ribbon--sidebar">
        <div class="context-pill"><strong>学生</strong> ${escapeHtml(activeSample.name)}</div>
        <div class="context-pill is-status"><strong>状态</strong> ${releaseDone ? "已定稿" : "待审核"}</div>
      </div>
      <div class="student-list">
        ${state.data.samples.map((sample) => {
          const isActive = sample.id === state.activeStudent;
          const released = Boolean(state.releasedStudents[sample.id]);
          const statusClass = released ? "is-done" : "is-pending";
          return `
            <button
              class="student-card ${isActive ? "is-active" : ""}"
              data-student-id="${sample.id}"
              title="${escapeHtml(sample.name)}"
            >
              <div class="student-avatar">${escapeHtml(sample.label.slice(0, 2))}</div>
              <div class="student-meta">
                <div class="student-name">${escapeHtml(sample.name)}</div>
                <div class="student-desc">${escapeHtml(sample.subtitle)}</div>
              </div>
              <div class="student-status ${statusClass}">${released ? "✓" : "·"}</div>
            </button>
          `;
        }).join("")}
      </div>
      <div class="rail-footnote">
        这一版前端会自动扫描 <code>artifacts/{task}/{sample_name}/{dim}</code>，并把已生成的维度产物完整挂到右侧审核台。
      </div>
    `;
  }

  function renderReaderPanel() {
    const activeSample = getActiveSample();
    const dimension = getDimensionMeta();
    const evidenceByEntry = getDimensionEvidenceMap(state.activeDimension);
    const observations = getObservationList();
    const activeObservation = getObservationById(state.activeObservationId) || observations[0];
    const manualCount = Object.values(activeSample.observationsByDimension)
      .flat()
      .reduce((sum, item) => sum + item.manualEvidence.length, 0);
    const releaseDone = Boolean(state.releasedStudents[state.activeStudent]);

    return `
      <header class="panel-header">
        <div class="panel-eyebrow">Evidence Reader / ${escapeHtml(dimension.code)}</div>
        <div class="context-ribbon">
          <div class="context-pill"><strong>样本</strong> ${escapeHtml(activeSample.name)}</div>
          <div class="context-pill"><strong>当前焦点</strong> ${escapeHtml(activeObservation?.code || dimension.code)}</div>
          <div class="context-pill"><strong>任务</strong> ${escapeHtml(activeSample.project)}</div>
          <div class="context-pill is-status"><strong>状态</strong> ${releaseDone ? "已 Release" : "人工审核中"}</div>
        </div>
        <div class="header-metrics header-metrics--inline">
          <div class="metric-box metric-box--inline">
            <div class="metric-label">当前维度</div>
            <div class="metric-value">${escapeHtml(dimension.code)}</div>
          </div>
          <div class="metric-box metric-box--inline">
            <div class="metric-label">高亮证据数</div>
            <div class="metric-value">${[...evidenceByEntry.values()].flat().length}</div>
          </div>
          <div class="metric-box metric-box--inline">
            <div class="metric-label">人工增补</div>
            <div class="metric-value">${manualCount}</div>
          </div>
        </div>
      </header>
      <div class="reader-scroll" id="reader-scroll">
        ${activeSample.transcript.length === 0 ? `
          <div class="placeholder-note">
            当前样本未找到对应的 <code>data/training/${escapeHtml(activeSample.project)}/${escapeHtml(activeSample.sampleName)}.md</code>，
            因此暂时只能展示右侧评估产物，无法建立左侧证据定位。
          </div>
        ` : `
          <div class="timeline">
            ${activeSample.transcript.map((entry) => {
              const relatedEvidence = evidenceByEntry.get(entry.id) || [];
              const isHuman = entry.kind === "human";
              const isSystem = entry.kind === "system" || entry.kind === "meta";
              const isActive = relatedEvidence.some((item) => item.spanId === state.activeSpanId);
              return `
                <article id="reader-entry-${entry.id}" class="entry ${isHuman ? "is-human" : ""} ${isSystem ? "is-system" : ""} ${isActive ? "is-active" : ""}" data-entry-id="${entry.id}">
                  <div class="entry-meta">
                    <div class="entry-role">
                      <span class="role-badge">${escapeHtml(entry.roleTag)}</span>
                      <span>${escapeHtml(entry.role)}</span>
                      <span>·</span>
                      <span>${escapeHtml(entry.phase)}</span>
                    </div>
                      <span>${escapeHtml(entry.time)}</span>
                  </div>
                  <p class="entry-content">${renderTextWithEvidenceHighlights(entry.rawContent || entry.content, relatedEvidence, state.activeSpanId)}</p>
                  ${relatedEvidence.length > 0 ? `
                    <div class="quote-stack">
                      ${relatedEvidence.map((evidence) => `
                        <button
                          class="${evidence.manual ? "manual-chip" : "quote-link"} is-${evidence.observationId} ${state.activeSpanId === evidence.spanId ? "is-active" : ""}"
                          data-span-id="${evidence.spanId}"
                          data-observation-id="${evidence.observationId}"
                        >
                          <span class="obs-dot is-${evidence.observationId}"></span>
                          <span>${escapeHtml(evidence.observationId.toUpperCase())}</span>
                          ${evidence.manual ? "<span>人工增补</span>" : ""}
                        </button>
                      `).join("")}
                    </div>
                  ` : ""}
                </article>
              `;
            }).join("")}
          </div>
        `}
        <div class="selection-hint">
          在左侧阅读区选择任意文本，会弹出“新增证据”菜单。新增证据会即时写入当前维度观测点，并参与左右联动。
        </div>
      </div>
      <footer class="panel-footer">
        <div class="footer-note">
          当前样本：<strong>${escapeHtml(activeSample.name)}</strong> · 数据来自真实 <code>data/</code> 与 <code>artifacts/</code>
        </div>
        <button class="ghost-btn" data-reset-manual>清空人工增补</button>
      </footer>
    `;
  }

  function renderObservationCards(observations) {
    const openObservationIds = new Set(getOpenObservationIds());
    return `
      <div class="obs-list">
        ${observations.map((observation) => {
          const isOpen = openObservationIds.has(observation.id);
          const hasAdjudication = observation.raters.some((rater) => rater.id === "rater_3");
          return `
            <section class="obs-card ${isOpen ? "is-open" : ""}" id="observation-${observation.id}">
              <button class="obs-header" data-toggle-observation="${observation.id}">
                <div class="obs-left">
                  <div class="obs-code">
                    <span class="obs-dot is-${observation.id}"></span>
                    <span>${escapeHtml(observation.code)}</span>
                  </div>
                  <div class="obs-title">${escapeHtml(observation.title)}</div>
                </div>
                <div class="obs-right">
                  <div class="score-pill">${observation.score}</div>
                </div>
              </button>
              ${isOpen ? `
                <div class="obs-body">
                  <section>
                    <div class="section-label">
                      <span>量表锚点</span>
                      <div class="rubric-wrap">
                        <button class="meta-chip" type="button">查看量表</button>
                        <div class="rubric-popover rubric-popover--wide">${renderRubricPopover(observation)}</div>
                      </div>
                    </div>
                  </section>

                  <section class="final-edit">
                    <div class="section-label">
                      <span>最终输出</span>
                      <div class="mini-chip ${observation.manualEvidence.length > 0 ? "is-manual" : ""}">
                        ${observation.manualEvidence.length > 0 ? `${observation.manualEvidence.length} 条人工补充` : "可编辑"}
                      </div>
                    </div>
                    <div class="field-row">
                      <label for="score-${observation.id}">最终分数</label>
                      <select id="score-${observation.id}" class="score-select" data-score-input="${observation.id}">
                        ${[1, 2, 3, 4, 5].map((score) => `
                          <option value="${score}" ${score === observation.score ? "selected" : ""}>${score} 分</option>
                        `).join("")}
                      </select>
                    </div>
                    <div class="field-row">
                      <label for="feedback-${observation.id}">最终反馈</label>
                      <textarea id="feedback-${observation.id}" class="feedback-edit" data-feedback-input="${observation.id}">${escapeHtml(observation.feedback)}</textarea>
                    </div>
                  </section>

                  <section>
                    <div class="section-label">
                      <span>证据引用</span>
                      <span>${observation.evidence.length} 条</span>
                    </div>
                    ${observation.evidence.length > 0 ? `
                      <div class="quote-stack">
                        ${observation.evidence.map((evidence) => `
                          ${evidence.locatable || evidence.manual ? `
                            <button
                              id="inspector-link-${evidence.spanId}"
                              class="${evidence.manual ? "manual-chip" : "evidence-link"} is-${observation.id} ${state.activeSpanId === evidence.spanId ? "is-active" : ""}"
                              data-span-id="${evidence.spanId}"
                              data-observation-id="${observation.id}"
                            >
                              <span class="obs-dot is-${observation.id}"></span>
                              <span>${escapeHtml(trimText(evidence.quote, 34))}</span>
                            </button>
                          ` : `
                            <div class="evidence-link is-${observation.id} is-unlocatable">
                              <span class="obs-dot is-${observation.id}"></span>
                              <span>${escapeHtml(trimText(evidence.quote, 34))}</span>
                              <span class="evidence-note">无原文定位</span>
                            </div>
                          `}
                        `).join("")}
                      </div>
                    ` : `
                      <div class="placeholder-note">当前观测点暂无可回链的证据片段。</div>
                    `}
                  </section>

                  ${observation.conflict ? `
                    <section>
                      <div class="section-label">
                        <span>裁决记录</span>
                      </div>
                      <div class="compact-card">
                        <div class="compact-feedback">
                          <strong>冲突：</strong>${escapeHtml(observation.conflict.detail)}<br>
                          <strong>建议路径：</strong>${escapeHtml(observation.conflict.recommendedPath)}<br>
                          <strong>最终决议：</strong>${escapeHtml(observation.conflict.resolution || "当前记录未提供额外裁决说明。")}
                        </div>
                      </div>
                    </section>
                  ` : ""}

                  <section>
                    <div class="section-label">
                      <span>评分过程展示区</span>
                      <div class="mini-chip">${observation.raters.length} 位 rater</div>
                    </div>
                    ${observation.raters.length > 0 ? `
                      <div class="rater-grid">
                        ${observation.raters.map((rater) => `
                          <article class="rater-card ${hasAdjudication && rater.id === "rater_3" ? "is-adjudication" : ""}">
                            <div class="rater-head">
                              <div class="rater-name">${escapeHtml(rater.label)}</div>
                              <div class="score-pill">${rater.score}</div>
                            </div>
                            <p class="rater-rationale">${escapeHtml(rater.rationale || "当前 rater 未提供额外评分理由。")}</p>
                            <div class="rater-descriptor">${escapeHtml(rater.descriptor || "当前 rater 未提供 descriptor 摘要。")}</div>
                          </article>
                        `).join("")}
                      </div>
                    ` : `
                      <div class="placeholder-note">当前维度未检测到独立的 hypothesis / scoring record 产物。</div>
                    `}
                  </section>

                </div>
              ` : ""}
            </section>
          `;
        }).join("")}
      </div>
    `;
  }

  function renderInspectorPanel() {
    const activeSample = getActiveSample();
    const observations = getObservationList();
    const releaseDone = Boolean(state.releasedStudents[state.activeStudent]);
    const adjudicated = observations.some((observation) => observation.raters.some((rater) => rater.id === "rater_3"));

    return `
      <header class="panel-header">
        <div class="inspector-top inspector-top--compact">
          <div class="nav-row">
            ${getDimensionCodes(activeSample).map((code) => `
              <button class="nav-chip ${state.activeDimension === code ? "is-active" : ""}" data-dimension="${code}">
                <div class="nav-code">
                  <span>${escapeHtml(code)}</span>
                  <span class="nav-score">${formatScore(activeSample.dimensionScores[code])}</span>
                </div>
                <div class="nav-label">${escapeHtml(activeSample.dimensionMeta[code].title)}</div>
              </button>
            `).join("")}
          </div>
          <div class="summary-grid">
            <div class="summary-card">
              <span>当前维度分数</span>
              <strong>${formatScore(activeSample.dimensionScores[state.activeDimension])}</strong>
            </div>
            ${observations.map((observation) => `
              <div class="summary-card summary-card--observation">
                <span>${escapeHtml(observation.code)}</span>
                <strong>${observation.score}</strong>
              </div>
            `).join("")}
            ${adjudicated ? '<div class="mini-chip is-adjudicated summary-chip">已触发裁决</div>' : ""}
          </div>
        </div>
      </header>
      <div class="inspector-scroll" id="inspector-scroll">
        ${renderObservationCards(observations)}
      </div>
      <footer class="panel-footer">
        <div class="footer-note">
          ${releaseDone ? "已完成定稿，左侧学生状态已更新为 ✓。" : "可原地改写最终反馈与分数，确认无误后点击 Release。"}
        </div>
        <button class="release-btn ${releaseDone ? "is-done" : ""}" data-release>
          ${releaseDone ? "已 Release" : "Release 定稿"}
        </button>
      </footer>
    `;
  }

  function renderShell() {
    root.innerHTML = `
      <aside class="rail">${renderRail()}</aside>
      <main class="workspace">
        <section class="panel panel--reader">
          <div class="panel-shell">${renderReaderPanel()}</div>
        </section>
        <aside class="panel panel--inspector">
          <div class="panel-shell">${renderInspectorPanel()}</div>
        </aside>
      </main>
    `;

    renderSelectionMenu();
    renderToast();
    bindEvents();
  }

  function bindEvents() {
    root.querySelectorAll("[data-student-id]").forEach((node) => {
      node.addEventListener("click", () => {
        activateStudent(node.dataset.studentId);
        hideSelectionMenu(false);
        render();
      });
    });

    root.querySelectorAll("[data-dimension]").forEach((node) => {
      node.addEventListener("mousedown", (event) => event.preventDefault());
      node.addEventListener("click", () => {
        activateDimension(node.dataset.dimension);
        hideSelectionMenu(false);
        render();
      });
    });

    root.querySelectorAll("[data-toggle-observation]").forEach((node) => {
      node.addEventListener("mousedown", (event) => event.preventDefault());
      node.addEventListener("click", () => {
        const id = node.dataset.toggleObservation;
        const openIds = new Set(getOpenObservationIds());
        if (openIds.has(id)) {
          openIds.delete(id);
        } else {
          openIds.add(id);
        }
        state.openObservationIds = [...openIds];
        state.activeObservationId = id;
        const defaultSpanId = getObservationById(id)?.evidence[0]?.spanId || null;
        state.activeSpanId = defaultSpanId || state.activeSpanId;
        render({ preserveInspectorScroll: true, preservePageScroll: true });
        window.requestAnimationFrame(() => {
          scrollObservationIntoView(id);
        });
      });
    });

    root.querySelectorAll("[data-span-id]").forEach((node) => {
      if (node.closest("#inspector-scroll")) {
        node.addEventListener("mousedown", (event) => event.preventDefault());
      }
      node.addEventListener("click", () => {
        const { spanId, observationId } = node.dataset;
        const origin = node.closest("#inspector-scroll") ? "inspector" : "reader";
        state.activeObservationId = observationId;
        ensureObservationOpen(observationId);
        state.activeSpanId = spanId;
        render({ preserveInspectorScroll: true, preservePageScroll: true });
        window.requestAnimationFrame(() => {
          scrollReaderEvidenceIntoView(spanId);
          if (origin !== "inspector") {
            scrollInspectorEvidenceIntoView(spanId, observationId);
          }
        });
      });
    });

    root.querySelectorAll("[data-score-input]").forEach((node) => {
      node.addEventListener("change", () => {
        const observation = getObservationById(node.dataset.scoreInput);
        observation.score = Number(node.value);
        getActiveSample().dimensionScores[state.activeDimension] = calculateDimensionScore(state.activeDimension);
        showToast(`${observation.code} 分数已改为 ${observation.score} 分。`);
        render({ preserveInspectorScroll: true, preservePageScroll: true });
      });
    });

    root.querySelectorAll("[data-feedback-input]").forEach((node) => {
      node.addEventListener("change", () => {
        const observation = getObservationById(node.dataset.feedbackInput);
        observation.feedback = node.value.trim();
        showToast(`${observation.code} 最终反馈已更新。`);
        render({ preserveInspectorScroll: true, preservePageScroll: true });
      });
    });

    root.querySelector("[data-release]")?.addEventListener("click", () => {
      state.releasedStudents[state.activeStudent] = true;
      showToast("当前学生已 Release，左侧状态更新为 ✓。");
      render({ preserveInspectorScroll: true, preservePageScroll: true });
    });

    root.querySelector("[data-reset-manual]")?.addEventListener("click", () => {
      Object.values(getActiveSample().observationsByDimension)
        .flat()
        .forEach((observation) => {
          observation.manualEvidence = [];
          observation.evidence = observation.evidence.filter((item) => !item.manual);
        });
      state.activeSpanId = getObservationList()[0]?.evidence[0]?.spanId || null;
      getActiveSample().dimensionScores[state.activeDimension] = calculateDimensionScore(state.activeDimension);
      showToast("已清空当前样本的人工增补证据。");
      render({ preserveInspectorScroll: true, preservePageScroll: true });
    });

    root.querySelector("#reader-scroll")?.addEventListener("mouseup", handleTextSelection);
  }

  function render(options = {}) {
    if (options.preservePageScroll) {
      preservePageScroll();
    }
    if (options.preserveInspectorScroll) {
      preserveInspectorScroll();
    }
    renderShell();
    if (options.preservePageScroll) {
      restorePageScroll();
    }
    if (options.preserveInspectorScroll) {
      restoreInspectorScroll();
    }
  }

  document.addEventListener("click", (event) => {
    if (!selectionMenu.contains(event.target) && !event.target.closest("[data-entry-id]")) {
      hideSelectionMenu(false);
    }
  });

  render();
}
