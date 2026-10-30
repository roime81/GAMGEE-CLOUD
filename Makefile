.PHONY: all bootstrap deploy-core run-agent test clean

all: bootstrap deploy-core

bootstrap:
	@bash scripts/bootstrap.sh

deploy-core:
	@kubectl apply -f platform/capsule/network-policies.yaml

run-agent:
	@cd ai-agent && uvicorn app.main:app --host 127.0.0.1 --port 8080

test:
	@python -m unittest discover -s ai-agent/tests -v

clean:
	@bash scripts/teardown.sh
