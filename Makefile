# SPDX-License-Identifier: FSL-1.1-ALv2
# Common tasks. Works the same in Codespaces, a Dev Container or a local venv.
.PHONY: help setup test boundary check run-offline

help:
	@echo "make setup        install core in editable mode with dev tools"
	@echo "make test         run the core test suite (offline, no API keys)"
	@echo "make boundary     check that core/ never imports ee/"
	@echo "make check        test + boundary (what CI runs)"
	@echo "make run-offline ISSUE=N   run the agent flow on issue N with the offline planner"

setup:
	python -m pip install --upgrade pip
	pip install -e "core[dev]"
	@test -f core/.env || cp core/.env.example core/.env

test:
	cd core && TRADVIA_OFFLINE=1 python -m pytest -q

boundary:
	bash scripts/check-license-boundary.sh

check: test boundary

run-offline:
	@test -n "$(ISSUE)" || (echo "usage: make run-offline ISSUE=<number>" && exit 1)
	cd core && TRADVIA_OFFLINE=1 tradvia --issue $(ISSUE)
