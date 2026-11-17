"""Every experiment runs on the fixture and its variants agree with each other.

Timings and plan shapes are only meaningful on the full dataset, so they are not
checked here. What is checked is the part that must hold at any size: variants
that claim to return the same rows do, variants that claim to differ do, and no
experiment leaves an index or table behind.
"""
import pytest

from lab import bench

EXPERIMENTS = bench.load_experiments()


def test_there_are_experiments():
    assert len(EXPERIMENTS) >= 12


@pytest.mark.parametrize("exp", EXPERIMENTS, ids=lambda e: e["id"])
def test_experiment_file_is_complete(exp):
    assert exp["title"] and exp["question"].strip()
    assert set(exp.get("params", {})) == {"full", "fixture"}, "needs [params.full] and [params.fixture]"
    names = [v["name"] for v in exp["variant"]]
    assert len(names) == len(set(names)) and len(names) >= 2
    for v in exp["variant"]:
        assert v.get("query") or exp.get("query"), f"{v['name']} has no query"
        if "CREATE" in v.get("setup", "") and "DROP TABLE IF EXISTS" not in v.get("setup", ""):
            assert v.get("teardown"), f"{v['name']} creates something and never drops it"
        if v.get("expect_timeout"):
            assert v.get("timeout_s")


@pytest.mark.parametrize("exp", EXPERIMENTS, ids=lambda e: e["id"])
def test_variants_agree_on_the_fixture(loaded, conn, exp):
    [result] = bench.run([exp["id"]], dataset="fixture", runs=1, check_plans=False, verbose=False)
    problems = {v["name"]: v["problems"] for v in result["variants"] if v["problems"]}
    assert problems == {}
    assert result["variants"][0]["rows"] > 0, "the reference variant returned nothing, so agreement proves nothing"

    leftovers = conn.execute("""
        SELECT relname FROM pg_class WHERE relname LIKE 'exp\\_%' AND relnamespace = 'public'::regnamespace
        UNION ALL SELECT stxname FROM pg_statistic_ext WHERE stxname LIKE 'exp\\_%'""").fetchall()
    assert leftovers == []


def test_the_null_trap_is_real_on_the_fixture(conn):
    """The headline of experiment 06, stated directly."""
    never_posted = conn.execute(
        "SELECT count(*) FROM users u WHERE NOT EXISTS (SELECT 1 FROM posts p WHERE p.owner_user_id = u.id)"
    ).fetchone()[0]
    not_in = conn.execute("SELECT count(*) FROM users WHERE id NOT IN (SELECT owner_user_id FROM posts)").fetchone()[0]
    assert never_posted > 0 and not_in == 0


def test_render_quotes_values_safely(conn):
    sql = bench.render(conn, "SELECT {{a}}, {{b}}", {"a": "O'Neil; DROP TABLE users", "b": 7})
    assert conn.execute(sql).fetchone() == ("O'Neil; DROP TABLE users", 7)
    with pytest.raises(KeyError):
        bench.render(conn, "SELECT {{missing}}", {})


def test_result_hash_respects_order_only_when_asked():
    a, b = [(1, "x"), (2, "y")], [(2, "y"), (1, "x")]
    assert bench.result_hash(a, ordered=False) == bench.result_hash(b, ordered=False)
    assert bench.result_hash(a, ordered=True) != bench.result_hash(b, ordered=True)
    assert bench.result_hash(a, ordered=True) != bench.result_hash(a + [(2, "y")], ordered=False)


def test_plan_summary_reads_a_real_plan(conn):
    plan = conn.execute("""EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
        SELECT count(*) FROM users u WHERE NOT EXISTS (SELECT 1 FROM posts p WHERE p.owner_user_id = u.id)
    """).fetchone()[0][0]
    info = bench.describe(plan)
    assert any("Anti Join" in n for n in info["nodes"])
    assert any(n.endswith("on posts") for n in info["nodes"])
    assert info["buffers_hit"] + info["buffers_read"] > 0
