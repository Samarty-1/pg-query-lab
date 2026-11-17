"""The loader puts exactly what is in the XML into the tables."""
import psycopg
import pytest

from lab.load import FILES, normalise_tags


def test_every_generated_row_is_loaded(loaded):
    _, generated, counts = loaded
    for fname, (table, _) in FILES.items():
        assert counts[table] == len(generated[fname][1]), table


def test_both_tag_encodings_end_up_the_same():
    assert normalise_tags("<bash><c++><.net>") == "|bash|c++|.net|"
    assert normalise_tags("|bash|c++|.net|") == "|bash|c++|.net|"
    assert normalise_tags("<bash>") == "|bash|"
    assert normalise_tags("") is None and normalise_tags(None) is None


def test_post_tags_has_one_row_per_tag_mention(loaded, conn):
    _, generated, counts = loaded
    mentions = sum(len(p["Tags"].strip("|").split("|")) for p in generated["Posts.xml"][1] if p.get("Tags"))
    assert counts["post_tags"] == mentions
    # Tag names that are special in LIKE and in regular expressions survive the split.
    for name in ("c++", ".net", "22.04"):
        expected = sum(1 for p in generated["Posts.xml"][1] if p.get("Tags") and f"|{name}|" in p["Tags"])
        got = conn.execute(
            "SELECT count(*) FROM post_tags pt JOIN tags t ON t.id = pt.tag_id WHERE t.tag_name = %s", (name,)
        ).fetchone()[0]
        assert got == expected and expected > 0, name


def test_missing_attributes_load_as_null_not_empty_string(loaded, conn):
    _, generated, _ = loaded
    ownerless = sum(1 for p in generated["Posts.xml"][1] if p.get("OwnerUserId") is None)
    assert ownerless > 0
    assert conn.execute("SELECT count(*) FROM posts WHERE owner_user_id IS NULL").fetchone()[0] == ownerless
    assert conn.execute("SELECT count(*) FROM posts WHERE title = ''").fetchone()[0] == 0


def test_text_with_quotes_and_non_ascii_round_trips(loaded, conn):
    _, generated, _ = loaded
    want = {u["Id"]: (u["DisplayName"], u.get("Location")) for u in generated["Users.xml"][1]}
    got = {i: (n, loc) for i, n, loc in conn.execute("SELECT id, display_name, location FROM users")}
    assert got == want
    assert any("'" in n for n, _ in want.values()) and any(loc == "São Paulo" for _, loc in want.values())


def test_timestamps_keep_their_milliseconds(loaded, conn):
    _, generated, _ = loaded
    post = generated["Posts.xml"][1][0]
    got = conn.execute("SELECT to_char(creation_date, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS') FROM posts WHERE id = %s",
                       (post["Id"],)).fetchone()[0]
    assert got == post["CreationDate"]


def test_loading_twice_replaces_rather_than_appends(loaded):
    from lab import load
    xml_dir, _, counts = loaded
    assert load.load(xml_dir, verbose=False) == counts


def test_a_missing_file_stops_the_load_before_anything_is_dropped(loaded, tmp_path, conn):
    from lab import load
    before = conn.execute("SELECT count(*) FROM posts").fetchone()[0]
    with pytest.raises(SystemExit):
        load.load(tmp_path, verbose=False)
    assert conn.execute("SELECT count(*) FROM posts").fetchone()[0] == before


def test_new_rows_must_respect_a_not_valid_foreign_key(conn):
    """Historical votes may point at deleted posts; a new vote may not."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("INSERT INTO votes (id, post_id, vote_type_id, creation_date) VALUES (-1, 999999999, 2, now())")


def test_an_answer_without_a_question_is_rejected(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO posts (id, post_type_id, creation_date, score) VALUES (-5, 2, now(), 0)")
