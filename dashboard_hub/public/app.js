const state = {
  session: null,
  app: null,
  currentPage: localStorage.getItem("dashboard.page") || "overview",
  region: localStorage.getItem("dashboard.region") || "india",
  company: localStorage.getItem("dashboard.company") || "all",
  period: localStorage.getItem("dashboard.period") || "this_month",
  dateFrom: localStorage.getItem("dashboard.dateFrom") || "",
  dateTo: localStorage.getItem("dashboard.dateTo") || "",
};

const loginShell = document.getElementById("login-shell");
const appShell = document.getElementById("app-shell");
const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("login-error");
const pageNav = document.getElementById("page-nav");
const regionSelect = document.getElementById("region-select");
const companySelect = document.getElementById("company-select");
const periodSelect = document.getElementById("period-select");
const dateFromInput = document.getElementById("date-from");
const dateToInput = document.getElementById("date-to");
const pageTitle = document.getElementById("page-title");
const pageSubtitle = document.getElementById("page-subtitle");
const sessionUser = document.getElementById("session-user");
const statusBanner = document.getElementById("status-banner");
const kpiGrid = document.getElementById("kpi-grid");
const chartGrid = document.getElementById("chart-grid");
const tableGrid = document.getElementById("table-grid");
const refreshBtn = document.getElementById("refresh-btn");
const logoutBtn = document.getElementById("logout-btn");

function showStatus(message, isError = false) {
  statusBanner.textContent = message;
  statusBanner.classList.remove("hidden", "error", "success");
  statusBanner.classList.add(isError ? "error" : "success");
}

function hideStatus() {
  statusBanner.classList.add("hidden");
}

function formatValue(kpi) {
  if (kpi.format === "currency") {
    return `${kpi.currency_symbol || ""}${Number(kpi.value || 0).toLocaleString()}`;
  }
  if (kpi.format === "decimal") {
    return Number(kpi.value || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return Number(kpi.value || 0).toLocaleString();
}

function setPeriodVisibility() {
  const custom = periodSelect.value === "custom";
  document.getElementById("date-from-wrap").classList.toggle("hidden", !custom);
  document.getElementById("date-to-wrap").classList.toggle("hidden", !custom);
}

function regionEntry() {
  return (state.app?.regions || []).find((entry) => entry.key === state.region);
}

function persistState() {
  localStorage.setItem("dashboard.page", state.currentPage);
  localStorage.setItem("dashboard.region", state.region);
  localStorage.setItem("dashboard.company", state.company);
  localStorage.setItem("dashboard.period", state.period);
  localStorage.setItem("dashboard.dateFrom", state.dateFrom);
  localStorage.setItem("dashboard.dateTo", state.dateTo);
}

function buildPageNav() {
  pageNav.innerHTML = "";
  (state.app?.pages || []).forEach((page) => {
    const button = document.createElement("button");
    button.className = `nav-item ${page.key === state.currentPage ? "active" : ""}`;
    button.textContent = page.label;
    button.addEventListener("click", () => {
      state.currentPage = page.key;
      persistState();
      buildPageNav();
      loadDashboard();
    });
    pageNav.appendChild(button);
  });
}

function populateFilters() {
  regionSelect.innerHTML = "";
  (state.app?.regions || []).forEach((entry) => {
    const option = document.createElement("option");
    option.value = entry.key;
    option.textContent = entry.label;
    regionSelect.appendChild(option);
  });
  if (!(state.app?.regions || []).some((entry) => entry.key === state.region)) {
    state.region = state.app?.regions?.[0]?.key || "india";
  }
  regionSelect.value = state.region;

  companySelect.innerHTML = "";
  const currentRegion = regionEntry();
  if (state.region === "india") {
    const allOption = document.createElement("option");
    allOption.value = "all";
    allOption.textContent = "All India";
    companySelect.appendChild(allOption);
  }
  (currentRegion?.companies || []).forEach((company) => {
    const option = document.createElement("option");
    option.value = company.key;
    option.textContent = company.label;
    companySelect.appendChild(option);
  });
  const validValues = Array.from(companySelect.options).map((option) => option.value);
  if (!validValues.includes(state.company)) {
    state.company = state.region === "india" ? "all" : (currentRegion?.companies?.[0]?.key || "");
  }
  companySelect.value = state.company;

  periodSelect.value = state.period;
  dateFromInput.value = state.dateFrom;
  dateToInput.value = state.dateTo;
  setPeriodVisibility();
}

function renderKpis(kpis) {
  kpiGrid.innerHTML = "";
  (kpis || []).forEach((kpi) => {
    const card = document.createElement("article");
    card.className = "card kpi-card";
    card.innerHTML = `<p class="kpi-label">${kpi.label}</p><p class="kpi-value">${formatValue(kpi)}${kpi.suffix || ""}</p>`;
    kpiGrid.appendChild(card);
  });
}

function renderCharts(charts) {
  chartGrid.innerHTML = "";
  (charts || []).forEach((chart) => {
    const panel = document.createElement("article");
    panel.className = "card panel";
    const max = Math.max(...(chart.items || []).map((item) => Number(item.value || 0)), 1);
    panel.innerHTML = `
      <div class="panel-head">
        <h3>${chart.title}</h3>
      </div>
      <div class="bar-list">
        ${(chart.items || [])
          .slice(0, 12)
          .map(
            (item) => `
            <div class="bar-row">
              <span class="bar-label">${item.label}</span>
              <div class="bar-track"><div class="bar-fill" style="width:${(Number(item.value || 0) / max) * 100}%"></div></div>
              <span class="bar-value">${Number(item.value || 0).toLocaleString()}</span>
            </div>`,
          )
          .join("")}
      </div>
    `;
    chartGrid.appendChild(panel);
  });
}

function renderTables(tables) {
  tableGrid.innerHTML = "";
  tableGrid.classList.toggle("hidden", !(tables || []).length);
  (tables || []).forEach((table) => {
    const panel = document.createElement("article");
    panel.className = "card panel table-panel";
    const headers = (table.columns || []).map((column) => `<th>${column.label}</th>`).join("");
    const rows = (table.rows || [])
      .map((row) => {
        return `<tr>${(table.columns || [])
          .map((column) => {
            if (column.key === "record_url") {
              return `<td>${row.record_url ? `<a href="${row.record_url}" target="_blank" rel="noreferrer">Open</a>` : ""}</td>`;
            }
            return `<td>${row[column.key] ?? ""}</td>`;
          })
          .join("")}</tr>`;
      })
      .join("");
    panel.innerHTML = `
      <div class="panel-head">
        <h3>${table.title}</h3>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr>${headers}</tr></thead>
          <tbody>${rows || `<tr><td colspan="${table.columns.length}">No data</td></tr>`}</tbody>
        </table>
      </div>
    `;
    tableGrid.appendChild(panel);
  });
}

async function loadDashboard(refresh = false) {
  hideStatus();
  pageTitle.textContent = state.currentPage.charAt(0).toUpperCase() + state.currentPage.slice(1);
  pageSubtitle.textContent = `Region: ${state.region.toUpperCase()} | Company: ${companySelect.options[companySelect.selectedIndex]?.text || state.company}`;
  const params = new URLSearchParams({
    region: state.region,
    company: state.company,
    period: state.period,
  });
  if (state.period === "custom") {
    if (state.dateFrom) params.set("date_from", state.dateFrom);
    if (state.dateTo) params.set("date_to", state.dateTo);
  }
  if (refresh) {
    params.set("refresh", "1");
  }
  showStatus("Loading dashboard...");
  const response = await fetch(`/api/dashboard/${state.currentPage}?${params.toString()}`);
  const data = await response.json();
  if (!response.ok || !data.ok) {
    showStatus(data.error || "Unable to load dashboard data.", true);
    return;
  }
  hideStatus();
  renderKpis(data.kpis);
  renderCharts(data.charts);
  renderTables(data.tables);
  pageTitle.textContent = data.title || pageTitle.textContent;
  pageSubtitle.textContent = `${data.requestedScope.companyLabel} | ${data.scope.date_from || "Beginning"} to ${data.scope.date_to || "Today"} | ${data.scope.source_name}`;
}

async function initializeSession() {
  const response = await fetch("/api/session");
  const data = await response.json();
  if (!response.ok || !data.ok) {
    loginShell.classList.remove("hidden");
    appShell.classList.add("hidden");
    return;
  }
  state.session = data.session;
  state.app = data.app;
  sessionUser.textContent = `Signed in as ${state.session.username}`;
  loginShell.classList.add("hidden");
  appShell.classList.remove("hidden");
  buildPageNav();
  populateFilters();
  await loadDashboard();
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.classList.add("hidden");
  const formData = new FormData(loginForm);
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: formData.get("username"),
      password: formData.get("password"),
    }),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    loginError.textContent = data.error || "Login failed.";
    loginError.classList.remove("hidden");
    return;
  }
  loginForm.reset();
  await initializeSession();
});

regionSelect.addEventListener("change", async () => {
  state.region = regionSelect.value;
  state.company = state.region === "india" ? "all" : (regionEntry()?.companies?.[0]?.key || "");
  persistState();
  populateFilters();
  await loadDashboard();
});

companySelect.addEventListener("change", async () => {
  state.company = companySelect.value;
  persistState();
  await loadDashboard();
});

periodSelect.addEventListener("change", async () => {
  state.period = periodSelect.value;
  setPeriodVisibility();
  persistState();
  await loadDashboard();
});

dateFromInput.addEventListener("change", async () => {
  state.dateFrom = dateFromInput.value;
  persistState();
  if (state.period === "custom") {
    await loadDashboard();
  }
});

dateToInput.addEventListener("change", async () => {
  state.dateTo = dateToInput.value;
  persistState();
  if (state.period === "custom") {
    await loadDashboard();
  }
});

refreshBtn.addEventListener("click", async () => {
  await loadDashboard(true);
});

logoutBtn.addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  window.location.reload();
});

initializeSession();
