# Football Value Bets

Football statistical analysis software designed to detect value betting opportunities.

## Objective

Compare the probabilities calculated by our model with the odds offered by bookmakers.

## Target Markets

- 1X2
- Double Chance
- Over / Under 2.5
- Anytime Scorer

## Target Leagues

- English Championship
- French Ligue 2
- Other European second divisions

## Tech Stack

- Python
- PostgreSQL
- Pandas
- SQLAlchemy / psycopg2
- Football-Data
- FBref

## Project Structure

```text
app/              Main Python application code
sql/              PostgreSQL SQL scripts
data/raw/         Raw non-versioned data
data/processed/   Cleaned non-versioned data
notebooks/        Exploratory analysis and tests
tests/            Unit tests
logs/             Import logs