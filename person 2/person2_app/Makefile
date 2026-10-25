# SatyaLens — Makefile
# Provides a documented `make package` command for clean source distribution.

.PHONY: package test build

## package: Create a clean source ZIP excluding dev artifacts, secrets, and evidence files.
##          The ZIP includes: source, tests, docs, config examples.
##          It NEVER includes: .venv, node_modules, uploads, *.db, .env files, or real evidence.
package:
	@VERSION=$$(date +%Y%m%d_%H%M) && \
	ZIP_NAME=satyalens_release_$${VERSION}.zip && \
	echo "Creating $$ZIP_NAME ..." && \
	zip -r $$ZIP_NAME . \
	  --exclude "*.venv/*" \
	  --exclude "*/.venv/*" \
	  --exclude "*/node_modules/*" \
	  --exclude "*/__pycache__/*" \
	  --exclude "*.pyc" \
	  --exclude "*.pyo" \
	  --exclude "*/uploads/*" \
	  --exclude "*/reports/*" \
	  --exclude "*/tmp/*" \
	  --exclude "*.db" \
	  --exclude "*.sqlite" \
	  --exclude "*.sqlite3" \
	  --exclude ".env" \
	  --exclude ".env.*" \
	  --exclude "*.env" \
	  --exclude "*.pem" \
	  --exclude "*.key" \
	  --exclude "*.p12" \
	  --exclude "*/dist/*" \
	  --exclude "*/.git/*" \
	  --exclude "*/.mypy_cache/*" \
	  --exclude "*/.pytest_cache/*" \
	  --exclude "satyalens_release_*.zip" && \
	echo "Done: $$ZIP_NAME"

## test: Run the full backend test suite.
test:
	cd backend && .venv/bin/pytest -v

## build: Build the frontend production bundle.
build:
	cd frontend && npm run build
