-- ============================================================
-- Football Value Bets - PostgreSQL Schema
-- ============================================================

BEGIN;

-- ============================================================
-- 1. Leagues
-- ============================================================

CREATE TABLE leagues (
    league_id BIGSERIAL PRIMARY KEY,
    league_code VARCHAR(10) NOT NULL UNIQUE,
    league_name VARCHAR(100) NOT NULL,
    country VARCHAR(100) NOT NULL,
    division_level SMALLINT NOT NULL CHECK (division_level > 0),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 2. Seasons
-- ============================================================

CREATE TABLE seasons (
    season_id BIGSERIAL PRIMARY KEY,
    season_name VARCHAR(20) NOT NULL UNIQUE,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT check_season_dates CHECK (end_date > start_date)
);

-- ============================================================
-- 3. Teams
-- ============================================================

CREATE TABLE teams (
    team_id BIGSERIAL PRIMARY KEY,
    team_name VARCHAR(150) NOT NULL,
    country VARCHAR(100),
    current_league_id BIGINT REFERENCES leagues(league_id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_team_name_country UNIQUE (team_name, country)
);

-- ============================================================
-- 4. Team aliases
-- ============================================================

CREATE TABLE team_aliases (
    team_alias_id BIGSERIAL PRIMARY KEY,
    team_id BIGINT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
    source VARCHAR(50) NOT NULL,
    alias_name VARCHAR(150) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_team_alias_source UNIQUE (source, alias_name)
);

-- ============================================================
-- 5. Players
-- ============================================================

CREATE TABLE players (
    player_id BIGSERIAL PRIMARY KEY,
    player_name VARCHAR(150) NOT NULL,
    current_team_id BIGINT REFERENCES teams(team_id),
    main_position VARCHAR(30),
    nationality VARCHAR(100),
    date_of_birth DATE,
    fbref_player_url TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_player_identity UNIQUE (player_name, date_of_birth)
);

-- ============================================================
-- 6. Player aliases
-- ============================================================

CREATE TABLE player_aliases (
    player_alias_id BIGSERIAL PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    source VARCHAR(50) NOT NULL,
    alias_name VARCHAR(150) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_player_alias_source UNIQUE (source, alias_name)
);

-- ============================================================
-- 7. Player team history
-- ============================================================

CREATE TABLE player_team_history (
    player_team_history_id BIGSERIAL PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    team_id BIGINT NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
    start_date DATE NOT NULL,
    end_date DATE,
    is_loan BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT check_player_team_history_dates
        CHECK (end_date IS NULL OR end_date >= start_date)
);

-- ============================================================
-- 8. Fixtures
-- ============================================================

CREATE TABLE fixtures (
    fixture_id BIGSERIAL PRIMARY KEY,
    league_id BIGINT NOT NULL REFERENCES leagues(league_id),
    season_id BIGINT NOT NULL REFERENCES seasons(season_id),
    match_date TIMESTAMP NOT NULL,
    home_team_id BIGINT NOT NULL REFERENCES teams(team_id),
    away_team_id BIGINT NOT NULL REFERENCES teams(team_id),
    status VARCHAR(30) NOT NULL DEFAULT 'scheduled',
    source VARCHAR(50),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT check_fixture_teams CHECK (home_team_id <> away_team_id),

    CONSTRAINT check_fixture_status CHECK (
        status IN ('scheduled', 'postponed', 'cancelled', 'played')
    ),

    CONSTRAINT unique_fixture UNIQUE (
        league_id,
        season_id,
        match_date,
        home_team_id,
        away_team_id
    )
);

-- ============================================================
-- 9. Matches
-- ============================================================

CREATE TABLE matches (
    match_id BIGSERIAL PRIMARY KEY,
    fixture_id BIGINT NOT NULL UNIQUE REFERENCES fixtures(fixture_id) ON DELETE CASCADE,

    home_goals SMALLINT CHECK (home_goals >= 0),
    away_goals SMALLINT CHECK (away_goals >= 0),

    home_shots SMALLINT DEFAULT 0 CHECK (home_shots >= 0),
    away_shots SMALLINT DEFAULT 0 CHECK (away_shots >= 0),

    home_shots_on_target SMALLINT DEFAULT 0 CHECK (home_shots_on_target >= 0),
    away_shots_on_target SMALLINT DEFAULT 0 CHECK (away_shots_on_target >= 0),

    home_xg_estimated NUMERIC(5,2) DEFAULT 0 CHECK (home_xg_estimated >= 0),
    away_xg_estimated NUMERIC(5,2) DEFAULT 0 CHECK (away_xg_estimated >= 0),

    source VARCHAR(50),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 10. Player match stats
-- ============================================================

CREATE TABLE player_match_stats (
    player_match_stat_id BIGSERIAL PRIMARY KEY,

    match_id BIGINT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    player_id BIGINT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
    team_id BIGINT NOT NULL REFERENCES teams(team_id),

    position_played VARCHAR(30),
    is_starter BOOLEAN NOT NULL DEFAULT FALSE,
    is_captain BOOLEAN NOT NULL DEFAULT FALSE,

    minutes_played SMALLINT NOT NULL DEFAULT 0 CHECK (
        minutes_played >= 0 AND minutes_played <= 130
    ),

    goals SMALLINT NOT NULL DEFAULT 0 CHECK (goals >= 0),
    assists SMALLINT NOT NULL DEFAULT 0 CHECK (assists >= 0),

    shots SMALLINT NOT NULL DEFAULT 0 CHECK (shots >= 0),
    shots_on_target SMALLINT NOT NULL DEFAULT 0 CHECK (shots_on_target >= 0),

    expected_goals NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (expected_goals >= 0),
    non_penalty_expected_goals NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (non_penalty_expected_goals >= 0),
    expected_assisted_goals NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (expected_assisted_goals >= 0),

    penalties_taken SMALLINT NOT NULL DEFAULT 0 CHECK (penalties_taken >= 0),
    penalties_scored SMALLINT NOT NULL DEFAULT 0 CHECK (penalties_scored >= 0),

    yellow_cards SMALLINT NOT NULL DEFAULT 0 CHECK (yellow_cards >= 0),
    red_cards SMALLINT NOT NULL DEFAULT 0 CHECK (red_cards >= 0),

    fouls_committed SMALLINT NOT NULL DEFAULT 0 CHECK (fouls_committed >= 0),
    fouls_drawn SMALLINT NOT NULL DEFAULT 0 CHECK (fouls_drawn >= 0),

    source VARCHAR(50) DEFAULT 'FBref',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_player_match UNIQUE (match_id, player_id)
);

-- ============================================================
-- 11. Bookmakers
-- ============================================================

CREATE TABLE bookmakers (
    bookmaker_id BIGSERIAL PRIMARY KEY,
    bookmaker_name VARCHAR(100) NOT NULL UNIQUE,
    country VARCHAR(100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 12. Market odds
-- ============================================================

CREATE TABLE market_odds (
    market_odd_id BIGSERIAL PRIMARY KEY,

    fixture_id BIGINT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    bookmaker_id BIGINT NOT NULL REFERENCES bookmakers(bookmaker_id),

    market_type VARCHAR(50) NOT NULL,
    selection_code VARCHAR(50),
    selection_player_id BIGINT REFERENCES players(player_id),

    line_value NUMERIC(5,2),
    odds_value NUMERIC(7,3) NOT NULL CHECK (odds_value > 1),

    odds_stage VARCHAR(30) NOT NULL DEFAULT 'snapshot',
    odds_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    source VARCHAR(50),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT check_odds_stage CHECK (
        odds_stage IN ('opening', 'snapshot', 'closing', 'live')
    )
);

-- ============================================================
-- 13. Model predictions
-- ============================================================

CREATE TABLE model_predictions (
    model_prediction_id BIGSERIAL PRIMARY KEY,

    fixture_id BIGINT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,

    market_type VARCHAR(50) NOT NULL,
    selection_code VARCHAR(50),
    selection_player_id BIGINT REFERENCES players(player_id),
    line_value NUMERIC(5,2),

    model_probability NUMERIC(6,5) NOT NULL CHECK (
        model_probability > 0 AND model_probability < 1
    ),

    fair_odds NUMERIC(7,3) NOT NULL CHECK (fair_odds > 1),

    model_name VARCHAR(100) NOT NULL,
    model_version VARCHAR(50),

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 14. Value bets
-- ============================================================

CREATE TABLE value_bets (
    value_bet_id BIGSERIAL PRIMARY KEY,

    fixture_id BIGINT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    market_odd_id BIGINT NOT NULL REFERENCES market_odds(market_odd_id) ON DELETE CASCADE,
    model_prediction_id BIGINT NOT NULL REFERENCES model_predictions(model_prediction_id) ON DELETE CASCADE,

    bookmaker_id BIGINT NOT NULL REFERENCES bookmakers(bookmaker_id),

    market_type VARCHAR(50) NOT NULL,
    selection_code VARCHAR(50),
    selection_player_id BIGINT REFERENCES players(player_id),
    line_value NUMERIC(5,2),

    bookmaker_odds NUMERIC(7,3) NOT NULL CHECK (bookmaker_odds > 1),
    model_probability NUMERIC(6,5) NOT NULL CHECK (
        model_probability > 0 AND model_probability < 1
    ),
    fair_odds NUMERIC(7,3) NOT NULL CHECK (fair_odds > 1),

    edge_percent NUMERIC(7,3) NOT NULL,
    confidence_score NUMERIC(5,2) DEFAULT 0 CHECK (
        confidence_score >= 0 AND confidence_score <= 100
    ),

    status VARCHAR(30) NOT NULL DEFAULT 'detected',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT check_value_bet_status CHECK (
        status IN ('detected', 'selected', 'won', 'lost', 'void')
    )
);

-- ============================================================
-- 15. Import logs
-- ============================================================

CREATE TABLE import_logs (
    import_log_id BIGSERIAL PRIMARY KEY,

    source VARCHAR(50) NOT NULL,
    file_name VARCHAR(255),
    league_code VARCHAR(10),
    season_name VARCHAR(20),

    rows_inserted INTEGER NOT NULL DEFAULT 0 CHECK (rows_inserted >= 0),
    rows_updated INTEGER NOT NULL DEFAULT 0 CHECK (rows_updated >= 0),
    rows_failed INTEGER NOT NULL DEFAULT 0 CHECK (rows_failed >= 0),

    status VARCHAR(30) NOT NULL DEFAULT 'started',
    error_message TEXT,

    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,

    CONSTRAINT check_import_status CHECK (
        status IN ('started', 'success', 'failed', 'partial')
    )
);

-- ============================================================
-- Indexes
-- ============================================================

CREATE INDEX idx_teams_current_league
ON teams(current_league_id);

CREATE INDEX idx_team_aliases_team
ON team_aliases(team_id);

CREATE INDEX idx_players_current_team
ON players(current_team_id);

CREATE INDEX idx_player_aliases_player
ON player_aliases(player_id);

CREATE INDEX idx_player_team_history_player
ON player_team_history(player_id);

CREATE INDEX idx_player_team_history_team
ON player_team_history(team_id);

CREATE INDEX idx_fixtures_league_season
ON fixtures(league_id, season_id);

CREATE INDEX idx_fixtures_match_date
ON fixtures(match_date);

CREATE INDEX idx_fixtures_home_team
ON fixtures(home_team_id);

CREATE INDEX idx_fixtures_away_team
ON fixtures(away_team_id);

CREATE INDEX idx_matches_fixture
ON matches(fixture_id);

CREATE INDEX idx_player_match_stats_match
ON player_match_stats(match_id);

CREATE INDEX idx_player_match_stats_player
ON player_match_stats(player_id);

CREATE INDEX idx_player_match_stats_team
ON player_match_stats(team_id);

CREATE INDEX idx_player_match_stats_position
ON player_match_stats(position_played);

CREATE INDEX idx_market_odds_fixture
ON market_odds(fixture_id);

CREATE INDEX idx_market_odds_bookmaker
ON market_odds(bookmaker_id);

CREATE INDEX idx_market_odds_market_type
ON market_odds(market_type);

CREATE INDEX idx_market_odds_player_selection
ON market_odds(selection_player_id);

CREATE INDEX idx_market_odds_time
ON market_odds(odds_time);

CREATE INDEX idx_model_predictions_fixture
ON model_predictions(fixture_id);

CREATE INDEX idx_model_predictions_market
ON model_predictions(market_type);

CREATE INDEX idx_value_bets_fixture
ON value_bets(fixture_id);

CREATE INDEX idx_value_bets_status
ON value_bets(status);

CREATE INDEX idx_import_logs_source
ON import_logs(source);

COMMIT;