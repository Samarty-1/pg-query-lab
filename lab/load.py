"""Load a Stack Exchange data dump (a folder of XML files) into Postgres.

    python -m lab.load extract data/raw/askubuntu.com.7z data/xml
    python -m lab.load load data/xml

Each XML file is one flat list of <row .../> elements whose attributes are the
columns. Rows are streamed straight into COPY, so memory stays flat however
large the file is. The schema is dropped and rebuilt on every load: a load is
a full replacement, never an append.
"""
import argparse
import sys
import time
from pathlib import Path

from lxml import etree

from .db import ROOT, connect, run_sql_file, scalar

# file -> (table, [(xml attribute, column)])
FILES = {
    "Users.xml": ("users", [
        ("Id", "id"), ("Reputation", "reputation"), ("CreationDate", "creation_date"),
        ("DisplayName", "display_name"), ("LastAccessDate", "last_access_date"),
        ("WebsiteUrl", "website_url"), ("Location", "location"), ("AboutMe", "about_me"),
        ("Views", "views"), ("UpVotes", "up_votes"), ("DownVotes", "down_votes"),
        ("AccountId", "account_id"),
    ]),
    "Posts.xml": ("posts", [
        ("Id", "id"), ("PostTypeId", "post_type_id"), ("ParentId", "parent_id"),
        ("AcceptedAnswerId", "accepted_answer_id"), ("CreationDate", "creation_date"),
        ("Score", "score"), ("ViewCount", "view_count"), ("Body", "body"),
        ("OwnerUserId", "owner_user_id"), ("OwnerDisplayName", "owner_display_name"),
        ("LastEditorUserId", "last_editor_user_id"), ("LastEditDate", "last_edit_date"),
        ("LastActivityDate", "last_activity_date"), ("Title", "title"), ("Tags", "tags"),
        ("AnswerCount", "answer_count"), ("CommentCount", "comment_count"),
        ("FavoriteCount", "favorite_count"), ("ClosedDate", "closed_date"),
        ("CommunityOwnedDate", "community_owned_date"), ("ContentLicense", "content_license"),
    ]),
    "Comments.xml": ("comments", [
        ("Id", "id"), ("PostId", "post_id"), ("Score", "score"), ("Text", "text"),
        ("CreationDate", "creation_date"), ("UserId", "user_id"),
        ("UserDisplayName", "user_display_name"), ("ContentLicense", "content_license"),
    ]),
    "Votes.xml": ("votes", [
        ("Id", "id"), ("PostId", "post_id"), ("VoteTypeId", "vote_type_id"),
        ("UserId", "user_id"), ("CreationDate", "creation_date"), ("BountyAmount", "bounty_amount"),
    ]),
    "Badges.xml": ("badges", [
        ("Id", "id"), ("UserId", "user_id"), ("Name", "name"), ("Date", "date"),
        ("Class", "class"), ("TagBased", "tag_based"),
    ]),
    "Tags.xml": ("tags", [
        ("Id", "id"), ("TagName", "tag_name"), ("Count", "count"),
        ("ExcerptPostId", "excerpt_post_id"), ("WikiPostId", "wiki_post_id"),
    ]),
    "PostLinks.xml": ("post_links", [
        ("Id", "id"), ("CreationDate", "creation_date"), ("PostId", "post_id"),
        ("RelatedPostId", "related_post_id"), ("LinkTypeId", "link_type_id"),
    ]),
}

TABLES = [t for t, _ in FILES.values()] + ["post_tags"]


def normalise_tags(raw):
    """The dump has used two tag encodings: '<a><b>' (older) and '|a|b|' (2023 on).

    Store the newer form whichever arrives. Tag names cannot contain '|', '<' or '>'.
    """
    if raw is None or raw == "":
        return None
    if raw.startswith("<"):
        return "|" + raw[1:-1].replace("><", "|") + "|"
    return raw


def rows(path: Path, attrs):
    """Yield one tuple per <row>, attribute order as given, missing attribute -> None."""
    tags_at = attrs.index("Tags") if "Tags" in attrs else None
    for _, elem in etree.iterparse(str(path), events=("end",), tag="row", huge_tree=True):
        get = elem.attrib.get
        values = [get(a) for a in attrs]
        if tags_at is not None:
            values[tags_at] = normalise_tags(values[tags_at])
        yield values
        elem.clear()
        # Free already-parsed siblings, or the root keeps every row alive.
        while elem.getprevious() is not None:
            del elem.getparent()[0]


def load_file(conn, path: Path, table: str, mapping) -> int:
    attrs = [a for a, _ in mapping]
    cols = ", ".join(c for _, c in mapping)
    n = 0
    with conn.cursor() as cur, cur.copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
        for values in rows(path, attrs):
            copy.write_row(values)
            n += 1
    return n


BUILD_POST_TAGS = """
INSERT INTO post_tags (post_id, tag_id)
SELECT DISTINCT p.id, t.id
FROM posts p
CROSS JOIN LATERAL unnest(string_to_array(btrim(p.tags, '|'), '|')) AS x(tag_name)
JOIN tags t ON t.tag_name = x.tag_name
WHERE p.tags IS NOT NULL
"""


def load(xml_dir: Path, verbose: bool = True) -> dict:
    """Drop and rebuild every table from the XML in xml_dir. Returns row counts."""
    missing = [f for f in FILES if not (xml_dir / f).exists()]
    if missing:
        raise SystemExit(f"{xml_dir} is missing {', '.join(missing)}")

    counts = {}
    with connect() as conn:
        for t in TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        run_sql_file(conn, ROOT / "sql" / "schema" / "001_tables.sql")

        for fname, (table, mapping) in FILES.items():
            t0 = time.time()
            with conn.transaction():
                counts[table] = load_file(conn, xml_dir / fname, table, mapping)
            if verbose:
                print(f"{table:11s} {counts[table]:>11,} rows  {time.time() - t0:6.1f}s", flush=True)

        t0 = time.time()
        conn.execute(BUILD_POST_TAGS)
        counts["post_tags"] = scalar(conn, "SELECT count(*) FROM post_tags")
        if verbose:
            print(f"{'post_tags':11s} {counts['post_tags']:>11,} rows  {time.time() - t0:6.1f}s", flush=True)

        t0 = time.time()
        run_sql_file(conn, ROOT / "sql" / "schema" / "002_constraints.sql")
        if verbose:
            print(f"constraints added and checked  {time.time() - t0:6.1f}s", flush=True)

        # VACUUM sets the visibility map (index-only scans depend on it); ANALYZE gathers statistics.
        conn.execute("VACUUM (ANALYZE)")
    return counts


def extract(archive: Path, out_dir: Path) -> None:
    """Unpack only the files this project loads (PostHistory is skipped: it is most of the bytes)."""
    import py7zr

    out_dir.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(archive) as z:
        names = z.getnames()
        wanted = [n for n in names if n in FILES]
        print(f"archive holds {names}; extracting {wanted}", flush=True)
        z.extract(path=out_dir, targets=wanted)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m lab.load", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract", help="unpack the needed XML files from a .7z dump")
    e.add_argument("archive", type=Path)
    e.add_argument("out_dir", type=Path)
    l = sub.add_parser("load", help="rebuild the database from a folder of XML files")
    l.add_argument("xml_dir", type=Path)
    args = p.parse_args(argv)

    if args.cmd == "extract":
        extract(args.archive, args.out_dir)
    else:
        t0 = time.time()
        counts = load(args.xml_dir)
        print(f"loaded {sum(counts.values()):,} rows in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
