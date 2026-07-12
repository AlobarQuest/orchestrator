"""The report-only reconciliation runner (WS-P2.1 AC-009).

A separate process. It imports NOTHING from `orchestrator.*` -- including
`orchestrator.persistence`, which would hand it a database session and let it write canonical
state directly, gutting the report-only mandate. The only coupling is the HTTP wire contract.
"""
