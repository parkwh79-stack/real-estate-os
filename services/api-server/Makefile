.PHONY: run test build db bash

run:
	uvicorn main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest tests/ -v

build:
	docker build -t realytics-api .

docker-run:
	docker-compose up -d

docker-down:
	docker-compose down
