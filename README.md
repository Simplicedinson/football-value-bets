# Football Value Bets

Logiciel d'analyse statistique football pour détecter des value bets.

## Objectif

Comparer les probabilités calculées par notre modèle avec les cotes proposées par les bookmakers.

## Marchés ciblés

- 1X2
- Double Chance
- Over / Under 2.5
- Buteurs

## Championnats ciblés

- Championship anglaise
- Ligue 2 française
- Autres deuxièmes divisions européennes

## Stack technique

- Python
- PostgreSQL
- Pandas
- SQLAlchemy / psycopg2
- Football-Data
- FBref

## Structure du projet

```text
app/              Code Python principal
sql/              Scripts SQL PostgreSQL
data/raw/         Données brutes non versionnées
data/processed/   Données nettoyées non versionnées
notebooks/        Tests et analyses exploratoires
tests/            Tests unitaires
logs/             Logs d'import