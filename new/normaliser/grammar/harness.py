"""
Safe importer for the six CRM report services.

The services construct watsonx `ModelInference` and Presto connections at import
time. This module stubs those so the *pure* parsing functions can be exercised
offline, with no credentials and no network.

Anything the stub intercepts is recorded, so a probe result that secretly
depended on an LLM call is visible rather than silently treated as
deterministic.
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[3] / "code" / "old"

# Recorded whenever stubbed infrastructure is touched during a probe.
LLM_CALLS: list[str] = []
DB_CALLS: list[str] = []


class _StubModelInference:
    """Stands in for watsonx ModelInference. Never authenticates."""

    def __init__(self, *args, **kwargs):
        self._args = args

    def generate_text(self, prompt=None, *args, **kwargs):
        LLM_CALLS.append(str(prompt)[:200] if prompt else "<no prompt>")
        # Return an empty JSON object: the services' own error handling then
        # falls back to their deterministic path, which is what we want to probe.
        return "<JSON_RESPONSE>{}</JSON_RESPONSE>"

    def generate(self, *args, **kwargs):
        LLM_CALLS.append("generate()")
        return {"results": [{"generated_text": "{}"}]}


class _StubCredentials:
    def __init__(self, *args, **kwargs):
        pass


class _StubCursor:
    description = None

    def execute(self, sql, *a, **k):
        DB_CALLS.append(sql[:300])
        raise RuntimeError("Presto disabled during grammar probe")

    def fetchall(self):
        return []


class _StubConn:
    def cursor(self):
        return _StubCursor()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


def _install_stubs() -> None:
    """Replace network-touching third-party surfaces with inert stand-ins."""
    # --- watsonx ---
    wx = types.ModuleType("ibm_watsonx_ai")
    wx.Credentials = _StubCredentials
    wx.APIClient = object

    fm = types.ModuleType("ibm_watsonx_ai.foundation_models")
    fm.ModelInference = _StubModelInference

    meta = types.ModuleType("ibm_watsonx_ai.metanames")
    meta.GenTextParamsMetaNames = type(
        "GenTextParamsMetaNames",
        (),
        {k: k for k in ("DECODING_METHOD", "MAX_NEW_TOKENS", "MIN_NEW_TOKENS",
                        "TEMPERATURE", "TOP_K", "TOP_P", "REPETITION_PENALTY",
                        "STOP_SEQUENCES", "RANDOM_SEED")},
    )

    fmu = types.ModuleType("ibm_watsonx_ai.foundation_models.utils")
    enums = types.ModuleType("ibm_watsonx_ai.foundation_models.utils.enums")
    enums.ModelTypes = type("ModelTypes", (), {})
    enums.DecodingMethods = type("DecodingMethods", (), {"GREEDY": "greedy"})

    wx.foundation_models = fm
    fm.utils = fmu
    fmu.enums = enums

    for name, mod in [
        ("ibm_watsonx_ai", wx),
        ("ibm_watsonx_ai.foundation_models", fm),
        ("ibm_watsonx_ai.foundation_models.utils", fmu),
        ("ibm_watsonx_ai.foundation_models.utils.enums", enums),
        ("ibm_watsonx_ai.metanames", meta),
    ]:
        sys.modules[name] = mod

    # --- matplotlib / ibm_boto3 (task_report only, used for graph rendering) ---
    # Stubbed because the installed matplotlib is built against NumPy 1.x and
    # fails to initialise under NumPy 2.x. Not on any parsing path.
    mpl = types.ModuleType("matplotlib")
    mpl.use = lambda *a, **k: None
    plt = types.ModuleType("matplotlib.pyplot")
    for _fn in ("figure", "plot", "bar", "savefig", "close", "tight_layout",
                "xlabel", "ylabel", "title", "xticks", "yticks", "legend", "subplots"):
        setattr(plt, _fn, lambda *a, **k: None)
    mpl.pyplot = plt
    sys.modules["matplotlib"] = mpl
    sys.modules["matplotlib.pyplot"] = plt

    boto = types.ModuleType("ibm_boto3")
    boto.client = lambda *a, **k: None
    boto.resource = lambda *a, **k: None
    botocore = types.ModuleType("ibm_botocore")
    bc_client = types.ModuleType("ibm_botocore.client")
    bc_client.Config = lambda *a, **k: None
    botocore.client = bc_client
    sys.modules["ibm_boto3"] = boto
    sys.modules["ibm_botocore"] = botocore
    sys.modules["ibm_botocore.client"] = bc_client

    # --- prestodb ---
    pdb = types.ModuleType("prestodb")
    dbapi = types.ModuleType("prestodb.dbapi")
    dbapi.connect = lambda *a, **k: _StubConn()
    auth = types.ModuleType("prestodb.auth")
    auth.BasicAuthentication = lambda *a, **k: None
    pdb.dbapi = dbapi
    pdb.auth = auth
    sys.modules["prestodb"] = pdb
    sys.modules["prestodb.dbapi"] = dbapi
    sys.modules["prestodb.auth"] = auth


def _seed_env() -> None:
    """Fill env vars the services read at import time, so nothing is None."""
    defaults = {
        "PRESTO_CATALOG": "probe_catalog",
        "PRESTO_SCHEMA": "probe_schema",
        "PRESTO_HOST": "localhost",
        "PRESTO_PORT": "8080",
        "PRESTO_USER": "probe",
        "PRESTO_USERNAME": "probe",
        "PRESTO_PASSWORD": "probe",
        "PRESTO_LEAD_SCHEMA": "probe_schema",
        "PRESTO_EVENT_SCHEMA": "probe_schema",
        "PRESTO_OPPO_SCHEMA": "probe_schema",
        "PRESTO_TASK_SCHEMA": "probe_schema",
        "PRESTO_CASE_SCHEMA": "probe_schema",
        "TABLE_LEAD": "lead_t",
        "TABLE_EVENT": "event_t",
        "TABLE_OPPO": "opp_t",
        "TABLE_TASK": "task_t",
        "TABLE_CASE": "case_t",
        "PRESTO_TABLE": "probe_table",
        "WATSONX_URL": "https://us-south.ml.cloud.ibm.com",
        "WATSONX_API_KEY": "probe-key",
        "WATSONX_PROJECT_ID": "probe-project",
        "PROJECT_ID": "probe-project",
        "MODEL_ID": "probe-model",
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, v)


@contextmanager
def _on_path(directory: Path):
    directory = str(directory)
    added = directory not in sys.path
    if added:
        sys.path.insert(0, directory)
    try:
        yield
    finally:
        if added and directory in sys.path:
            sys.path.remove(directory)


_PREPARED = False


def prepare() -> None:
    global _PREPARED
    if not _PREPARED:
        _install_stubs()
        _seed_env()
        _PREPARED = True


def load(service: str):
    """Import one service module with stubs in place.

    Returns the module, or raises with the original error so an uncoverable
    service is reported explicitly rather than skipped in silence.
    """
    prepare()
    with _on_path(CODE_DIR):
        return importlib.import_module(service)


@contextmanager
def watch():
    """Record LLM / DB access that happens inside the block."""
    llm_start, db_start = len(LLM_CALLS), len(DB_CALLS)
    record = {}
    try:
        yield record
    finally:
        record["llm_calls"] = LLM_CALLS[llm_start:]
        record["db_calls"] = DB_CALLS[db_start:]


# --------------------------------------------------------------------------
# Per-service date-parser entry points.
# Each returns a normalised (start, end, label) tuple or None.
# --------------------------------------------------------------------------

def _lead_dates(mod, q):
    return mod.LeadIntentDetector().detect_date_intent(q)


def _opp_dates(mod, q):
    return mod.OppIntentDetector().detect_date_intent(q)


def _task_dates(mod, q):
    return mod.TaskIntentDetector().detect_date_intent(q)


def _event_dates(mod, q):
    return mod.EventIntentDetector().detect_date_intent(q)


def _case_dates(mod, q):
    return mod.get_date_range_for_query(q.lower())


def _targets_dates(mod, q):
    return mod.parse_dates_from_question(q)


SERVICES = {
    "lead":    ("lead_report",       _lead_dates),
    "opp":     ("opportunity_report", _opp_dates),
    "task":    ("task_report",       _task_dates),
    "event":   ("event_report",      _event_dates),
    "case":    ("case_report",       _case_dates),
    "targets": ("targetvsactuals",   _targets_dates),
}


def parse_dates(service: str, question: str):
    """Run one service's date parser on one question.

    Returns (result, meta) where meta records whether the stubbed LLM or DB was
    reached — i.e. whether this parse was genuinely deterministic.
    """
    module_name, fn = SERVICES[service]
    mod = load(module_name)
    with watch() as rec:
        try:
            result = fn(mod, question)
            err = None
        except Exception as e:  # noqa: BLE001 - probe records failures as data
            result, err = None, f"{type(e).__name__}: {e}"
    return result, {"error": err, **rec}
