#!/usr/bin/env bash
# Load BWS_ACCESS_TOKEN for the SDS runtime-fetch set from the macOS login Keychain
# — never from a plaintext file.
#
# Source this (don't execute it) at the top of any SDS script that calls `bws`:
#     source "$(dirname "${BASH_SOURCE[0]}")/sds-token.sh"
#
# Keychain item: service 'Claude', account 'BWS_ACCESS_TOKEN_SDS'
# (mirrors the BWS_ACCESS_TOKEN_VPS_BACKUP / BWS_ACCESS_TOKEN_INFRA_DRIFT pattern).
# Create/update it with:
#     security add-generic-password -U -s Claude -a BWS_ACCESS_TOKEN_SDS \
#         -T /usr/bin/security -w
#
# The token belongs to the read-only `sds-operator` BWS machine account, scoped to the
# `SDS Operator` project alone. That project holds exactly the three secrets the SDS
# runtime fetches: the orchestrator SYSTEM bearer, the VERIFIER bearer, and the Todoist
# token. It can read nothing else, and it can write nothing at all.
#
# Before 2026-07-30 these scripts bootstrapped with the vps-backup machine account's
# token, which could read the whole Ops / Platform project — over-scoped, never leaked.
# See docs/software-delivery-system/2026-07-30-sds-bws-token-migration-plan.md.
#
# Idempotent: respects an already-set BWS_ACCESS_TOKEN (e.g. an ad-hoc override).

if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
    BWS_ACCESS_TOKEN="$(/usr/bin/security find-generic-password \
        -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)"
    export BWS_ACCESS_TOKEN
fi

if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
    echo "FATAL: BWS_ACCESS_TOKEN not found in Keychain" \
         "(service=Claude, account=BWS_ACCESS_TOKEN_SDS)." \
         "Add it with: security add-generic-password -U -s Claude" \
         "-a BWS_ACCESS_TOKEN_SDS -T /usr/bin/security -w" >&2
    # `return` when sourced (the documented use); `exit` if executed by mistake.
    # Spelled as an explicit branch rather than the canonical helpers'
    # `return 1 2>/dev/null || exit 1`: that form is behaviourally identical, but
    # the `exit` reads as unreachable to the linter (SC2317), which both
    # bws-token.sh copies carry as baselined debt. New code shouldn't need it.
    if (return 0 2>/dev/null); then return 1; else exit 1; fi
fi
