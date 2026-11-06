# Countersign: every command a judge runs is a recipe here (architecture.md §12.2)
set dotenv-filename := ".env.local"
set dotenv-load := true

default:
    @just --list

install:
    uv sync

# Phase 3: register agents, create personas, generate invoices
setup:
    uv run python scripts/register_agents.py
    uv run python scripts/setup_personas.py
    uv run python scripts/seed_invoices.py

discover:
    uv run python scripts/discover_env.py

set-addressing:
    uv run python scripts/set_addressing.py

create-docs:
    uv run python scripts/create_docs.py

build-template:
    uv run python scripts/build_template.py

setup-members:
    uv run python scripts/setup_personas.py --members

run:
    uv run python -m countersign run

scripted-intake inv:
    uv run python scripts/post_as.py --demo-upload {{inv}} --no-mention
    uv run python -m countersign.tools.scripted_intake {{inv}}

demo-upload inv:
    uv run python scripts/post_as.py --demo-upload {{inv}}

redteam:
    uv run python redteam/run.py

verify:
    uv run python -m countersign verify

bank:
    uv run python -m countersign bank

status:
    uv run python -m countersign status

accept-policy:
    uv run python -m countersign accept-policy

test:
    uv run pytest

check:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy src/countersign/core src/countersign/store src/countersign/chat
    uv run python scripts/check_purity.py

# Deletes var/ only; never ~/.config/countersign/ (rules.md §7)
reset:
    @read -p "Delete var/ (database and logs)? [y/N] " a && [ "$a" = "y" ] && rm -rf var && mkdir -p var || echo "aborted"
