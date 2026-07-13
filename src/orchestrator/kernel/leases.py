import hashlib
from datetime import timedelta

LEASE_DURATION = timedelta(minutes=15)
RENEWAL_CADENCE = timedelta(minutes=5)
DEFAULT_MAX_ATTEMPTS = 3
MIN_PRODUCTION_DRILL_DEADLINE_SECONDS = 60


def hash_lease_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
