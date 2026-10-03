"""Public search/lookup boundary shared with future cnip/cnnv callers.

Both methods accept either request fields or {"request": request_fields} and
return the evidence.json contract. Import-only operation can return pending
work; resume the same query_id, never create a second run just to poll it.
"""
from copy import deepcopy
from workflow import Workflow
from search.modules.export import build_export
from shared.contracts import validate


class SearchService:
    def __init__(self, data_dir=None):
        self.workflow = Workflow(data_dir)

    def search(self, request):
        return self._start("search", request)

    def lookup(self, request):
        return self._start("lookup", request)

    def _start(self, entry, request):
        payload = deepcopy(request.get("request", request))
        payload["entry"] = entry
        payload.setdefault("caller", "cnnv")
        result = self.workflow.create(payload)
        return self.evidence(result["run_id"])

    def evidence(self, query_id):
        run = self.workflow.store.load(query_id)
        actual_spec = run["spec"]
        if actual_spec is None:
            # This transient skeleton represents an unfinished contract, not an inferred specification.
            run["spec"] = {"entry": run["request"]["entry"], "intent": run["request"]["intent"],
                           "read_scope": run["request"]["read_scope"], "base_date": run["request"].get("base_date"),
                           "keys": run["request"]["keys"], "features": run["request"]["features"], "hard": {"op": "AND", "args": []}, "soft": []}
        result = build_export(run, self.workflow._review_targets(run), self.workflow._missing_materials(run))
        result["scope"]["workflow_state"] = {"stage": run["stage"], "status": run["status"]}
        result["scope"]["task_directory"] = str(self.workflow.store.run_dir(query_id))
        if actual_spec is None or run["stage"] in ("SPEC", "PLAN"):
            result.update(status="PARTIAL", coverage_complete=False, screening_complete=False, review_complete=False)
            result["needs_human"].append("需求规格或检索计划尚未完成；调用方应执行当前 Agent 任务后继续同一 query_id")
        elif run["status"] == "WAITING_IMPORT" and not run["termination_reason"]:
            result["status"] = "PENDING_IMPORT"
        elif run["stage"] not in ("EXPORT",) and run["status"] != "DONE":
            result["status"] = "PARTIAL"
        validate("evidence", result)
        return result
