.PHONY: fmt
fmt:
	uv run ruff format src

.PHONY: check-fmt
check-fmt:
	uv run ruff format --check --diff

.PHONY: ruff
ruff:
	uv run ruff check src

.PHONY: mypy
mypy:
	uv run mypy src/cratere

.PHONY: test
test:
	PYTHONPATH=src uv run pytest src/tests

.PHONY: lint
lint: mypy ruff test check-fmt

.PHONY: build
build:
	uv build
