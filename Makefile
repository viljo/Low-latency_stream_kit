.PHONY: test

test:
	uv run pytest -q -k integration || python -m pytest -q -k integration
