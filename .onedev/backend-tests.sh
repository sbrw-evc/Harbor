#!/bin/sh
# The backend tests with their coverage (backend/coverage.xml for SonarQube), as the Release and SonarQube jobs run
# them: in python:3.12-slim, with a PostgreSQL of the container itself so that the tests with a database run too.
# Usage: sh .onedev/backend-tests.sh (from the root of the repository).
set -eu
apt-get update -qq
apt-get install -y -qq --no-install-recommends openssh-client postgresql >/dev/null
PG=$(ls -d /usr/lib/postgresql/*/bin | head -n 1)
DATA=$(mktemp -d)
chown postgres "$DATA"
su postgres -c "$PG/initdb -A trust -D $DATA" >/dev/null
su postgres -c "$PG/pg_ctl -s -w -D $DATA -l $DATA/log -o '-k /tmp -c listen_addresses=127.0.0.1' start"
su postgres -c "psql -q -h /tmp -c 'CREATE DATABASE test'"
export TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/test

pip install -q --no-cache-dir -r backend/requirements.txt -r backend/requirements-dev.txt
cd backend
python -m pytest -q -p no:cacheprovider --cov=app --cov-report=xml:coverage.xml --cov-report=term tests
