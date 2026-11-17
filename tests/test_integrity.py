"""The integrity report counts what the fixture was built to contain."""
from lab import integrity


def test_dangling_references_are_counted_exactly(loaded):
    _, generated, _ = loaded
    posts = {p["Id"] for p in generated["Posts.xml"][1]}
    votes_to_missing = sum(1 for v in generated["Votes.xml"][1] if v["PostId"] not in posts)
    links_to_missing = sum(1 for l in generated["PostLinks.xml"][1] if l["RelatedPostId"] not in posts)
    accepted_missing = sum(1 for p in generated["Posts.xml"][1]
                           if p.get("AcceptedAnswerId") and p["AcceptedAnswerId"] not in posts)
    assert votes_to_missing > 0 and links_to_missing > 0 and accepted_missing > 0

    refs, _, _, problems = integrity.run(write=False, verbose=False)
    by_ref = {r["reference"]: r for r in refs}
    assert by_ref["votes.post_id -> posts.id"]["dangling"] == votes_to_missing
    assert by_ref["post_links.related_post_id -> posts.id"]["dangling"] == links_to_missing
    assert by_ref["posts.accepted_answer_id -> posts.id"]["dangling"] == accepted_missing
    assert problems == []


def test_validated_keys_have_nothing_dangling_and_the_catalog_agrees(loaded):
    refs, _, constraints, _ = integrity.run(write=False, verbose=False)
    for r in refs:
        if r["declared"] == "validated":
            assert r["dangling"] == 0, r["reference"]
    validated = dict(constraints)
    assert validated["comments_post_fk"] is True
    assert validated["votes_post_fk"] is False
    # Every reference checked by the report is backed by a real constraint.
    assert len(constraints) == len(integrity.REFERENCES)


def test_stored_totals_are_recounted(loaded, conn):
    _, counters, _, _ = integrity.run(write=False, verbose=False)
    by_name = {c["counter"]: c for c in counters}
    # The fixture keeps answer and comment counts exact and tag counts exact.
    assert by_name["posts.answer_count"]["wrong"] == 0
    assert by_name["posts.comment_count"]["wrong"] == 0
    assert by_name["tags.count"]["wrong"] == 0

    # Break one stored total and the report must see exactly one more disagreement.
    conn.execute("BEGIN")
    try:
        conn.execute("""UPDATE posts SET answer_count = answer_count + 1
                        WHERE id = (SELECT min(id) FROM posts WHERE post_type_id = 1)""")
        checked, wrong = conn.execute(integrity.COUNTERS["posts.answer_count"][1]).fetchone()
        assert wrong == 1 and checked > 0
    finally:
        conn.execute("ROLLBACK")
