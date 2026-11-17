"""Run the experiments and record what actually happened.

    python -m lab.bench                 # every experiment, full dataset parameters
    python -m lab.bench 03 07           # only experiments whose file starts with 03 or 07
    python -m lab.bench --dataset fixture --runs 1 --no-write

An experiment (experiments/NN_name.toml) is one question asked of the database
and two or more ways of answering it. For every variant this:

  1. runs its `setup` SQL (an index, a statistics object, a VACUUM ...),
  2. runs the query twice to warm the cache, then `--runs` times, timing each
     run on the client with all rows fetched; the median is reported,
  3. captures one EXPLAIN (ANALYZE, BUFFERS) plan,
  4. hashes the rows returned and compares them with the first variant's rows,
  5. checks the plan against the variant's `plan_has` / `plan_lacks` claims,
  6. runs its `teardown` SQL, so no variant inherits another's indexes.

A variant that was expected to return the same rows and does not, or whose
plan does not look the way the experiment file says it does, fails the run.
"""
import argparse
import hashlib
import json
import re
import statistics
import sys
import time
import tomllib

from psycopg import sql as pgsql
from psycopg.errors import QueryCanceled

from .db import ROOT, connect, scalar

EXPERIMENTS = ROOT / "experiments"
RESULTS = ROOT / "results"


def load_experiments(prefixes=None):
    out = []
    for path in sorted(EXPERIMENTS.glob("*.toml")):
        if prefixes and not any(path.name.startswith(p) for p in prefixes):
            continue
        exp = tomllib.loads(path.read_text(encoding="utf-8"))
        exp["id"] = path.stem
        out.append(exp)
    return out


def render(conn, text: str, params: dict) -> str:
    """Replace {{name}} with a safely quoted literal."""
    def sub(m):
        name = m.group(1)
        if name not in params:
            raise KeyError(f"no value for parameter {{{{{name}}}}}")
        return pgsql.Literal(params[name]).as_string(conn)
    return re.sub(r"\{\{(\w+)\}\}", sub, text)


def run_script(conn, text: str, params: dict) -> None:
    """Run setup/teardown SQL one statement at a time.

    Sent as one string, several statements run as a single implicit transaction,
    which VACUUM refuses. None of the scripts has a semicolon inside a literal.
    """
    for stmt in render(conn, text, params).split(";"):
        if stmt.strip():
            conn.execute(stmt)


def result_hash(rows, ordered: bool):
    """Hash of the returned rows. Unordered queries are compared as multisets."""
    lines = [json.dumps(r, default=str) for r in rows]
    if not ordered:
        lines.sort()
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def walk(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from walk(child)


def describe(plan_root) -> dict:
    """Boil a JSON plan down to the facts the write-up quotes."""
    nodes, buffers_hit, buffers_read = [], 0, 0
    facts = {"heap_fetches": 0, "rows_removed_by_filter": 0, "sort": [], "workers": 0, "temp_blocks": 0}
    top = plan_root["Plan"]
    analyzed = "Actual Rows" in top
    buffers_hit = top.get("Shared Hit Blocks", 0)
    buffers_read = top.get("Shared Read Blocks", 0)
    facts["temp_blocks"] = top.get("Temp Read Blocks", 0) + top.get("Temp Written Blocks", 0)
    for n in walk(top):
        label = n["Node Type"]
        join_type = n.get("Join Type", "Inner")
        if join_type != "Inner":
            # Same wording as the text plan: "Hash Anti Join", "Nested Loop Left Join".
            label = label.replace(" Join", "") + f" {join_type} Join"
        if n.get("Index Name"):
            label += f" using {n['Index Name']}"
        elif n.get("Relation Name"):
            label += f" on {n['Relation Name']}"
        nodes.append(label)
        facts["heap_fetches"] += n.get("Heap Fetches", 0) * n.get("Actual Loops", 1)
        facts["rows_removed_by_filter"] += n.get("Rows Removed by Filter", 0) * n.get("Actual Loops", 1)
        if n.get("Sort Method"):
            facts["sort"].append(f"{n['Sort Method']} ({n.get('Sort Space Type', '?')} {n.get('Sort Space Used', 0)} kB)")
        facts["workers"] = max(facts["workers"], n.get("Workers Launched", 0))
    worst = max(
        (n for n in walk(top) if analyzed and n.get("Plan Rows") and n.get("Actual Rows") is not None),
        key=lambda n: abs(_log_ratio(n["Plan Rows"], n["Actual Rows"])),
        default=None,
    )
    return {
        "nodes": nodes,
        "buffers_hit": buffers_hit,
        "buffers_read": buffers_read,
        "planning_ms": plan_root.get("Planning Time"),
        "execution_ms": plan_root.get("Execution Time"),
        "worst_estimate": None if worst is None else {
            "node": worst["Node Type"], "estimated": worst["Plan Rows"], "actual": worst["Actual Rows"],
        },
        **facts,
    }


def _log_ratio(est, act):
    import math
    return math.log10(max(est, 1) / max(act, 1))


def _execute(conn, query, rollback):
    """Run the query and fetch everything; optionally inside a transaction that is rolled back."""
    if not rollback:
        return conn.execute(query).fetchall()
    conn.execute("BEGIN")
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.execute("ROLLBACK")


def run_variant(conn, exp, variant, params, runs, baseline_hash, enforce_timeouts=True):
    name = variant["name"]
    query = render(conn, variant.get("query", exp.get("query", "")), params)
    rollback = exp.get("rollback", False)
    timeout_s = variant.get("timeout_s")
    settings = dict(variant.get("settings", {}))
    if timeout_s:
        settings["statement_timeout"] = f"{timeout_s}s"
    try:
        if variant.get("setup"):
            run_script(conn, variant["setup"], params)
        for k, v in settings.items():
            conn.execute(pgsql.SQL("SET {} = {}").format(pgsql.Identifier(k), pgsql.Literal(v)))

        if timeout_s:
            # A variant that is expected to be hopeless gets one attempt and a time limit.
            t0 = time.perf_counter()
            try:
                rows = _execute(conn, query, rollback)
                timings, timed_out = [(time.perf_counter() - t0) * 1000], False
            except QueryCanceled:
                rows, timings, timed_out = [], [timeout_s * 1000.0], True
            conn.execute("RESET statement_timeout")
            plan_json = conn.execute("EXPLAIN (FORMAT JSON) " + query).fetchone()[0][0]
            plan_text = "\n".join(r[0] for r in conn.execute("EXPLAIN " + query).fetchall())
            if timed_out:
                plan_text = f"-- cancelled after {timeout_s} s; this is the plan without ANALYZE\n" + plan_text
        else:
            rows, timings, timed_out = None, [], False
            for i in range(2 + runs):
                t0 = time.perf_counter()
                rows = _execute(conn, query, rollback)
                if i >= 2:
                    timings.append((time.perf_counter() - t0) * 1000)
            explain = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query
            plan_json = _execute(conn, explain, rollback)[0][0][0]
            plan_text = "\n".join(r[0] for r in _execute(conn, "EXPLAIN (ANALYZE, BUFFERS) " + query, rollback))
        info = describe(plan_json)
        if variant.get("estimate_on") and not timed_out:
            # Planner's row estimate against what the node really produced (per process, per loop).
            for n in walk(plan_json["Plan"]):
                label = n["Node Type"] + (f" on {n['Relation Name']}" if n.get("Relation Name") else "")
                if label == variant["estimate_on"]:
                    info["estimate"] = {"node": label, "estimated": n["Plan Rows"], "actual": n["Actual Rows"]}
                    break

        objects = {}
        for obj in variant.get("measure_size", []):
            objects[obj] = scalar(conn, "SELECT pg_total_relation_size(%s::regclass)", (obj,))
    finally:
        for k in settings:
            conn.execute(pgsql.SQL("RESET {}").format(pgsql.Identifier(k)))
        if variant.get("teardown"):
            failing = sys.exc_info()[0] is not None
            try:
                run_script(conn, variant["teardown"], params)
            except Exception:
                if not failing:      # never let clean-up hide the error that got us here
                    raise

    h = result_hash(rows, exp.get("ordered", True))
    expect = variant.get("expect", "same")
    problems = []
    if baseline_hash is not None and not timed_out:
        if expect == "same" and h != baseline_hash:
            problems.append("returns different rows from the first variant")
        if expect == "differs" and h == baseline_hash:
            problems.append("was expected to return different rows but returned the same")
    if timed_out and not variant.get("expect_timeout"):
        problems.append(f"did not finish within {timeout_s} s")
    if variant.get("expect_timeout") and not timed_out and enforce_timeouts:
        problems.append(f"was expected to run past {timeout_s} s but finished")
    return {
        "name": name,
        "note": variant.get("note", ""),
        "median_ms": statistics.median(timings),
        "min_ms": min(timings),
        "max_ms": max(timings),
        "timed_out": timed_out,
        "rows": len(rows),
        "hash": h,
        "expect": expect,
        "plan": info,
        "plan_text": plan_text,
        "object_bytes": objects,
        "problems": problems,
    }


def check_plan(variant, result):
    """Compare the captured plan with what the experiment file claims about it."""
    text = " | ".join(result["plan"]["nodes"])
    for needle in variant.get("plan_has", []):
        if needle not in text:
            result["problems"].append(f"plan was expected to contain '{needle}' but is: {text}")
    for needle in variant.get("plan_lacks", []):
        if needle in text:
            result["problems"].append(f"plan was expected not to contain '{needle}' but is: {text}")


def run(prefixes=None, dataset="full", runs=7, check_plans=True, verbose=True):
    out = []
    with connect() as conn:
        for exp in load_experiments(prefixes):
            params = dict(exp.get("params", {}).get(dataset, {}))
            exp_runs = min(runs, exp.get("max_runs", runs))
            if verbose:
                print(f"\n== {exp['id']}: {exp['title']}", flush=True)
            if exp.get("setup"):
                run_script(conn, exp["setup"], params)
            results, base_hash = [], None
            try:
                if exp.get("derive"):
                    # One-row query whose column names become extra parameters
                    # (e.g. the last row of the previous page, for keyset pagination).
                    cur = conn.execute(render(conn, exp["derive"], params))
                    row = cur.fetchone()
                    if row is None:
                        raise SystemExit(f"{exp['id']}: derive query returned no row")
                    params.update({d.name: v for d, v in zip(cur.description, row)})
                for variant in exp["variant"]:
                    r = run_variant(conn, exp, variant, params, exp_runs, base_hash,
                                    enforce_timeouts=(dataset == "full"))
                    if base_hash is None:
                        base_hash = r["hash"]
                    if check_plans:
                        check_plan(variant, r)
                    results.append(r)
                    if verbose:
                        flag = "" if not r["problems"] else "   <-- " + "; ".join(r["problems"])
                        print(f"   {r['name']:44s} {'>' if r['timed_out'] else ' '}{r['median_ms']:10.1f} ms  {r['rows']:>7} rows  "
                              f"{r['plan']['buffers_hit'] + r['plan']['buffers_read']:>9,} buffers{flag}", flush=True)
            finally:
                if exp.get("teardown"):
                    run_script(conn, exp["teardown"], params)
            out.append({"id": exp["id"], "title": exp["title"], "question": exp["question"],
                        "params": params, "variants": results})
    return out


def environment(conn) -> dict:
    keys = ["server_version", "shared_buffers", "work_mem", "maintenance_work_mem", "effective_cache_size",
            "random_page_cost", "max_parallel_workers_per_gather", "jit"]
    env = {k: scalar(conn, f"SHOW {k}") for k in keys}
    env["database_size"] = scalar(conn, "SELECT pg_size_pretty(pg_database_size(current_database()))")
    env["rows"] = {t: scalar(conn, f"SELECT count(*) FROM {t}") for t in
                   ["users", "posts", "comments", "votes", "badges", "tags", "post_links", "post_tags"]}
    return env


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m lab.bench")
    p.add_argument("prefixes", nargs="*", help="experiment file prefixes, e.g. 03")
    p.add_argument("--dataset", choices=["full", "fixture"], default="full")
    p.add_argument("--runs", type=int, default=7)
    p.add_argument("--no-plan-checks", action="store_true")
    p.add_argument("--no-write", action="store_true", help="do not update results/")
    args = p.parse_args(argv)

    results = run(args.prefixes or None, args.dataset, args.runs, not args.no_plan_checks)
    failed = [(e["id"], v["name"], v["problems"]) for e in results for v in e["variants"] if v["problems"]]

    if not args.no_write:
        from .report import write_results
        with connect() as conn:
            env = environment(conn)
        write_results(results, env, args.runs, merge=bool(args.prefixes))

    if failed:
        print("\nFAILED:")
        for exp_id, name, problems in failed:
            print(f"  {exp_id} / {name}: {'; '.join(problems)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
