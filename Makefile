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
	.venv/bin/python scripts/panel_regression_probe.py migration/01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5 --output .local/panel-regressions.json --fail-on-findings
