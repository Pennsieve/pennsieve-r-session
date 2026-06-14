.PHONY: help run build clean

SERVICE_NAME ?= "pennsieve-r-session"

.DEFAULT: help

help:
	@echo "Make Help for $(SERVICE_NAME)"
	@echo ""
	@echo "make build   - build the Docker image"
	@echo "make run     - run the session locally via docker-compose (JupyterLab on http://localhost:8888)"
	@echo "make clean   - remove output files"

build:
	docker build -t $(SERVICE_NAME) .

run:
	docker-compose -f docker-compose.yml down --remove-orphans
	docker-compose -f docker-compose.yml build
	docker-compose -f docker-compose.yml up

clean:
	rm -f data/output/*
