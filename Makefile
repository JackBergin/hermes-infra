.DEFAULT_GOAL := help

.PHONY: help init setup configure up down restart logs doctor backup backup-offsite restore restore-force restore-test upgrade check

PYTHON ?= python3
CTL = $(PYTHON) scripts/hermesctl.py

help:
	@$(CTL) help
init:
	@$(CTL) init
setup:
	@$(CTL) setup
configure:
	@$(CTL) configure
up:
	@$(CTL) up
down:
	@$(CTL) down
restart:
	@$(CTL) restart
logs:
	@$(CTL) logs
doctor:
	@$(CTL) doctor
backup:
	@$(CTL) backup
backup-offsite:
	@$(CTL) backup --offsite
restore:
	@test -n "$(FILE)" || (echo "Use FILE=/absolute/or/relative/backup.zip" >&2; exit 2)
	@$(CTL) restore "$(FILE)"
restore-force:
	@test -n "$(FILE)" || (echo "Use FILE=/absolute/or/relative/backup.zip" >&2; exit 2)
	@$(CTL) restore "$(FILE)" --force
restore-test:
	@test -n "$(FILE)" || (echo "Use FILE=/absolute/or/relative/backup.zip" >&2; exit 2)
	@$(CTL) restore-test "$(FILE)"
upgrade:
	@test -n "$(IMAGE)" || (echo "Use IMAGE=nousresearch/hermes-agent@sha256:<digest>" >&2; exit 2)
	@$(CTL) upgrade "$(IMAGE)"
check:
	@$(PYTHON) -m unittest discover -s tests -v
	@$(PYTHON) -m py_compile scripts/hermesctl.py
