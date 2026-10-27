"""The client side of the SDS member record (ADR-0058): read it, stage a change to it.

A separate program beside the orchestrator, in the shape of the other lane packages: it reaches
the orchestrator over HTTP and imports nothing from it. The scheduled lanes derive their
populations from `GET /api/v1/members` through `populations`; a person's change to the record is
staged with `sds-members stage` and confirmed on the orchestrator's `/review` page.
"""
