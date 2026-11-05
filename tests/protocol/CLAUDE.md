# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Protocol smoke tests (`tests/protocol/`)

- Protocol smoke tests may manipulate time or lease expiry as fixture setup; runtime recovery
  behaviour must go through public API/CLI surfaces, never private service shortcuts. The scan
  covers every `.py` file under `tests/protocol/` except the shared setup it names, so a new
  protocol test is scanned without being listed
  (`test_the_scan_covers_every_protocol_test_and_not_the_shared_setup`).
  History: `docs/history/claude-md-invariants-archive.md` #5.
