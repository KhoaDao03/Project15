"""Settlement Edge identity and read-only interpretation of retained legacy history.

No strategy registry, activation, algorithm dispatch, or multi-engine execution lives
here. Historical identities are data: they never instantiate retired strategies.
"""

LEGACY_REVISION = "b4f291105b2f1bbb8f025b82f922abe9b22084f4"


def identity(config):
    return dict(
        model_id="settlement-edge",
        model_name="BTC15 Settlement Edge",
        model_version="v1",
        config_hash=config.version,
    )


def run_model(row):
    body = row["body"]
    if body.get("model"):
        return body["model"]
    # Old control runs predate explicit identities. Child run IDs are not controls.
    archived = "/" in row["run_id"]
    return dict(
        model_id="legacy-archived" if archived else "settlement-edge",
        model_name="Archived strategy" if archived else "BTC15 Settlement Edge",
        model_version="v1",
        config_hash=body.get("versions", {}).get("config", "legacy"),
    )


def history_models(store, mode):
    return {r["run_id"]: run_model(r) for r in store.run_summaries(mode)}


def archived(row, models):
    model = row["body"].get("model") or models.get(row["run_id"]) or run_model(row)
    return model.get("model_id") != "settlement-edge"


def select_history(rows, models, scope="settlement"):
    if scope not in ("settlement", "archive"):
        raise ValueError("Unknown history scope")
    return [r for r in rows if archived(r, models) == (scope == "archive")]


def require_single_run(store, run_id):
    """Reject incompatible resumes before creating an engine or touching checkpoints."""
    if not run_id:
        return
    groups = store.list(kind="model_group", run_id=run_id, limit=None)
    runs = store.list(kind="run", run_id=run_id, limit=None)
    if any(r["body"].get("members") for r in groups) or any(archived(r, {}) for r in runs):
        raise ValueError(
            f"Run {run_id!r} belongs to a retired multi-strategy portfolio. "
            f"Use revision {LEGACY_REVISION} to cancel/settle its exposure, then use a new "
            "Settlement Edge run ID. History is unchanged. See docs/SINGLE_STRATEGY.md."
        )


def guard_archived_exposure(store):
    """Legacy positions/pending orders must be recovered, not hidden by new startup."""
    models = history_models(store, "PAPER")

    def key(r):
        return r["run_id"], r["market"], r["opportunity_id"]

    closed = {key(r) for r in store.list(kind="trade_result", mode="PAPER", limit=None)}
    unresolved = {
        r["run_id"]
        for r in store.list(kind="fill", mode="PAPER", limit=None)
        if archived(r, models) and r["body"].get("action") == "buy" and key(r) not in closed
    }
    for run_id, model in models.items():
        if model.get("model_id") == "settlement-edge":
            continue
        checkpoint = store.load_checkpoint(run_id) or {}
        if checkpoint.get("positions") or any(o.get("active") for o in checkpoint.get("orders", {}).values()):
            unresolved.add(run_id)
    if unresolved:
        raise ValueError(
            "Unresolved archived paper exposure: "
            + ", ".join(sorted(unresolved))
            + f". Recover with revision {LEGACY_REVISION}; do not clear leases/checkpoints "
            "or reset budgets. See docs/SINGLE_STRATEGY.md."
        )
