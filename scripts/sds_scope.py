"""Which repositories a lane covers, read from the SDS member record.

The scheduled lanes used to derive their repository lists from scattered declarations, and the
lists drifted. Membership is now explicit (ADR-0058): this program asks the orchestrator for the
member record and prints one population of it, one value per line.

    sds_scope ledger              observed repositories (onboarding and member)
    sds_scope checkouts           working copies
    sds_scope bindable            working copies of machine-local members granted rollouts
    sds_scope granted <service>   repositories granted one of the seven services

INCOMPLETE IS SAID OUT LOUD, NEVER IMPLIED BY A SHORTER LIST. An unreadable record, an unset
token, an unknown service or an empty population prints nothing on stdout, writes
`sds-scope: <reason>` to stderr and exits 3. There is no partial answer: the record is one read.
"""

import argparse
import os
import sys
from collections.abc import Sequence

from sds_members.client import SERVICES, MembersUnreadable, read_members
from sds_members.populations import bindable, granted, observed, working_copies

TOKEN_VARIABLE = "SDS_SCOPE_ORCHESTRATOR_TOKEN"
KEY_ID = "orchestrator-observer"

EXIT_OK = 0
EXIT_INCOMPLETE = 3


def _incomplete(reason: str) -> int:
    print(f"sds-scope: {reason}", file=sys.stderr)
    return EXIT_INCOMPLETE


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a lane's repositories, one per line.")
    parser.add_argument("population", choices=["ledger", "checkouts", "bindable", "granted"])
    parser.add_argument("service", nargs="?", help="the service, for `granted`")
    args = parser.parse_args(argv)
    if args.population == "granted" and args.service not in SERVICES:
        return _incomplete(f"granted needs one of: {', '.join(SERVICES)}")
    if args.population != "granted" and args.service is not None:
        return _incomplete(f"{args.population} takes no service")
    token = os.environ.get(TOKEN_VARIABLE, "")
    if not token:
        return _incomplete(f"{TOKEN_VARIABLE} is not set")
    try:
        members = read_members(token=token, credential_key_id=KEY_ID)
    except MembersUnreadable as error:
        return _incomplete(str(error))
    names = {
        "ledger": lambda: observed(members),
        "checkouts": lambda: working_copies(members),
        "bindable": lambda: bindable(members),
        "granted": lambda: granted(members, args.service),
    }[args.population]()
    if not names:
        return _incomplete(f"the {args.population} population is empty")
    print("\n".join(names))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
