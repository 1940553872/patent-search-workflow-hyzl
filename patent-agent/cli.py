from __future__ import annotations
import argparse
import json
import sys

from shared.contracts import ContractError, read_json
from shared.storage import encode


def main(argv=None):
    parser = argparse.ArgumentParser(description="中国专利检索工作流：智慧芽官网检索 + 本地 PDF 取证")
    parser.add_argument("--data-dir", help="默认使用项目 data 目录")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "search", "lookup"):
        p = sub.add_parser(name)
        p.add_argument("--request", required=True, help="JSON 请求文件")
    sub.add_parser("list")
    sub.add_parser("test", help="执行正式规则与整流程验收")
    for name in ("show", "advance", "packet", "queries", "prompts", "ingest-pdfs", "export", "trace", "evidence"):
        p = sub.add_parser(name)
        p.add_argument("run_id")
    p = sub.add_parser("submit")
    p.add_argument("run_id")
    p.add_argument("stage", choices=["SPEC", "PLAN", "MATCH", "GAP", "REVIEW"])
    p.add_argument("file")
    p = sub.add_parser("import")
    p.add_argument("run_id")
    p.add_argument("file", nargs="?")
    p.add_argument("--query", required=True)
    p.add_argument("--status", default="complete", choices=["complete", "zero", "truncated", "failed", "rate_limited", "timeout", "denied", "unsupported"])
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--note", default="")
    p.add_argument("--actual-query", help="页面实际采用的检索式")
    p.add_argument("--total-hits", type=int, help="页面结果总数，按公开文本计")
    p.add_argument("--searched-at", help="实际检索日期 YYYY-MM-DD")
    p = sub.add_parser("replan", help="保留历史记录，重编普通检索计划")
    p.add_argument("run_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True)
    p = sub.add_parser("confirm")
    p.add_argument("run_id")
    p.add_argument("gate", choices=["spec", "export"])
    p.add_argument("--by", required=True)
    p = sub.add_parser("confirm-pdf")
    p.add_argument("run_id")
    p.add_argument("--key", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--pages", type=int, required=True)
    p.add_argument("--scope", choices=["full", "claims"], default="full")
    p = sub.add_parser("replace-pdf")
    p.add_argument("run_id")
    p.add_argument("--key", required=True)
    p.add_argument("--file", required=True)
    p = sub.add_parser("update-spec")
    p.add_argument("run_id")
    p.add_argument("file")
    p.add_argument("--by", required=True)
    p = sub.add_parser("stop")
    p.add_argument("run_id")
    p.add_argument("--reason", required=True)
    p = sub.add_parser("authorize")
    p.add_argument("run_id")
    p.add_argument("--source", action="append", default=[])
    p.add_argument("--model", action="append", default=[])
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true")
    p = sub.add_parser("evaluate")
    p.add_argument("run_id")
    p.add_argument("--labels", required=True, help="同任务同快照的人工标注 JSON")
    args = parser.parse_args(argv)
    if args.command == "test":
        import unittest
        from shared.storage import APP
        suite = unittest.defaultTestLoader.discover(str(APP / "tests"), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    try:
        from workflow import Workflow
        workflow = Workflow(args.data_dir)
        cmd = args.command
        if cmd in ("create", "search", "lookup"):
            request = read_json(args.request)
            if cmd in ("search", "lookup"):
                from service import SearchService
                result = getattr(SearchService(args.data_dir), cmd)(request)
            else:
                result = workflow.create(request)
        elif cmd == "serve":
            from web import serve
            return serve(workflow, args.port, args.open)
        elif cmd == "list":
            result = [{"run_id": r["run_id"], "title": r["title"], "stage": r["stage"], "status": r["status"]} for r in workflow.store.list()]
        elif cmd == "show":
            result = workflow.view(args.run_id)
        elif cmd == "submit":
            result = workflow.submit(args.run_id, args.stage, read_json(args.file))
        elif cmd == "import":
            execution = {k: v for k, v in {"actual_query": args.actual_query, "total_hits": args.total_hits, "searched_at": args.searched_at}.items() if v is not None}
            result = workflow.import_file(args.run_id, args.file, args.query, args.status, args.page, args.note, execution)
        elif cmd == "replan":
            result = workflow.replan(args.run_id, args.reason, args.by)
        elif cmd == "confirm":
            result = workflow.confirm(args.run_id, args.gate, args.by)
        elif cmd == "confirm-pdf":
            result = workflow.confirm_pdf(args.run_id, args.key, args.by, args.pages, args.scope)
        elif cmd == "replace-pdf":
            result = workflow.replace_pdf(args.run_id, args.key, args.file)
        elif cmd == "update-spec":
            result = workflow.update_spec(args.run_id, read_json(args.file), args.by)
        elif cmd == "stop":
            result = workflow.stop(args.run_id, args.reason)
        elif cmd == "authorize":
            result = workflow.authorize(args.run_id, args.source, args.model)
        elif cmd == "ingest-pdfs":
            result = workflow.ingest_pdfs(args.run_id)
        elif cmd == "trace":
            result = workflow.store.traces(args.run_id)
        elif cmd == "evidence":
            from service import SearchService
            result = SearchService(args.data_dir).evidence(args.run_id)
        elif cmd == "evaluate":
            from shared.evaluation import evaluate
            run = workflow.store.load(args.run_id)
            evidence_path = workflow.store.run_dir(args.run_id) / "evidence.json"
            if not evidence_path.exists():
                raise ValueError("请先导出 evidence.json，再进行正式人工标注评测")
            result = evaluate(read_json(evidence_path), read_json(args.labels))
            workflow.store.artifact(run, "acceptance.json", encode(result))
            workflow.store.save(run, "FORMAL_EVALUATION", {"status": result["status"]})
        else:
            result = getattr(workflow, cmd)(args.run_id)
        if isinstance(result, dict) and "run_id" in result and cmd not in ("show", "packet"):
            result = {k: result[k] for k in ("run_id", "title", "stage", "status", "paths") if k in result}
        print(encode(result))
        return 0
    except (ContractError, ValueError, OSError, KeyError) as exc:
        print(encode({"error": str(exc)}), file=sys.stderr)
        return 1
    except ImportError as exc:
        print(encode({"error": f"缺少运行依赖：{exc}；请安装 requirements.txt，或使用项目启动脚本的 Codex Python"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
