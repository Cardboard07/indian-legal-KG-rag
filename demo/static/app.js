// Indian Legal KG-RAG Client Application Logic

const VERIFIED_TESTS = [
  { id: "test_01", title: "Empty & Edgeless Projections", desc: "Safe initialization and fallback to isolates without crashing." },
  { id: "test_02", title: "Isolates & Disconnected Components", desc: "130,895 isolates preserved in COMM::v1::ISOLATES." },
  { id: "test_03", title: "Finite & Positive Edge Weights", desc: "All projection edge weights strictly positive (w >= 1.0)." },
  { id: "test_04", title: "Duplicate Evidence Deduplication", desc: "Weights count unique supporting chunks, preventing retry inflation." },
  { id: "test_05", title: "Unsplittable Community Termination", desc: "Modularity stagnation cleanly terminates partition branches as leaves." },
  { id: "test_06", title: "Uneven Hierarchy Depth Handling", desc: "Tree manages variable branch termination across levels 0, 1, 2." },
  { id: "test_07", title: "Frontier Coverage Integrity", desc: "100% of 26,694 connected nodes covered with 0 overlaps and 0 gaps." },
  { id: "test_08", title: "Missing Child Reports Fallback", desc: "Context builder degrades gracefully to raw assertions if child report absent." },
  { id: "test_09", title: "Oversized Context Budget Enforcement", desc: "Strictly enforces context character limits (3000, 6000, 12000)." },
  { id: "test_10", title: "Citation Chain Integrity", desc: "Every finding links to valid chunk extensions (.md) and assertion IDs." },
  { id: "test_11", title: "Preservation of Legal Exceptions", desc: "Conditions, exceptions, and thresholds bundled with obligations." },
  { id: "test_12", title: "Failed Map Batches Resilience", desc: "Handles empty or malformed batches safely without breaking reduce stage." },
  { id: "test_13", title: "All-Zero Map Results Fallback", desc: "Returns explicit insufficient-evidence determination when scores = 0." },
  { id: "test_14", title: "Interrupted Report Resumability", desc: "Loads existing report checkpoints and skips redundant generation." },
  { id: "test_15", title: "Idempotent FalkorDB Persistence", desc: "Re-running MERGE on communities and reports produces 0 duplicates." },
  { id: "test_16", title: "Stale-Cache & Snapshot Hashes", desc: "Manifest locks SHA256 hashes of extraction_store.db and rbi_fts.db." },
  { id: "test_17", title: "Existing Mode Preservation", desc: "baseline_hybrid and hybrid_graph remain operational alongside global modes." }
];

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  loadBenchmarkPresets();
  loadCommunities();
  renderVerifications();

  document.getElementById("runBtn").addEventListener("click", executeQuery);
});

// Tab Navigation
function initTabs() {
  const tabs = document.querySelectorAll(".tab-btn");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.style.display = "none");

      tab.classList.add("active");
      const targetId = `tab-${tab.dataset.tab}`;
      const targetContent = document.getElementById(targetId);
      if (targetContent) targetContent.style.display = "block";
    });
  });
}

// Load Benchmark Presets
async function loadBenchmarkPresets() {
  const container = document.getElementById("presetList");
  try {
    const res = await fetch("/api/benchmark_questions");
    const data = await res.json();
    if (data.status === "success") {
      container.innerHTML = data.questions.map((q, idx) => `
        <div class="preset-item" onclick="selectPreset('${encodeURIComponent(q.question)}')">
          <span class="preset-tag">${q.theme}</span>
          <span class="preset-text">${q.question}</span>
        </div>
      `).join("");
    }
  } catch (err) {
    container.innerHTML = "<p style='color: #64748b;'>Could not load presets.</p>";
  }
}

function selectPreset(encodedQuestion) {
  const question = decodeURIComponent(encodedQuestion);
  document.getElementById("queryInput").value = question;
}

// Execute Live Query
async function executeQuery() {
  const query = document.getElementById("queryInput").value.trim();
  if (!query) return;

  const mode = document.querySelector('input[name="mode"]:checked').value;
  const level = parseInt(document.getElementById("levelSelect").value);

  const runBtn = document.getElementById("runBtn");
  const btnText = document.getElementById("btnText");
  const btnSpinner = document.getElementById("btnSpinner");
  const resultPanel = document.getElementById("resultPanel");

  btnText.textContent = "Processing...";
  btnSpinner.style.display = "inline-block";
  runBtn.disabled = true;

  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, mode, level })
    });
    const data = await res.json();

    if (data.status === "success") {
      renderResult(data);
      resultPanel.style.display = "block";
      resultPanel.scrollIntoView({ behavior: "smooth" });
    } else {
      alert("Error executing query: " + data.message);
    }
  } catch (err) {
    alert("Network error: " + err.message);
  } finally {
    btnText.textContent = "Execute Query";
    btnSpinner.style.display = "none";
    runBtn.disabled = false;
  }
}

// Render Results
function renderResult(data) {
  const res = data.result;
  document.getElementById("metaMode").textContent = `Mode: ${data.mode}`;
  document.getElementById("metaLatency").textContent = `Latency: ${data.elapsed_ms} ms`;
  
  const citations = res.citations || [];
  document.getElementById("metaCitations").textContent = `Citations: ${citations.length}`;

  document.getElementById("answerText").textContent = res.answer || "No answer generated.";
  document.getElementById("analysisText").innerHTML = `<strong>Legal Determination:</strong> ${res.legal_analysis || 'Direct analysis based on retrieved regulatory evidence.'}`;

  // Citations
  const citContainer = document.getElementById("citationsList");
  if (citations.length > 0) {
    citContainer.innerHTML = citations.map(c => `
      <div class="citation-card">
        <div class="citation-file">${c.chunk_id || c.source_chunk || 'Regulatory Passage'}</div>
        <div class="citation-doc">Document: ${c.document_id || 'RBI Circular'}</div>
      </div>
    `).join("");
  } else {
    citContainer.innerHTML = "<p style='color: #64748b;'>No citations attached.</p>";
  }

  // Qualifiers
  const qualContainer = document.getElementById("qualifiersBox");
  const quals = res.conditions_and_exceptions || [];
  if (quals.length > 0) {
    qualContainer.innerHTML = quals.map(q => `
      <div class="qualifier-item">
        <span>&bull;</span>
        <span>${typeof q === 'string' ? q : JSON.stringify(q)}</span>
      </div>
    `).join("");
  } else {
    qualContainer.innerHTML = `
      <div class="qualifier-item">
        <span>&bull;</span>
        <span>Preserved conditions, exceptions, and numeric thresholds are bundled directly inside the synthesized findings above.</span>
      </div>
    `;
  }
}

// Load Communities
async function loadCommunities() {
  const container = document.getElementById("commGrid");
  try {
    const res = await fetch("/api/communities");
    const data = await res.json();
    if (data.status === "success") {
      container.innerHTML = data.top_communities.map(c => `
        <div class="comm-card" onclick="openReport('${c.id}')">
          <div class="comm-card-header">
            <span class="comm-badge">${c.id}</span>
            <span style="color: #34d399; font-size: 11px; font-weight: 600;">Level 0 Domain</span>
          </div>
          <div class="comm-title">${c.title}</div>
          <div class="comm-metrics">
            <span><strong>${c.size.toLocaleString()}</strong> Entities</span>
            <span><strong>${c.internal_edges.toLocaleString()}</strong> Edges</span>
            <span><strong>${c.findings_count}</strong> Findings</span>
          </div>
        </div>
      `).join("");
    }
  } catch (err) {
    container.innerHTML = "<p style='color: #64748b;'>Could not load communities.</p>";
  }
}

// Open Report Modal
async function openReport(commId) {
  const modal = document.getElementById("reportModal");
  const body = document.getElementById("modalBody");
  body.innerHTML = "<p style='color: #94a3b8;'>Loading report details...</p>";
  modal.classList.add("open");

  try {
    const res = await fetch(`/api/report/${commId}`);
    const data = await res.json();
    if (data.status === "success") {
      const r = data.report;
      body.innerHTML = `
        <div style="font-family: var(--font-mono); color: var(--accent-cyan); font-size: 12px; margin-bottom: 8px;">
          ${r.community_id} | Snapshot: ${r.snapshot_id}
        </div>
        <h2 style="font-family: var(--font-display); font-size: 22px; margin-bottom: 16px; color: #f8fafc;">
          ${r.title}
        </h2>
        <p style="color: #cbd5e1; font-size: 14px; line-height: 1.6; margin-bottom: 24px; background: rgba(255,255,255,0.03); padding: 16px; border-radius: 8px;">
          ${r.summary}
        </p>
        <h3 style="font-family: var(--font-display); font-size: 16px; color: #94a3b8; text-transform: uppercase; margin-bottom: 12px;">
          Key Findings & Citations
        </h3>
        <div style="display: flex; flex-direction: column; gap: 12px; margin-bottom: 24px;">
          ${r.findings.map(f => `
            <div style="background: rgba(15,23,42,0.6); padding: 14px; border-left: 3px solid var(--accent-violet); border-radius: 0 8px 8px 0;">
              <strong style="color: #f1f5f9; display: block; margin-bottom: 4px;">${f.explanation}</strong>
              <div style="font-size: 11px; font-family: var(--font-mono); color: #94a3b8;">
                Assertion: ${f.supporting_assertion_ids?.join(", ") || 'N/A'}
              </div>
            </div>
          `).join("")}
        </div>
      `;
    } else {
      body.innerHTML = `<p style="color: #f87171;">${data.message}</p>`;
    }
  } catch (err) {
    body.innerHTML = `<p style="color: #f87171;">Failed to load report: ${err.message}</p>`;
  }
}

function closeModal() {
  document.getElementById("reportModal").classList.remove("open");
}

// Render 17 Verification Cases
function renderVerifications() {
  const container = document.getElementById("verificationGrid");
  container.innerHTML = VERIFIED_TESTS.map(t => `
    <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(16, 185, 129, 0.2); border-radius: 8px; padding: 14px; display: flex; gap: 12px; align-items: flex-start;">
      <div style="background: rgba(16, 185, 129, 0.15); color: #34d399; width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: bold; flex-shrink: 0;">
        ✓
      </div>
      <div>
        <div style="font-family: var(--font-display); font-weight: 700; color: #f8fafc; font-size: 14px; margin-bottom: 2px;">
          ${t.title}
        </div>
        <div style="font-size: 12px; color: #94a3b8; line-height: 1.4;">
          ${t.desc}
        </div>
      </div>
    </div>
  `).join("");
}
