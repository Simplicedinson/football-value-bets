(function () {
    const dashboard = document.getElementById("live-dashboard");

    if (!dashboard) {
        return;
    }

    const liveApiUrl = dashboard.dataset.liveApi;
    const refreshIntervalMs = Number(dashboard.dataset.refreshIntervalMs || "60000");
    const countBadge = document.getElementById("live-count-badge");
    const syncStatus = document.getElementById("live-sync-status");
    const searchInput = document.getElementById("live-search-input");
    const statusFilter = document.getElementById("live-status-filter");
    const leagueFilter = document.getElementById("live-league-filter");
    const sortOrderSelect = document.getElementById("live-sort-order");
    const filterSummary = document.getElementById("live-filter-summary");
    const initialMatchesNode = document.getElementById("live-initial-matches");

    let refreshTimerId = null;
    let isFetching = false;
    let allMatches = [];

    const filters = {
        query: "",
        status: "all",
        league: "all",
        sort: "minute_desc",
    };

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function formatOdd(value) {
        if (value === null || value === undefined || value === "") {
            return "-";
        }

        const numericValue = Number(value);

        if (Number.isNaN(numericValue)) {
            return "-";
        }

        return numericValue.toFixed(2);
    }

    function formatSyncDate(value) {
        if (!value) {
            return "Waiting for first live sync";
        }

        const parsedDate = new Date(value);

        if (Number.isNaN(parsedDate.getTime())) {
            return `Last sync: ${value}`;
        }

        return `Last sync: ${parsedDate.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        })}`;
    }

    function normalizeText(value) {
        return String(value ?? "").trim().toLowerCase();
    }

    function parseLiveMinute(value, matchStatus) {
        if (matchStatus === "halftime") {
            return 45;
        }

        const match = String(value ?? "").match(/(\d{1,3})/);

        if (!match) {
            return -1;
        }

        return Number(match[1]);
    }

    function renderMatchCard(match) {
        const statusClass = match.match_status === "halftime" ? "is-halftime" : "is-live";
        const statusBadge = match.match_status === "halftime"
            ? '<span class="live-status-pill halftime">HT</span>'
            : `<span class="live-status-pill">${escapeHtml(match.live_minute || "LIVE")}</span>`;
        const statusText = match.match_status === "halftime" ? "Half-time break" : "In play now";

        return `
            <article class="live-match-card ${statusClass}">
                <div class="live-card-top">
                    <div class="live-status-stack">
                        ${statusBadge}
                        <span class="live-status-text">${escapeHtml(statusText)}</span>
                    </div>

                    <div class="live-league-badge">
                        ${escapeHtml(match.league_name)}
                    </div>
                </div>

                <div class="live-scoreboard">
                    <div class="live-team-panel home">
                        <span class="live-team-label">Home</span>
                        <h2 class="live-team-name">${escapeHtml(match.home_team)}</h2>
                    </div>

                    <div class="live-score-core">
                        <div class="live-score-shell">
                            <span>${escapeHtml(match.home_score ?? "-")}</span>
                            <span class="live-score-divider">:</span>
                            <span>${escapeHtml(match.away_score ?? "-")}</span>
                        </div>
                        <span class="live-score-caption">Live scoreboard</span>
                    </div>

                    <div class="live-team-panel away">
                        <span class="live-team-label">Away</span>
                        <h2 class="live-team-name">${escapeHtml(match.away_team)}</h2>
                    </div>
                </div>

                <div class="live-odds-strip">
                    <div class="live-odd-card">
                        <span class="live-odd-label">Home</span>
                        <strong class="live-odd-value">${formatOdd(match.home_win)}</strong>
                    </div>

                    <div class="live-odd-card draw">
                        <span class="live-odd-label">Draw</span>
                        <strong class="live-odd-value">${formatOdd(match.draw)}</strong>
                    </div>

                    <div class="live-odd-card">
                        <span class="live-odd-label">Away</span>
                        <strong class="live-odd-value">${formatOdd(match.away_win)}</strong>
                    </div>
                </div>

                <div class="live-card-footer">
                    <p class="live-card-note">Live 1X2 market snapshot</p>
                    <a class="view-link live-view-link" href="/matches/${encodeURIComponent(match.fixture_id)}">Open Match</a>
                </div>
            </article>
        `;
    }

    function renderEmptyState(message) {
        return `
            <section class="card live-empty-card" id="live-empty-state">
                <div class="empty-state">
                    <strong>${escapeHtml(message)}</strong>
                    <p>Adjust the filters or wait for the next live refresh.</p>
                </div>
            </section>
        `;
    }

    function updateLeagueOptions(matches) {
        if (!leagueFilter) {
            return;
        }

        const currentValue = leagueFilter.value || "all";
        const leagues = [...new Set(
            matches
                .map((match) => String(match.league_name || "").trim())
                .filter(Boolean)
        )].sort((a, b) => a.localeCompare(b));

        const options = [
            '<option value="all">All leagues</option>',
            ...leagues.map(
                (league) => `<option value="${escapeHtml(league)}">${escapeHtml(league)}</option>`
            ),
        ];

        leagueFilter.innerHTML = options.join("");

        if (leagues.includes(currentValue)) {
            leagueFilter.value = currentValue;
        } else {
            leagueFilter.value = "all";
            filters.league = "all";
        }
    }

    function applyFilters(matches) {
        const normalizedQuery = normalizeText(filters.query);
        const filteredMatches = matches.filter((match) => {
            const matchesStatus = filters.status === "all" || match.match_status === filters.status;
            const matchesLeague = filters.league === "all" || match.league_name === filters.league;

            if (!matchesStatus || !matchesLeague) {
                return false;
            }

            if (!normalizedQuery) {
                return true;
            }

            const haystack = [
                match.home_team,
                match.away_team,
                match.league_name,
            ]
                .map(normalizeText)
                .join(" ");

            return haystack.includes(normalizedQuery);
        });

        filteredMatches.sort((leftMatch, rightMatch) => {
            if (filters.sort === "league_asc") {
                return String(leftMatch.league_name || "").localeCompare(
                    String(rightMatch.league_name || "")
                );
            }

            if (filters.sort === "league_desc") {
                return String(rightMatch.league_name || "").localeCompare(
                    String(leftMatch.league_name || "")
                );
            }

            const leftMinute = parseLiveMinute(leftMatch.live_minute, leftMatch.match_status);
            const rightMinute = parseLiveMinute(rightMatch.live_minute, rightMatch.match_status);

            if (filters.sort === "minute_asc") {
                return leftMinute - rightMinute;
            }

            return rightMinute - leftMinute;
        });

        return filteredMatches;
    }

    function updateCounters(visibleMatches, totalMatches) {
        if (countBadge) {
            countBadge.textContent = `${visibleMatches.length} live`;
        }

        if (filterSummary) {
            filterSummary.textContent = `${visibleMatches.length} / ${totalMatches} matches`;
        }
    }

    function renderCurrentView() {
        const visibleMatches = applyFilters(allMatches);

        updateCounters(visibleMatches, allMatches.length);

        if (allMatches.length === 0) {
            dashboard.innerHTML = renderEmptyState("No live matches found.");
            return;
        }

        if (visibleMatches.length === 0) {
            dashboard.innerHTML = renderEmptyState("No matches match your filters.");
            return;
        }

        const cardsHtml = visibleMatches.map(renderMatchCard).join("");
        dashboard.innerHTML = `<div class="live-grid" id="live-grid">${cardsHtml}</div>`;
    }

    function updateLiveDom(payload) {
        const matches = Array.isArray(payload.matches) ? payload.matches : [];
        const refresh = payload.refresh || {};

        allMatches = matches;
        updateLeagueOptions(allMatches);
        renderCurrentView();

        if (syncStatus) {
            if (refresh.last_error) {
                syncStatus.textContent = `Refresh error: ${refresh.last_error}`;
            } else {
                syncStatus.textContent = formatSyncDate(refresh.last_completed_at);
            }
        }

        dashboard.classList.toggle("is-refreshing", Boolean(refresh.is_refreshing));
    }

    async function refreshLiveMatches() {
        if (isFetching || !liveApiUrl) {
            return;
        }

        isFetching = true;

        try {
            const response = await fetch(liveApiUrl, {
                method: "GET",
                headers: {
                    Accept: "application/json",
                },
                cache: "no-store",
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const payload = await response.json();
            updateLiveDom(payload);
        } catch (error) {
            if (syncStatus) {
                syncStatus.textContent = `Refresh error: ${error}`;
            }
        } finally {
            isFetching = false;
        }
    }

    function scheduleRefresh() {
        refreshTimerId = window.setTimeout(async () => {
            await refreshLiveMatches();
            scheduleRefresh();
        }, refreshIntervalMs);
    }

    function bindControls() {
        if (searchInput) {
            searchInput.addEventListener("input", (event) => {
                filters.query = event.target.value || "";
                renderCurrentView();
            });
        }

        if (statusFilter) {
            statusFilter.addEventListener("change", (event) => {
                filters.status = event.target.value || "all";
                renderCurrentView();
            });
        }

        if (leagueFilter) {
            leagueFilter.addEventListener("change", (event) => {
                filters.league = event.target.value || "all";
                renderCurrentView();
            });
        }

        if (sortOrderSelect) {
            sortOrderSelect.addEventListener("change", (event) => {
                filters.sort = event.target.value || "minute_desc";
                renderCurrentView();
            });
        }
    }

    function readInitialMatches() {
        if (!initialMatchesNode) {
            return [];
        }

        try {
            const payload = JSON.parse(initialMatchesNode.textContent || "[]");
            return Array.isArray(payload) ? payload : [];
        } catch {
            return [];
        }
    }

    allMatches = readInitialMatches();
    updateLeagueOptions(allMatches);
    renderCurrentView();
    bindControls();
    scheduleRefresh();

    window.addEventListener("beforeunload", () => {
        if (refreshTimerId !== null) {
            window.clearTimeout(refreshTimerId);
        }
    });
})();
