"""Fail-closed Vercel configuration; identity is supplied by the operator."""
import os

from .release_persistent import PersistentReleaseService
from .release_readiness import SIGNER, CONTRACT
from .release_storage import PostgresRecordStore


def production_service():
    environment = os.environ.get("VERCEL_ENV", "preview")
    namespace = os.environ.get("CLUETIDE_STORAGE_NAMESPACE", "production" if environment == "production" else "preview")
    dsn = os.environ.get("DATABASE_URL", "")
    # A missing database or key keeps readiness available while all records and
    # writes fail closed. No process-local signing identity is created.
    store = PostgresRecordStore(dsn or "postgresql://unconfigured@127.0.0.1:1/unconfigured", namespace)
    key = os.environ.get("CLUETIDE_RELEASE_SIGNING_KEY")
    os.environ.setdefault("CLUETIDE_RELEASE_CONTRACT", CONTRACT)
    return PersistentReleaseService(store, signing_key=key, expected_signer=SIGNER,
        writes_enabled=environment == "production" and os.environ.get("CLUETIDE_RELEASE_WRITES_ENABLED") == "1")
