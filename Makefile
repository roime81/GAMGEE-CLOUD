.PHONY: all bootstrap deploy-core run-agent test test-factory factory-up factory-down clean

all: bootstrap

bootstrap:
	@bash scripts/bootstrap.sh

# Usage: make deploy-core TENANT=acme
deploy-core:
	@test -n "$(TENANT)" || { echo "Usage: make deploy-core TENANT=<tenant-name>" >&2; exit 2; }
	@bash scripts/apply-network-policies.sh $(TENANT)

run-agent:
	@cd ai-agent && uvicorn app.main:app --host 127.0.0.1 --port 8080

test:
	@cd ai-agent && python -m unittest discover -s tests -v

test-factory:
	@cd ai-factory && python -m unittest discover -s tests -t . -v

factory-up:
	@docker compose -f docker-compose.yml -f docker-compose.ai-factory.yml up -d --build

factory-down:
	@docker compose -f docker-compose.yml -f docker-compose.ai-factory.yml down

clean:
	@bash scripts/teardown.sh
