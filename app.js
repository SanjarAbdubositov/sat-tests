/**
 * ============================================================================
 * BLUEBOOK™ DIGITAL SAT MOCK PLATFORM — FRONTEND EXAM & ANALYTICS ENGINE
 * 1:1 Local Clone of the College Board Bluebook Interface (Practice Tests 4–11)
 * ============================================================================
 */

var calculator = null; // Global Desmos GraphingCalculator instance per specification

(function () {
  "use strict";

  const TEST_NUMBERS = [4, 5, 6, 7, 8, 9, 10, 11];
  const STORAGE_KEYS = {
    USER_PROFILE: "bb_cloner_user_profile_v1",
    SESSION_LOGS: "bb_cloner_session_logs_v1",
    SCORE_HISTORY: "bb_cloner_score_history_v1",
    ACTIVE_PROGRESS: "bb_cloner_active_progress_v1",
  };

  // Standardized SAT Module Configurations
  // Reading & Writing: 32:00 (1920s) per module | Math: 35:00 (2100s) per module
  const MODULE_DEFS = [
    { index: 0, type: "Reading", module: 1, label: "Section 1, Module 1: Reading and Writing", shortLabel: "R&W Mod 1", durationSec: 32 * 60 },
    { index: 1, type: "Reading", module: 2, label: "Section 1, Module 2: Reading and Writing", shortLabel: "R&W Mod 2", durationSec: 32 * 60 },
    { index: 2, type: "Math", module: 1, label: "Section 2, Module 1: Math", shortLabel: "Math Mod 1", durationSec: 35 * 60 },
    { index: 3, type: "Math", module: 2, label: "Section 2, Module 2: Math", shortLabel: "Math Mod 2", durationSec: 35 * 60 },
  ];

  // Application State
  const state = {
    userName: "Sanjar Abdubositov",
    mocksData: {},       // { "4": [120 items], ..., "11": [120 items] }
    scoringTables: {},   // { "4": { Reading: {...}, Math: {...} }, ... }
    currentFilter: "all",

    // Active Exam State
    activeTestNum: null,
    activeModuleIdx: 0,  // 0..3
    activeQuestionIdx: 0, // 0..(moduleQuestions.length - 1)
    timerSecondsLeft: 32 * 60,
    timerInterval: null,
    timerHidden: false,
    examStartedAt: null,

    // Per-Test User Progress: { answers: { qId: 0..3 }, flags: { qId: true }, strikes: { qId: [0..3] }, highlights: { qId: { lineIdx: color } }, modTimers: { 0: 1920, ... } }
    progressByTest: {},

    // Highlighter & Strikethrough Tool States
    highlighterEnabled: true,
    highlightColor: "yellow",
    strikethroughMode: true,

    // Review Page State
    activeReviewRecord: null,
    reviewModuleFilter: "all",
    selectedReviewQuestionId: 1,
  };

  // ==========================================================================
  // 1. INITIALIZATION & DYNAMIC DATA LOADING (JSON FETCH + BUNDLE FALLBACK)
  // ==========================================================================

  async function initApp() {
    loadPersistentStorage();
    recordSessionLog("SESSION_ENTRANCE", `Candidate ${state.userName} initialized Bluebook Client Cloner workspace.`);
    await loadAllMocksAndScoring();
    initDesmosCalculator();
    initDraggableModals();
    initSplitPaneResizer();
    bindEventListeners();
    renderDashboard();
  }

  async function loadAllMocksAndScoring() {
    // 1. Load pre-bundled data if available (ensures instant zero-CORS operation on file://)
    if (window.SAT_MOCKS_DATA) {
      state.mocksData = Object.assign({}, window.SAT_MOCKS_DATA);
    }
    if (window.SAT_SCORING_DATA) {
      state.scoringTables = Object.assign({}, window.SAT_SCORING_DATA);
    }

    // 2. Attempt dynamic fetch() from ./data/mock{N}.json or ./mock{N}.json when served over HTTP/HTTPS
    if (window.location.protocol.startsWith("http")) {
      try {
        const scoreResp = await fetch("data/scoring_tables.json");
        if (scoreResp.ok) {
          state.scoringTables = await scoreResp.json();
        }
      } catch (_) {
        // Fallback already loaded from window.SAT_SCORING_DATA
      }

      await Promise.all(
        TEST_NUMBERS.map(async (num) => {
          try {
            const resp = await fetch(`mock${num}.json`);
            if (resp.ok) {
              state.mocksData[String(num)] = await resp.json();
            }
          } catch (_) {
            // Keep pre-bundled data for mock{num}
          }
        })
      );
    }

    const loadedTestsCount = Object.keys(state.mocksData).length;
    const totalQuestions = Object.values(state.mocksData).reduce((acc, arr) => acc + (Array.isArray(arr) ? arr.length : 0), 0);
    const pillText = document.getElementById("ingestion-status-text");
    if (pillText) {
      pillText.textContent = `${loadedTestsCount} Practice Tests Synced (${totalQuestions} Items)`;
    }
  }

  // ==========================================================================
  // 2. LOCALSTORAGE TELEMETRY & PERSISTENCE ENGINE
  // ==========================================================================

  function loadPersistentStorage() {
    try {
      const savedUser = localStorage.getItem(STORAGE_KEYS.USER_PROFILE);
      if (savedUser) {
        const parsed = JSON.parse(savedUser);
        if (parsed && parsed.userName) state.userName = parsed.userName;
      }
      const savedProgress = localStorage.getItem(STORAGE_KEYS.ACTIVE_PROGRESS);
      if (savedProgress) {
        state.progressByTest = JSON.parse(savedProgress) || {};
      }
    } catch (err) {
      console.warn("Local storage load warning:", err);
    }
    updateUserBadges();
  }

  function saveActiveProgress() {
    try {
      localStorage.setItem(STORAGE_KEYS.ACTIVE_PROGRESS, JSON.stringify(state.progressByTest));
    } catch (err) {
      console.warn("Unable to save progress:", err);
    }
  }

  function getSessionLogs() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEYS.SESSION_LOGS) || "[]");
    } catch (_) {
      return [];
    }
  }

  function recordSessionLog(eventType, message) {
    try {
      const logs = getSessionLogs();
      logs.unshift({
        id: Date.now() + "-" + Math.random().toString(36).slice(2, 6),
        timestamp: new Date().toISOString(),
        candidate: state.userName,
        eventType,
        message,
      });
      localStorage.setItem(STORAGE_KEYS.SESSION_LOGS, JSON.stringify(logs.slice(0, 120)));
    } catch (err) {
      console.warn("Audit log write warning:", err);
    }
  }

  function getScoreHistory() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEYS.SCORE_HISTORY) || "[]");
    } catch (_) {
      return [];
    }
  }

  function saveScoreRecord(record) {
    try {
      const history = getScoreHistory();
      history.unshift(record);
      localStorage.setItem(STORAGE_KEYS.SCORE_HISTORY, JSON.stringify(history));
    } catch (err) {
      console.warn("Score history save warning:", err);
    }
  }

  function ensureTestProgress(testNum) {
    const key = String(testNum);
    if (!state.progressByTest[key]) {
      state.progressByTest[key] = {
        testNumber: testNum,
        status: "in-progress",
        currentModuleIdx: 0,
        currentQuestionIdx: 0,
        answers: {},     // { [qId]: 0..3 }
        flags: {},       // { [qId]: boolean }
        strikes: {},     // { [qId]: [0, 2] }
        highlights: {},  // { [qId]: { [lineIdx]: "yellow"|"blue"|"pink" } }
        modTimers: {
          0: MODULE_DEFS[0].durationSec,
          1: MODULE_DEFS[1].durationSec,
          2: MODULE_DEFS[2].durationSec,
          3: MODULE_DEFS[3].durationSec,
        },
        updatedAt: new Date().toISOString(),
      };
    }
    return state.progressByTest[key];
  }

  function updateUserBadges() {
    const initials = state.userName
      .split(" ")
      .filter(Boolean)
      .map((p) => p[0].toUpperCase())
      .slice(0, 2)
      .join("") || "SA";
    const av = document.getElementById("user-avatar-initials");
    const nm = document.getElementById("active-user-name");
    const btmNm = document.getElementById("bb-bottom-candidate-name");
    if (av) av.textContent = initials;
    if (nm) nm.textContent = state.userName;
    if (btmNm) btmNm.textContent = state.userName;
  }

  // ==========================================================================
  // 3. DASHBOARD & ADMINISTRATION PANEL RENDERING
  // ==========================================================================

  function renderDashboard() {
    const history = getScoreHistory();
    const logs = getSessionLogs();

    // Update KPI Cards
    const kpiHighest = document.getElementById("kpi-highest-score");
    const kpiHighestSub = document.getElementById("kpi-highest-sub");
    const kpiCompleted = document.getElementById("kpi-completed-count");
    const kpiAvgAcc = document.getElementById("kpi-avg-accuracy");
    const kpiSessions = document.getElementById("kpi-session-count");
    const kpiLastEnt = document.getElementById("kpi-last-entrance");

    if (history.length > 0) {
      const best = history.reduce((prev, cur) => (cur.compositeScore > prev.compositeScore ? cur : prev), history[0]);
      kpiHighest.textContent = `${best.compositeScore}`;
      kpiHighestSub.textContent = `Test #${best.testNumber} (R&W ${best.rwScore} • Math ${best.mathScore})`;
      kpiCompleted.textContent = `${history.length}`;
      const avgAcc = Math.round(history.reduce((s, r) => s + r.accuracyPct, 0) / history.length);
      kpiAvgAcc.textContent = `Avg Accuracy: ${avgAcc}% across attempts`;
    } else {
      kpiHighest.textContent = "—";
      kpiHighestSub.textContent = "No completed attempts yet";
      kpiCompleted.textContent = "0";
      kpiAvgAcc.textContent = "Avg Accuracy: —";
    }

    kpiSessions.textContent = `${logs.length}`;
    if (logs.length > 0) {
      kpiLastEnt.textContent = `Last event: ${formatShortTime(logs[0].timestamp)}`;
    }

    renderMockSelectorCards(history);
    renderAdminScoreTable(history);
    renderAdminAuditLogs(logs);
  }

  function renderMockSelectorCards(history) {
    const grid = document.getElementById("mock-cards-grid");
    if (!grid) return;
    grid.innerHTML = "";

    TEST_NUMBERS.forEach((testNum) => {
      const questions = state.mocksData[String(testNum)] || [];
      const prog = state.progressByTest[String(testNum)];
      const testHistory = history.filter((h) => Number(h.testNumber) === testNum);
      const latestScore = testHistory.length > 0 ? testHistory[0] : null;
      const answeredCount = prog && prog.answers ? Object.keys(prog.answers).length : 0;

      let statusType = "ready";
      let statusLabel = "Not Started";
      if (latestScore && answeredCount === 0) {
        statusType = "completed";
        statusLabel = "Completed";
      } else if (answeredCount > 0) {
        statusType = "in-progress";
        statusLabel = `In Progress (${answeredCount}/${questions.length})`;
      } else if (latestScore) {
        statusType = "completed";
        statusLabel = "Completed";
      }

      if (state.currentFilter === "in-progress" && statusType !== "in-progress") return;
      if (state.currentFilter === "completed" && !latestScore) return;

      const rwCount = questions.filter((q) => q.type === "Reading").length;
      const mathCount = questions.filter((q) => q.type === "Math").length;

      const card = document.createElement("article");
      card.className = "mock-card";
      card.innerHTML = `
        <div class="mock-card-top">
          <div class="mock-card-banner">
            <span class="mock-num-badge">PRACTICE TEST #${testNum}</span>
            <span class="mock-status-pill ${statusType}">${statusLabel}</span>
          </div>
          <h3 class="mock-card-title">SAT Practice Test #${testNum} (Digital)</h3>
          <p class="mock-card-meta">
            Full-length official College Board simulation featuring 2 Reading &amp; Writing modules and 2 Math modules with integrated Desmos graphing calculator.
          </p>
          <div class="mock-spec-pills">
            <span class="spec-pill">R&amp;W: ${rwCount} Qs (64 min)</span>
            <span class="spec-pill">Math: ${mathCount} Qs (70 min)</span>
            <span class="spec-pill">mock${testNum}.json</span>
          </div>
          <div class="mock-score-preview">
            ${
              latestScore
                ? `<span>Latest Score (R&amp;W ${latestScore.rwScore} / Math ${latestScore.mathScore})</span>
                   <strong>${latestScore.compositeScore} / 1600</strong>`
                : `<span>Official 400–1600 Scaling Ready</span>
                   <strong>${answeredCount > 0 ? Math.round((answeredCount / Math.max(1, questions.length)) * 100) + "% Done" : "Ready"}</strong>`
            }
          </div>
        </div>
        <div class="mock-card-actions">
          <button type="button" class="btn-launch-exam" data-launch-test="${testNum}">
            ${answeredCount > 0 ? "Resume Bluebook Exam" : "Start Bluebook Exam"}
          </button>
          ${
            latestScore
              ? `<button type="button" class="btn-review-exam" data-review-record="${latestScore.recordId}">Review Score</button>`
              : `<button type="button" class="btn-review-exam" data-diagnostic-preview="${testNum}" title="Inspect Answer Key & Explanations">Inspect Key</button>`
          }
        </div>
      `;
      grid.appendChild(card);
    });

    // Bind card buttons
    grid.querySelectorAll("[data-launch-test]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tNum = Number(btn.getAttribute("data-launch-test"));
        launchExamClient(tNum);
      });
    });

    grid.querySelectorAll("[data-review-record]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const recId = btn.getAttribute("data-review-record");
        const rec = getScoreHistory().find((r) => r.recordId === recId);
        if (rec) openReviewWorkspace(rec);
      });
    });

    grid.querySelectorAll("[data-diagnostic-preview]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const tNum = Number(btn.getAttribute("data-diagnostic-preview"));
        const previewRecord = computeExamScoreRecord(tNum, false);
        openReviewWorkspace(previewRecord);
      });
    });
  }

  function renderAdminScoreTable(history) {
    const tbody = document.getElementById("score-history-tbody");
    const badge = document.getElementById("score-history-badge");
    if (!tbody || !badge) return;

    badge.textContent = `${history.length} Record${history.length === 1 ? "" : "s"}`;
    if (history.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:#64748b;padding:22px;">No completed exam records yet. Launch any Practice Test (4–11) or click "Inspect Key" to view detailed scoring analytics.</td></tr>`;
      return;
    }

    tbody.innerHTML = history
      .map(
        (rec) => `
      <tr>
        <td>${formatShortTime(rec.timestamp)}</td>
        <td><strong>Practice Test #${rec.testNumber}</strong></td>
        <td><strong style="color:#002d62;font-family:var(--font-mono);font-size:14px;">${rec.compositeScore}</strong> <span style="color:#64748b;font-size:11px;">(${rec.compositeLower}–${rec.compositeUpper})</span></td>
        <td>${rec.rwScore} <span style="color:#64748b;font-size:11px;">(${rec.rwRaw}/66)</span></td>
        <td>${rec.mathScore} <span style="color:#64748b;font-size:11px;">(${rec.mathRaw}/54)</span></td>
        <td>
          <span style="color:#15803d;font-weight:700;">${rec.correctCount}C</span> /
          <span style="color:#b91c1c;font-weight:700;">${rec.incorrectCount}I</span> /
          <span style="color:#64748b;font-weight:700;">${rec.blankCount}B</span>
        </td>
        <td>
          <button type="button" class="btn-secondary-sm" data-open-history-id="${rec.recordId}" style="padding:4px 10px;font-size:11.5px;">Review</button>
        </td>
      </tr>
    `
      )
      .join("");

    tbody.querySelectorAll("[data-open-history-id]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const recId = btn.getAttribute("data-open-history-id");
        const rec = history.find((r) => r.recordId === recId);
        if (rec) openReviewWorkspace(rec);
      });
    });
  }

  function renderAdminAuditLogs(logs) {
    const container = document.getElementById("session-audit-log");
    const badge = document.getElementById("session-logs-badge");
    if (!container || !badge) return;

    badge.textContent = `${logs.length} Event${logs.length === 1 ? "" : "s"}`;
    container.innerHTML = logs
      .slice(0, 40)
      .map(
        (item) => `
      <div class="audit-log-item">
        <div class="audit-log-time">[${formatShortTime(item.timestamp)}] • <strong>${escapeHtml(item.eventType)}</strong></div>
        <div>${escapeHtml(item.message)}</div>
      </div>
    `
      )
      .join("");
  }

  // ==========================================================================
  // 4. 1:1 BLUEBOOK TESTING CLIENT (EXAM ENGINE)
  // ==========================================================================

  function launchExamClient(testNum) {
    state.activeTestNum = testNum;
    const prog = ensureTestProgress(testNum);
    state.activeModuleIdx = prog.currentModuleIdx || 0;
    state.activeQuestionIdx = prog.currentQuestionIdx || 0;
    state.examStartedAt = Date.now();

    recordSessionLog("EXAM_LAUNCH", `Launched SAT Practice Test #${testNum} (${MODULE_DEFS[state.activeModuleIdx].label}).`);
    switchView("exam-view");
    loadActiveModule(state.activeModuleIdx, state.activeQuestionIdx);
  }

  function getActiveModuleQuestions() {
    const allItems = state.mocksData[String(state.activeTestNum)] || [];
    const modDef = MODULE_DEFS[state.activeModuleIdx];
    return allItems.filter((item) => item.type === modDef.type && Number(item.module) === modDef.module);
  }

  function loadActiveModule(moduleIdx, questionIdx = 0) {
    state.activeModuleIdx = moduleIdx;
    const modDef = MODULE_DEFS[moduleIdx];
    const modQuestions = getActiveModuleQuestions();
    state.activeQuestionIdx = Math.max(0, Math.min(questionIdx, Math.max(0, modQuestions.length - 1)));

    const prog = ensureTestProgress(state.activeTestNum);
    prog.currentModuleIdx = moduleIdx;
    prog.currentQuestionIdx = state.activeQuestionIdx;

    // Update Top Bar Labels
    document.getElementById("bb-active-section-title").textContent = modDef.label;
    document.getElementById("bb-active-test-title").textContent = `SAT Practice Test #${state.activeTestNum}`;
    document.getElementById("bb-module-selector").value = String(moduleIdx);
    document.getElementById("bb-nav-popover-title").textContent = modDef.label;

    // Update Directions Text for Reading/Writing vs Math
    const dirText = document.getElementById("bb-directions-text");
    if (modDef.type === "Reading") {
      dirText.textContent =
        "The questions in this section address a number of important reading and writing skills. Each question includes one or more passages, which may include a table or graph. Read each passage and question carefully, and then choose the best answer to the question based on the passage(s). All questions in this section are multiple-choice with four answer choices. Each question has a single best answer.";
    } else {
      dirText.textContent =
        "The questions in this section address a number of important math skills. Use of an embedded Desmos graphing calculator is permitted for all questions. Unless otherwise indicated: all variables and expressions represent real numbers; figures provided are drawn to scale; all figures lie in a plane; and the domain of a given function f is the set of all real numbers x for which f(x) is a real number.";
    }

    // Start Standardized Countdown Timer (32:00 RW / 35:00 Math)
    const savedSec = prog.modTimers && typeof prog.modTimers[moduleIdx] === "number" ? prog.modTimers[moduleIdx] : modDef.durationSec;
    startModuleTimer(savedSec > 0 ? savedSec : modDef.durationSec);

    saveActiveProgress();
    renderActiveQuestion();
  }

  function startModuleTimer(initialSeconds) {
    if (state.timerInterval) clearInterval(state.timerInterval);
    state.timerSecondsLeft = initialSeconds;
    updateTimerDOM();

    state.timerInterval = setInterval(() => {
      state.timerSecondsLeft = Math.max(0, state.timerSecondsLeft - 1);
      const prog = ensureTestProgress(state.activeTestNum);
      if (prog && prog.modTimers) {
        prog.modTimers[state.activeModuleIdx] = state.timerSecondsLeft;
      }
      updateTimerDOM();

      if (state.timerSecondsLeft <= 0) {
        clearInterval(state.timerInterval);
        recordSessionLog("MODULE_TIMEOUT", `Time expired on Test #${state.activeTestNum} — ${MODULE_DEFS[state.activeModuleIdx].shortLabel}.`);
        openModuleTransitionModal(true);
      }
    }, 1000);
  }

  function updateTimerDOM() {
    const timerEl = document.getElementById("bb-timer-value");
    const iconEl = document.getElementById("bb-timer-hidden-indicator");
    const toggleBtn = document.getElementById("bb-timer-toggle-btn");
    if (!timerEl) return;

    const mins = Math.floor(state.timerSecondsLeft / 60);
    const secs = state.timerSecondsLeft % 60;
    timerEl.textContent = `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;

    if (state.timerSecondsLeft <= 300) {
      timerEl.classList.add("timer-warning");
    } else {
      timerEl.classList.remove("timer-warning");
    }

    if (state.timerHidden) {
      timerEl.classList.add("hidden");
      iconEl.classList.remove("hidden");
      toggleBtn.textContent = "Show";
    } else {
      timerEl.classList.remove("hidden");
      iconEl.classList.add("hidden");
      toggleBtn.textContent = "Hide";
    }
  }

  function renderActiveQuestion() {
    const modQuestions = getActiveModuleQuestions();
    if (modQuestions.length === 0) return;

    const qObj = modQuestions[state.activeQuestionIdx];
    const qNumInMod = state.activeQuestionIdx + 1;
    const totalInMod = modQuestions.length;
    const prog = ensureTestProgress(state.activeTestNum);
    prog.currentQuestionIdx = state.activeQuestionIdx;

    // 1. Render Left Column: Passage or Math Scenario with Line Highlighter
    renderHighlightablePassage(qObj, prog);

    // 2. Render Right Column Header & Prompt
    document.getElementById("bb-q-number-box").textContent = `${qNumInMod}`;
    document.getElementById("bb-q-progress-text").textContent = `Question ${qNumInMod} of ${totalInMod}`;
    document.getElementById("bb-pill-trigger-text").textContent = `Question ${qNumInMod} of ${totalInMod}`;
    document.getElementById("bb-question-prompt").textContent = qObj.question;

    // Flag for Review state
    const isFlagged = Boolean(prog.flags[qObj.id]);
    const flagBtn = document.getElementById("bb-flag-review-btn");
    const flagLbl = document.getElementById("bb-flag-label");
    flagBtn.classList.toggle("flagged", isFlagged);
    flagLbl.textContent = isFlagged ? "Marked for Review" : "Mark for Review";

    // 3. Render 4 Multiple-Choice Alternatives (A, B, C, D) + Strikethrough Controls
    const optionsContainer = document.getElementById("bb-options-container");
    optionsContainer.innerHTML = "";
    const letters = ["A", "B", "C", "D"];
    const selectedAns = prog.answers[qObj.id];
    const struckIndices = prog.strikes[qObj.id] || [];

    qObj.options.forEach((optText, idx) => {
      const isSelected = selectedAns === idx;
      const isStruck = struckIndices.includes(idx);

      const row = document.createElement("div");
      row.className = `bb-option-row${isStruck ? " eliminated" : ""}`;

      const card = document.createElement("div");
      card.className = `bb-option-card${isSelected ? " selected" : ""}`;
      card.setAttribute("role", "radio");
      card.setAttribute("aria-checked", isSelected ? "true" : "false");
      card.innerHTML = `
        <span class="bb-option-letter">${letters[idx]}</span>
        <span class="bb-option-text">${escapeHtml(optText)}</span>
      `;

      card.addEventListener("click", () => {
        if (prog.strikes[qObj.id] && prog.strikes[qObj.id].includes(idx)) {
          // Un-strike if user clicks directly on a struck option
          prog.strikes[qObj.id] = prog.strikes[qObj.id].filter((i) => i !== idx);
        }
        if (prog.answers[qObj.id] === idx) {
          // Allow deselecting on second click if desired
          delete prog.answers[qObj.id];
        } else {
          prog.answers[qObj.id] = idx;
        }
        prog.updatedAt = new Date().toISOString();
        saveActiveProgress();
        renderActiveQuestion();
      });

      row.appendChild(card);

      if (state.strikethroughMode) {
        const strikeBtn = document.createElement("button");
        strikeBtn.type = "button";
        strikeBtn.className = "bb-strike-btn";
        strikeBtn.title = isStruck ? `Restore Choice ${letters[idx]}` : `Cross out Choice ${letters[idx]}`;
        strikeBtn.textContent = isStruck ? "Undo" : letters[idx];
        strikeBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          toggleOptionStrike(qObj.id, idx);
        });
        row.appendChild(strikeBtn);
      }

      optionsContainer.appendChild(row);
    });

    // 4. Update Bottom Bar Navigation Buttons & Question Navigator Boxes
    const prevBtn = document.getElementById("bb-prev-btn");
    const nextBtn = document.getElementById("bb-next-btn");
    prevBtn.disabled = state.activeQuestionIdx === 0;
    nextBtn.textContent = state.activeQuestionIdx === totalInMod - 1 ? "Finish Module" : "Next";

    renderNavigatorBoxes(modQuestions, prog);
  }

  // Custom Line Highlighter Tool
  function renderHighlightablePassage(qObj, prog) {
    const container = document.getElementById("bb-passage-container");
    container.innerHTML = "";

    const qHighlights = prog.highlights[qObj.id] || {};
    const rawPassage = qObj.passage || "";

    // Split passage into paragraphs and interactive highlightable sentences/lines
    const paragraphs = rawPassage.split(/\n\s*\n|\n/);
    let lineCounter = 0;

    paragraphs.forEach((paraText) => {
      const trimmed = paraText.trim();
      if (!trimmed) return;

      const pEl = document.createElement("p");
      pEl.className = "bb-passage-paragraph";

      // Split into sentence/clause segments so each line/sentence can be clicked to highlight
      const segments = trimmed.match(/[^.!?]+[.!?]*\s*/g) || [trimmed];
      segments.forEach((seg) => {
        const currentLineIdx = lineCounter++;
        const span = document.createElement("span");
        const activeColor = qHighlights[currentLineIdx];
        span.className = `bb-passage-line${activeColor ? " hl-" + activeColor : ""}`;
        span.setAttribute("data-line-idx", String(currentLineIdx));
        span.textContent = seg + " ";

        span.addEventListener("click", () => {
          if (!state.highlighterEnabled) return;
          if (!prog.highlights[qObj.id]) prog.highlights[qObj.id] = {};
          if (prog.highlights[qObj.id][currentLineIdx] === state.highlightColor) {
            delete prog.highlights[qObj.id][currentLineIdx];
          } else {
            prog.highlights[qObj.id][currentLineIdx] = state.highlightColor;
          }
          saveActiveProgress();
          renderHighlightablePassage(qObj, prog);
        });

        pEl.appendChild(span);
      });

      container.appendChild(pEl);
    });
  }

  function toggleOptionStrike(qId, optIdx) {
    const prog = ensureTestProgress(state.activeTestNum);
    if (!prog.strikes[qId]) prog.strikes[qId] = [];
    if (prog.strikes[qId].includes(optIdx)) {
      prog.strikes[qId] = prog.strikes[qId].filter((i) => i !== optIdx);
    } else {
      prog.strikes[qId].push(optIdx);
      if (prog.answers[qId] === optIdx) {
        delete prog.answers[qId];
      }
    }
    saveActiveProgress();
    renderActiveQuestion();
  }

  function renderNavigatorBoxes(modQuestions, prog) {
    const inlineStrip = document.getElementById("bb-inline-q-strip");
    const popoverGrid = document.getElementById("bb-nav-popover-grid");
    if (!inlineStrip || !popoverGrid) return;

    inlineStrip.innerHTML = "";
    popoverGrid.innerHTML = "";

    modQuestions.forEach((qItem, idx) => {
      const isCurrent = idx === state.activeQuestionIdx;
      const isAnswered = typeof prog.answers[qItem.id] === "number";
      const isFlagged = Boolean(prog.flags[qItem.id]);

      const classes = [
        "bb-q-nav-box",
        isCurrent ? "active" : "",
        isAnswered ? "answered" : "unanswered",
        isFlagged ? "flagged" : "",
      ]
        .filter(Boolean)
        .join(" ");

      // Inline bottom strip button
      const boxBtn = document.createElement("button");
      boxBtn.type = "button";
      boxBtn.className = classes;
      boxBtn.textContent = `${idx + 1}`;
      boxBtn.title = `Question ${idx + 1} (${isAnswered ? "Answered" : "Unanswered"}${isFlagged ? ", Flagged" : ""})`;
      boxBtn.addEventListener("click", () => {
        state.activeQuestionIdx = idx;
        renderActiveQuestion();
      });
      inlineStrip.appendChild(boxBtn);

      // Popover grid button
      const popBtn = boxBtn.cloneNode(true);
      popBtn.addEventListener("click", () => {
        state.activeQuestionIdx = idx;
        document.getElementById("bb-navigator-popover").classList.add("hidden");
        renderActiveQuestion();
      });
      popoverGrid.appendChild(popBtn);
    });

    // Ensure active box is visible in horizontal strip
    const activeInline = inlineStrip.querySelector(".bb-q-nav-box.active");
    if (activeInline) {
      activeInline.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    }
  }

  function openModuleTransitionModal(autoTriggered = false) {
    const modal = document.getElementById("bb-transition-modal");
    const titleEl = document.getElementById("bb-modal-title");
    const descEl = document.getElementById("bb-modal-desc");
    const statsEl = document.getElementById("bb-modal-stats");
    const confirmBtn = document.getElementById("bb-modal-confirm-next-btn");

    const modDef = MODULE_DEFS[state.activeModuleIdx];
    const modQuestions = getActiveModuleQuestions();
    const prog = ensureTestProgress(state.activeTestNum);

    const answered = modQuestions.filter((q) => typeof prog.answers[q.id] === "number").length;
    const unanswered = modQuestions.length - answered;
    const flagged = modQuestions.filter((q) => prog.flags[q.id]).length;

    const isLastModule = state.activeModuleIdx === MODULE_DEFS.length - 1;

    titleEl.textContent = autoTriggered
      ? `Time Expired — ${modDef.label}`
      : `Finish ${modDef.label}?`;
    descEl.textContent = isLastModule
      ? "You have reached the end of the final module. Submitting now will compute your official 400–1600 scaled SAT scores and open the interactive explanation workspace."
      : `Your responses for ${modDef.shortLabel} have been flushed to localStorage. Ready to advance to ${MODULE_DEFS[state.activeModuleIdx + 1].label}?`;

    statsEl.innerHTML = `
      <div><strong style="font-size:20px;color:#15803d;">${answered}</strong><div style="font-size:12px;color:#475569;">Answered</div></div>
      <div><strong style="font-size:20px;color:#b45309;">${unanswered}</strong><div style="font-size:12px;color:#475569;">Unanswered</div></div>
      <div><strong style="font-size:20px;color:#dc2626;">${flagged}</strong><div style="font-size:12px;color:#475569;">Flagged</div></div>
    `;

    confirmBtn.textContent = isLastModule ? "Submit & View Official Score Report →" : `Continue to ${MODULE_DEFS[state.activeModuleIdx + 1].shortLabel} →`;
    modal.classList.remove("hidden");
  }

  // ==========================================================================
  // 5. OFFICIAL DESMOS GRAPHING CALCULATOR & DRAGGABLE FLOATING CONTAINERS
  // ==========================================================================

  function initDesmosCalculator() {
    const calcElem = document.getElementById("calculator");
    if (!calcElem) return;
    if (typeof window.Desmos !== "undefined" && window.Desmos.GraphingCalculator) {
      // Exact initialization specified in requirements
      calculator = Desmos.GraphingCalculator(document.getElementById("calculator"));
    } else {
      calcElem.innerHTML = `
        <div style="padding:24px;text-align:center;color:#475569;font-size:14px;">
          Desmos Graphing Calculator API is initializing (requires internet connection to load api/v1.12/calculator.js).
        </div>
      `;
    }
  }

  function initDraggableModals() {
    makeElementDraggable(
      document.getElementById("bb-calculator-modal"),
      document.getElementById("bb-calculator-drag-handle")
    );
    makeElementDraggable(
      document.getElementById("bb-reference-modal"),
      document.getElementById("bb-reference-drag-handle")
    );
  }

  function makeElementDraggable(modalEl, handleEl) {
    if (!modalEl || !handleEl) return;
    let isDragging = false;
    let startX = 0;
    let startY = 0;
    let origLeft = 0;
    let origTop = 0;

    handleEl.addEventListener("mousedown", (e) => {
      if (e.target.closest("button")) return;
      isDragging = true;
      const rect = modalEl.getBoundingClientRect();
      startX = e.clientX;
      startY = e.clientY;
      origLeft = rect.left;
      origTop = rect.top;
      document.body.style.userSelect = "none";
    });

    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      const newLeft = Math.max(8, Math.min(window.innerWidth - 120, origLeft + dx));
      const newTop = Math.max(8, Math.min(window.innerHeight - 80, origTop + dy));
      modalEl.style.left = `${newLeft}px`;
      modalEl.style.top = `${newTop}px`;
    });

    window.addEventListener("mouseup", () => {
      isDragging = false;
    });
  }

  function initSplitPaneResizer() {
    const divider = document.getElementById("bb-pane-divider");
    const workspace = document.getElementById("bb-workspace");
    if (!divider || !workspace) return;

    let isResizing = false;
    divider.addEventListener("mousedown", () => {
      isResizing = true;
    });

    window.addEventListener("mousemove", (e) => {
      if (!isResizing) return;
      const rect = workspace.getBoundingClientRect();
      const pct = ((e.clientX - rect.left) / rect.width) * 100;
      const clamped = Math.max(28, Math.min(72, pct));
      workspace.style.setProperty("--left-pane-width", `${clamped}%`);
    });

    window.addEventListener("mouseup", () => {
      isResizing = false;
    });
  }

  // ==========================================================================
  // 6. ADVANCED SCORING & EXPLANATION ENGINE (RAW-TO-SCALE 1600 CONVERSION)
  // ==========================================================================

  function computeExamScoreRecord(testNum, persistToHistory = true) {
    const questions = state.mocksData[String(testNum)] || [];
    const prog = ensureTestProgress(testNum);
    const answers = prog.answers || {};
    const table = state.scoringTables[String(testNum)] || { Reading: {}, Math: {} };

    let rwMod1Correct = 0;
    let rwMod2Correct = 0;
    let mathMod1Correct = 0;
    let mathMod2Correct = 0;

    let correctCount = 0;
    let incorrectCount = 0;
    let blankCount = 0;

    questions.forEach((q) => {
      const uAns = answers[q.id];
      if (typeof uAns !== "number") {
        blankCount++;
      } else if (uAns === q.correctAnswer) {
        correctCount++;
        if (q.type === "Reading" && q.module === 1) rwMod1Correct++;
        if (q.type === "Reading" && q.module === 2) rwMod2Correct++;
        if (q.type === "Math" && q.module === 1) mathMod1Correct++;
        if (q.type === "Math" && q.module === 2) mathMod2Correct++;
      } else {
        incorrectCount++;
      }
    });

    const rwRaw = rwMod1Correct + rwMod2Correct;     // 0..66
    const mathRaw = mathMod1Correct + mathMod2Correct; // 0..54

    const rwConv = (table.Reading && table.Reading[String(rwRaw)]) || fallbackScaleRW(rwRaw);
    const mathConv = (table.Math && table.Math[String(mathRaw)]) || fallbackScaleMath(mathRaw);

    const rwScore = rwConv.scaled;
    const mathScore = mathConv.scaled;
    const compositeScore = rwScore + mathScore;

    const record = {
      recordId: `sat-${testNum}-${Date.now()}`,
      testNumber: testNum,
      candidate: state.userName,
      timestamp: new Date().toISOString(),
      compositeScore,
      compositeLower: rwConv.lower + mathConv.lower,
      compositeUpper: rwConv.upper + mathConv.upper,
      rwScore,
      rwLower: rwConv.lower,
      rwUpper: rwConv.upper,
      rwRaw,
      rwMod1Correct,
      rwMod2Correct,
      mathScore,
      mathLower: mathConv.lower,
      mathUpper: mathConv.upper,
      mathRaw,
      mathMod1Correct,
      mathMod2Correct,
      correctCount,
      incorrectCount,
      blankCount,
      totalQuestions: questions.length,
      accuracyPct: questions.length > 0 ? Math.round((correctCount / questions.length) * 100) : 0,
      answersSnapshot: Object.assign({}, answers),
      flagsSnapshot: Object.assign({}, prog.flags || {}),
    };

    if (persistToHistory) {
      saveScoreRecord(record);
      // Reset active answers for this test so user can retake cleanly next time
      delete state.progressByTest[String(testNum)];
      saveActiveProgress();
      recordSessionLog(
        "EXAM_COMPLETED",
        `Completed SAT Practice Test #${testNum} — Composite: ${compositeScore}/1600 (R&W: ${rwScore}, Math: ${mathScore}).`
      );
    }

    return record;
  }

  function fallbackScaleRW(raw) {
    const scaled = Math.min(800, Math.max(200, Math.round((200 + (raw / 66) * 600) / 10) * 10));
    return { lower: Math.max(200, scaled - 10), upper: Math.min(800, scaled + 10), scaled };
  }

  function fallbackScaleMath(raw) {
    const scaled = Math.min(800, Math.max(200, Math.round((200 + (raw / 54) * 600) / 10) * 10));
    return { lower: Math.max(200, scaled - 10), upper: Math.min(800, scaled + 10), scaled };
  }

  function openReviewWorkspace(scoreRecord) {
    if (state.timerInterval) clearInterval(state.timerInterval);
    state.activeReviewRecord = scoreRecord;
    state.reviewModuleFilter = "all";

    const questions = state.mocksData[String(scoreRecord.testNumber)] || [];
    state.selectedReviewQuestionId = questions.length > 0 ? questions[0].id : 1;

    switchView("review-view");
    renderReviewReport();
  }

  function renderReviewReport() {
    const rec = state.activeReviewRecord;
    if (!rec) return;

    document.getElementById("rev-exam-title").textContent = `SAT Practice Test #${rec.testNumber} — Diagnostic Score Analysis`;
    document.getElementById("rev-completion-timestamp").textContent = `Candidate: ${rec.candidate} • ${formatShortTime(rec.timestamp)}`;

    document.getElementById("rev-total-score").textContent = `${rec.compositeScore}`;
    document.getElementById("rev-total-range").textContent = `Official Score Range: ${rec.compositeLower}–${rec.compositeUpper}`;

    document.getElementById("rev-rw-score").textContent = `${rec.rwScore}`;
    document.getElementById("rev-rw-raw-detail").textContent = `Raw Score: ${rec.rwRaw} / 66 (Mod 1: ${rec.rwMod1Correct}/33, Mod 2: ${rec.rwMod2Correct}/33)`;
    document.getElementById("rev-rw-range").textContent = `Official Section Range: ${rec.rwLower}–${rec.rwUpper}`;

    document.getElementById("rev-math-score").textContent = `${rec.mathScore}`;
    document.getElementById("rev-math-raw-detail").textContent = `Raw Score: ${rec.mathRaw} / 54 (Mod 1: ${rec.mathMod1Correct}/27, Mod 2: ${rec.mathMod2Correct}/27)`;
    document.getElementById("rev-math-range").textContent = `Official Section Range: ${rec.mathLower}–${rec.mathUpper}`;

    document.getElementById("rev-correct-count").textContent = `${rec.correctCount}`;
    document.getElementById("rev-incorrect-count").textContent = `${rec.incorrectCount}`;
    document.getElementById("rev-blank-count").textContent = `${rec.blankCount}`;
    document.getElementById("rev-accuracy-bar").style.width = `${rec.accuracyPct}%`;
    document.getElementById("rev-accuracy-text").textContent = `Overall Accuracy: ${rec.accuracyPct}% (${rec.correctCount} of ${rec.totalQuestions} items)`;

    renderReviewQuestionMatrix();
    renderReviewSelectedExplanation();
  }

  function getFilteredReviewQuestions() {
    const rec = state.activeReviewRecord;
    if (!rec) return [];
    const allQuestions = state.mocksData[String(rec.testNumber)] || [];
    if (state.reviewModuleFilter === "all") return allQuestions;
    const modDef = MODULE_DEFS[Number(state.reviewModuleFilter)];
    return allQuestions.filter((q) => q.type === modDef.type && Number(q.module) === modDef.module);
  }

  function renderReviewQuestionMatrix() {
    const rec = state.activeReviewRecord;
    const matrixEl = document.getElementById("rev-question-matrix");
    if (!rec || !matrixEl) return;

    const filtered = getFilteredReviewQuestions();
    const letters = ["A", "B", "C", "D"];
    matrixEl.innerHTML = "";

    filtered.forEach((q, idx) => {
      const uAns = rec.answersSnapshot[q.id];
      let status = "blank";
      if (typeof uAns === "number") {
        status = uAns === q.correctAnswer ? "correct" : "incorrect";
      }

      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = `matrix-cell ${status}${q.id === state.selectedReviewQuestionId ? " selected-cell" : ""}`;
      const displayNum = state.reviewModuleFilter === "all" ? q.id : idx + 1;
      const ansTag = typeof uAns === "number" ? letters[uAns] : "—";
      cell.innerHTML = `
        <span>${displayNum}</span>
        <span class="matrix-cell-sub">${ansTag}→${letters[q.correctAnswer]}</span>
      `;
      cell.title = `Item #${q.id} (${q.type} Mod ${q.module}) — Your Answer: ${ansTag}, Correct: ${letters[q.correctAnswer]}`;

      cell.addEventListener("click", () => {
        state.selectedReviewQuestionId = q.id;
        renderReviewQuestionMatrix();
        renderReviewSelectedExplanation();
      });

      matrixEl.appendChild(cell);
    });
  }

  function renderReviewSelectedExplanation() {
    const rec = state.activeReviewRecord;
    if (!rec) return;
    const allQuestions = state.mocksData[String(rec.testNumber)] || [];
    const qObj = allQuestions.find((q) => q.id === state.selectedReviewQuestionId) || allQuestions[0];
    if (!qObj) return;

    const letters = ["A", "B", "C", "D"];
    const uAns = rec.answersSnapshot[qObj.id];
    let outcome = "blank";
    let outcomeLabel = "Unanswered / Blank";
    if (typeof uAns === "number") {
      if (uAns === qObj.correctAnswer) {
        outcome = "correct";
        outcomeLabel = "Correct (+1 Raw Pt)";
      } else {
        outcome = "incorrect";
        outcomeLabel = "Incorrect (0 Pts)";
      }
    }

    document.getElementById("rev-detail-q-badge").textContent = `Item #${qObj.id}`;
    document.getElementById("rev-detail-mod-badge").textContent = `${qObj.type === "Reading" ? "Reading and Writing" : "Math"} • Module ${qObj.module}`;

    const outcomePill = document.getElementById("rev-detail-outcome-pill");
    outcomePill.className = `rev-outcome-pill ${outcome}`;
    outcomePill.textContent = outcomeLabel;

    document.getElementById("rev-detail-passage").textContent = qObj.passage;
    document.getElementById("rev-detail-question").textContent = qObj.question;

    const userAnsBox = document.getElementById("rev-user-ans-box");
    userAnsBox.className = `ans-comp-box user-ans-box ${outcome}`;
    document.getElementById("rev-detail-user-ans").textContent =
      typeof uAns === "number" ? `${letters[uAns]} (${qObj.options[uAns]})` : "Omitted / Blank";
    document.getElementById("rev-detail-correct-ans").textContent = `${letters[qObj.correctAnswer]} (${qObj.options[qObj.correctAnswer]})`;

    // Render all 4 options with visual verification badges
    const optsContainer = document.getElementById("rev-detail-options");
    optsContainer.innerHTML = "";
    qObj.options.forEach((optText, i) => {
      const isCorrect = i === qObj.correctAnswer;
      const isUserWrong = uAns === i && !isCorrect;
      const div = document.createElement("div");
      div.className = `rev-opt-item${isCorrect ? " is-correct" : ""}${isUserWrong ? " is-user-wrong" : ""}`;
      div.innerHTML = `
        <span class="rev-opt-badge">${letters[i]})</span>
        <span style="flex:1;">${escapeHtml(optText)}</span>
        ${isCorrect ? `<span style="color:#15803d;font-weight:800;font-size:12px;">✓ Correct Answer</span>` : ""}
        ${isUserWrong ? `<span style="color:#b91c1c;font-weight:800;font-size:12px;">✗ Your Choice</span>` : ""}
      `;
      optsContainer.appendChild(div);
    });

    // Format official explanation with clean paragraph splits per choice
    const expContainer = document.getElementById("rev-detail-explanation");
    const formattedExp = (qObj.explanation || "Explanation unavailable.")
      .replace(/\s+(?=Choice\s+[A-D]\s+is\s+)/g, "\n\n")
      .replace(/\s+(?=Alternate\s+approach:)/gi, "\n\n");

    expContainer.innerHTML = formattedExp
      .split("\n\n")
      .map((para) => `<p>${escapeHtml(para.trim())}</p>`)
      .join("");
  }

  // ==========================================================================
  // 7. EVENT LISTENERS & UI CONTROLLERS
  // ==========================================================================

  function bindEventListeners() {
    // Candidate Profile Edit
    document.getElementById("edit-profile-btn").addEventListener("click", () => {
      const nextName = window.prompt("Enter Candidate Full Name for Bluebook Session Logs:", state.userName);
      if (nextName && nextName.trim()) {
        state.userName = nextName.trim();
        localStorage.setItem(STORAGE_KEYS.USER_PROFILE, JSON.stringify({ userName: state.userName }));
        updateUserBadges();
        recordSessionLog("PROFILE_UPDATE", `Updated candidate profile name to ${state.userName}.`);
        renderDashboard();
      }
    });

    // Dashboard Filter Chips
    document.querySelectorAll(".filter-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        document.querySelectorAll(".filter-chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        state.currentFilter = chip.getAttribute("data-filter") || "all";
        renderDashboard();
      });
    });

    // Admin Telemetry Export & Clear
    document.getElementById("export-telemetry-btn").addEventListener("click", () => {
      const payload = {
        exportedAt: new Date().toISOString(),
        candidate: state.userName,
        scoreHistory: getScoreHistory(),
        sessionLogs: getSessionLogs(),
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `bluebook-telemetry-${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
    });

    document.getElementById("clear-telemetry-btn").addEventListener("click", () => {
      if (window.confirm("Reset all saved exam attempts, active progress, and session entrance logs in localStorage?")) {
        localStorage.removeItem(STORAGE_KEYS.SCORE_HISTORY);
        localStorage.removeItem(STORAGE_KEYS.SESSION_LOGS);
        localStorage.removeItem(STORAGE_KEYS.ACTIVE_PROGRESS);
        state.progressByTest = {};
        recordSessionLog("TELEMETRY_RESET", "Local administration records cleared by user.");
        renderDashboard();
      }
    });

    // Exam Topbar Controls
    document.getElementById("bb-timer-toggle-btn").addEventListener("click", () => {
      state.timerHidden = !state.timerHidden;
      updateTimerDOM();
    });

    document.getElementById("bb-directions-toggle").addEventListener("click", () => {
      document.getElementById("bb-directions-popover").classList.toggle("hidden");
    });

    document.getElementById("bb-directions-close").addEventListener("click", () => {
      document.getElementById("bb-directions-popover").classList.add("hidden");
    });

    document.getElementById("bb-module-selector").addEventListener("change", (e) => {
      const targetMod = Number(e.target.value);
      loadActiveModule(targetMod, 0);
    });

    // Calculator Trigger Button
    document.getElementById("bb-calculator-btn").addEventListener("click", () => {
      const modal = document.getElementById("bb-calculator-modal");
      const btn = document.getElementById("bb-calculator-btn");
      const isHidden = modal.classList.toggle("hidden");
      btn.classList.toggle("active", !isHidden);
      if (!isHidden && calculator && typeof calculator.resize === "function") {
        setTimeout(() => calculator.resize(), 40);
      }
    });

    document.getElementById("bb-calc-close-btn").addEventListener("click", () => {
      document.getElementById("bb-calculator-modal").classList.add("hidden");
      document.getElementById("bb-calculator-btn").classList.remove("active");
    });

    // Reference Sheet Trigger
    document.getElementById("bb-reference-btn").addEventListener("click", () => {
      const modal = document.getElementById("bb-reference-modal");
      const btn = document.getElementById("bb-reference-btn");
      const isHidden = modal.classList.toggle("hidden");
      btn.classList.toggle("active", !isHidden);
    });

    document.getElementById("bb-ref-close-btn").addEventListener("click", () => {
      document.getElementById("bb-reference-modal").classList.add("hidden");
      document.getElementById("bb-reference-btn").classList.remove("active");
    });

    // Highlighter Tool Controls
    document.getElementById("bb-highlighter-toggle-btn").addEventListener("click", () => {
      const bar = document.getElementById("bb-highlighter-bar");
      bar.classList.toggle("hidden");
    });

    document.querySelectorAll(".hl-color-swatch").forEach((sw) => {
      sw.addEventListener("click", () => {
        document.querySelectorAll(".hl-color-swatch").forEach((s) => s.classList.remove("active"));
        sw.classList.add("active");
        state.highlightColor = sw.getAttribute("data-color") || "yellow";
      });
    });

    document.getElementById("hl-clear-passage-btn").addEventListener("click", () => {
      const modQuestions = getActiveModuleQuestions();
      const qObj = modQuestions[state.activeQuestionIdx];
      if (!qObj) return;
      const prog = ensureTestProgress(state.activeTestNum);
      delete prog.highlights[qObj.id];
      saveActiveProgress();
      renderHighlightablePassage(qObj, prog);
    });

    // Flag for Review Button
    document.getElementById("bb-flag-review-btn").addEventListener("click", () => {
      const modQuestions = getActiveModuleQuestions();
      const qObj = modQuestions[state.activeQuestionIdx];
      if (!qObj) return;
      const prog = ensureTestProgress(state.activeTestNum);
      prog.flags[qObj.id] = !prog.flags[qObj.id];
      saveActiveProgress();
      renderActiveQuestion();
    });

    // Option Strikethrough Mode Toggle
    document.getElementById("bb-strikethrough-mode-btn").addEventListener("click", (e) => {
      state.strikethroughMode = !state.strikethroughMode;
      e.currentTarget.classList.toggle("active", state.strikethroughMode);
      renderActiveQuestion();
    });

    // Save & Exit to Dashboard
    document.getElementById("bb-save-exit-btn").addEventListener("click", () => {
      if (state.timerInterval) clearInterval(state.timerInterval);
      saveActiveProgress();
      recordSessionLog("EXAM_PAUSED", `Saved and exited SAT Practice Test #${state.activeTestNum}.`);
      switchView("dashboard-view");
      renderDashboard();
    });

    // Bottom Navigation: Previous / Next / Question Popover
    document.getElementById("bb-prev-btn").addEventListener("click", () => {
      if (state.activeQuestionIdx > 0) {
        state.activeQuestionIdx--;
        renderActiveQuestion();
      }
    });

    document.getElementById("bb-next-btn").addEventListener("click", () => {
      const modQuestions = getActiveModuleQuestions();
      if (state.activeQuestionIdx < modQuestions.length - 1) {
        state.activeQuestionIdx++;
        renderActiveQuestion();
      } else {
        openModuleTransitionModal(false);
      }
    });

    document.getElementById("bb-q-pill-trigger").addEventListener("click", () => {
      document.getElementById("bb-navigator-popover").classList.toggle("hidden");
    });

    document.getElementById("bb-nav-popover-close").addEventListener("click", () => {
      document.getElementById("bb-navigator-popover").classList.add("hidden");
    });

    document.getElementById("bb-finish-module-popover-btn").addEventListener("click", () => {
      document.getElementById("bb-navigator-popover").classList.add("hidden");
      openModuleTransitionModal(false);
    });

    document.getElementById("bb-score-exam-early-btn").addEventListener("click", () => {
      document.getElementById("bb-navigator-popover").classList.add("hidden");
      const rec = computeExamScoreRecord(state.activeTestNum, true);
      openReviewWorkspace(rec);
    });

    // Transition Modal Buttons
    document.getElementById("bb-modal-return-btn").addEventListener("click", () => {
      document.getElementById("bb-transition-modal").classList.add("hidden");
    });

    document.getElementById("bb-modal-score-now-btn").addEventListener("click", () => {
      document.getElementById("bb-transition-modal").classList.add("hidden");
      const rec = computeExamScoreRecord(state.activeTestNum, true);
      openReviewWorkspace(rec);
    });

    document.getElementById("bb-modal-confirm-next-btn").addEventListener("click", () => {
      document.getElementById("bb-transition-modal").classList.add("hidden");
      if (state.activeModuleIdx < MODULE_DEFS.length - 1) {
        recordSessionLog(
          "MODULE_COMPLETE",
          `Completed ${MODULE_DEFS[state.activeModuleIdx].shortLabel} on Test #${state.activeTestNum}; advancing to ${MODULE_DEFS[state.activeModuleIdx + 1].shortLabel}.`
        );
        loadActiveModule(state.activeModuleIdx + 1, 0);
      } else {
        const rec = computeExamScoreRecord(state.activeTestNum, true);
        openReviewWorkspace(rec);
      }
    });

    // Strict Simulation Lockdown on Exam Screen (Disable copy, cut, right-click)
    const examView = document.getElementById("exam-view");
    examView.addEventListener("contextmenu", (e) => e.preventDefault());
    examView.addEventListener("copy", (e) => e.preventDefault());
    examView.addEventListener("cut", (e) => e.preventDefault());

    // Review Page Controls
    document.getElementById("rev-back-dashboard-btn").addEventListener("click", () => {
      switchView("dashboard-view");
      renderDashboard();
    });

    document.getElementById("rev-retake-exam-btn").addEventListener("click", () => {
      if (!state.activeReviewRecord) return;
      const tNum = state.activeReviewRecord.testNumber;
      delete state.progressByTest[String(tNum)];
      saveActiveProgress();
      launchExamClient(tNum);
    });

    document.querySelectorAll("#rev-module-tabs .rev-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll("#rev-module-tabs .rev-tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        state.reviewModuleFilter = tab.getAttribute("data-mod") || "all";
        const filtered = getFilteredReviewQuestions();
        if (filtered.length > 0) {
          state.selectedReviewQuestionId = filtered[0].id;
        }
        renderReviewQuestionMatrix();
        renderReviewSelectedExplanation();
      });
    });

    document.getElementById("rev-prev-item-btn").addEventListener("click", () => {
      const filtered = getFilteredReviewQuestions();
      const idx = filtered.findIndex((q) => q.id === state.selectedReviewQuestionId);
      if (idx > 0) {
        state.selectedReviewQuestionId = filtered[idx - 1].id;
        renderReviewQuestionMatrix();
        renderReviewSelectedExplanation();
      }
    });

    document.getElementById("rev-next-item-btn").addEventListener("click", () => {
      const filtered = getFilteredReviewQuestions();
      const idx = filtered.findIndex((q) => q.id === state.selectedReviewQuestionId);
      if (idx >= 0 && idx < filtered.length - 1) {
        state.selectedReviewQuestionId = filtered[idx + 1].id;
        renderReviewQuestionMatrix();
        renderReviewSelectedExplanation();
      }
    });
  }

  // ==========================================================================
  // 8. UTILITY HELPERS
  // ==========================================================================

  function switchView(viewId) {
    document.querySelectorAll(".view-container").forEach((v) => v.classList.remove("active-view"));
    const target = document.getElementById(viewId);
    if (target) target.classList.add("active-view");
    window.scrollTo(0, 0);
  }

  function formatShortTime(isoStr) {
    try {
      const d = new Date(isoStr);
      return d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (_) {
      return isoStr;
    }
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Boot Platform on DOMContentLoaded
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initApp);
  } else {
    initApp();
  }
})();

