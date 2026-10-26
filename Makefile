PY ?= python3

realism:
	$(PY) -m secondlook realism --seeds 100

.PHONY: realism test e2e lint gen run serve record harness export-replay demo-slice

test: lint
	$(PY) -m pytest -q -m "not live and not e2e and not slow"

lint:
	$(PY) -m ruff check .

e2e:
	$(PY) -m pytest -q -m e2e

gen:
	$(PY) -m secondlook generate

run:
	$(PY) -m secondlook run

serve:
	$(PY) -m secondlook serve

record:
	$(PY) -m secondlook record

harness:
	$(PY) -m secondlook harness

export-replay:
	$(PY) -m secondlook export-replay

demo-slice:
	$(PY) -m secondlook run --slice --llm fake --speed 10x
