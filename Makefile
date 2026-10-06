.PHONY: setup test panel stop status audit
setup:
	bash scripts/setup-dev.sh
test:
	bash scripts/check.sh
panel:
	.venv/bin/python scripts/dev_panel.py start
stop:
	.venv/bin/python scripts/dev_panel.py stop
status:
	.venv/bin/python scripts/dev_panel.py status
audit:
	.venv/bin/python scripts/audit_runtime.py
