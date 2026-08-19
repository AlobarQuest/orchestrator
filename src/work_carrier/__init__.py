"""The carry: an approved change-manager record becomes an orchestrator intake (ADR-0026/0027).

A SEPARATE program (ADR-0002's shape), out of process, on a schedule, exactly as the deploy
watcher, the change proposer and the estate lander are. It composes nothing the orchestrator
owns and it decides nothing: change-manager decides whether the work is wanted, the package
checkout decides whether it can be expressed as an intake, and the orchestrator decides -- in
the transaction that records it -- whether the intake may be registered at all.

ADR-0027 completed it. The last step used to be a person pasting the printed payload into a
form, and that gate was found to be protecting a transcription rather than a judgment: the
payload was authored by an AI every time. What replaces it is attribution -- a machine-registered
intake must name the approved change record that caused it, which this program has and a paste
did not. A bare invocation still writes nothing; `--register` is what makes a pass act.
"""
