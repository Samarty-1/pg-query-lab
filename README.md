# pg-query-lab

Twelve slow queries on a real 12-million-row PostgreSQL database, each with the fix
measured rather than asserted: time, pages read, extra storage, and a check that the
fast version returns exactly the same rows as the slow one.

The data is the public Ask Ubuntu data dump (Stack Exchange, April 2024): 1.5M users,
945k posts, 4.9M votes, 1.5M comments, 2.0M badges. PostgreSQL 17, default planner
settings.

## What came out

| # | Question | Before | After | What changed | Cost |
|---|---|---:|---:|---|---|
| 01 | Latest 20 posts by one user | 155 ms | 0.16 ms | Index in the order the page reads: `(owner_user_id, creation_date DESC, id DESC)` | 37 MB |
| 02 | Newest 50 questions with a tag | 174 ms | 0.41 ms | Junction table that also carries the creation date, indexed `(tag_id, creation_date DESC)` | 103 MB |
| 03 | Page 4,001 of the question list | 65 ms | 0.22 ms | Keyset pagination instead of `OFFSET 200000` | none |
| 04 | Up-votes per day, last month | 151 ms | 2.0 ms | Index holding both columns the query needs (index-only scan) | 35 MB |
| 05 | Find a user by name, any case | 125 ms | 0.21 ms | Index on `lower(display_name)`, the expression actually filtered on | 40 MB |
| 06 | Users who never posted | did not finish | 489 ms | `NOT EXISTS` instead of `NOT IN` | none |
| 07 | Best answer to each of last month's questions | 136 ms | 2.6 ms | `LATERAL` + index `(parent_id, score DESC, id)` | 28 MB |
| 08 | Search question titles | 237 ms | 6.6 ms | Trigram index, same `ILIKE` query | 61 MB |
| 09 | Delete five posts | 4,607 ms | 3.8 ms | Index the columns that *reference* posts | 94 MB |
| 10 | Join filtered on two dependent columns | 273 ms | 251 ms | Extended statistics | none |
| 11 | Latest vote of the most common type | 190 ms | 196 ms | Index offered; planner declines it | 33 MB, unused |
| 12 | Insert 200,000 votes | 340 ms | 2,709 ms | Going from 0 to 6 indexes on the table | - |

Full tables with every variant, pages read and plans: [results/RESULTS.md](results/RESULTS.md).
One captured `EXPLAIN (ANALYZE, BUFFERS)` per variant: [results/plans/](results/plans/).

## The results that were not "add an index"

**A query that returns the wrong answer (06).** 16,835 posts have no owner because the
account was deleted. `WHERE id NOT IN (SELECT owner_user_id FROM posts)` therefore
compares against a list containing NULL, which makes the test unknown for every row:
it reports **0** users who never posted. The true figure is 1,162,936. At the default
`work_mem` the same query does not finish at all (cancelled after 20 s; the planner
costs it at 36 billion units) because the id list does not fit in memory and is
re-scanned for every user.

**An index that made the query 3x slower (04).** A BRIN index on `votes.creation_date`
is 48 kB, against 34 MB for a B-tree, and should be ideal for a date range. On the
table as loaded it takes 513 ms against 151 ms with no index at all. The dump's
`Votes.xml` is not written in id order, so the last month's votes are spread across
24% of the table's pages and the index cannot rule out a single range. On a copy of
the table stored in date order, the same 48 kB index answers in 3.6 ms.

**A primary key that does not help (02).** `post_tags` has primary key
`(post_id, tag_id)`. Looking up a tag reads the whole table, because the key starts
with the other column. Adding `(tag_id, post_id)` helps less than expected (22 ms):
the query still has to fetch all 11,211 bash questions to find the newest 50. The real
fix was changing what the junction row stores.

**Indexes that were built and never used.** A B-tree on `display_name` for a
`lower(display_name)` filter (05), a B-tree on `title` for `ILIKE '%...%'` (08), and
an index on a column where one value is 73% of the rows (11). In the last case the
planner is right: forcing the index is 1.2x slower than the scan it prefers.

**A fix that fixed the estimate and not the time (10).** The planner expected 399
rows from the badge filter and got 13,470 per worker. Extended statistics brought the
estimate to 17,428. The plan did not change shape and the time moved from 273 to
251 ms, inside run-to-run noise. Better statistics are not a speed-up by themselves.

**The slowest thing here is not a SELECT (09).** Deleting five posts takes 4.6 seconds,
all of it in foreign-key checks: Postgres indexes the referenced side of a key
automatically and the referencing side never.

**What all the fixes cost (12).** Inserting 200,000 rows takes 0.34 s into a table with
no indexes, 0.71 s with a primary key, and 2.7 s with six indexes: 8x.

## What the data is like

The dump is not referentially clean, and the schema says so instead of hiding it.
`python -m lab.integrity` counts every reference and every stored total
([results/INTEGRITY.md](results/INTEGRITY.md)):

- **20.3% of votes (1,004,216) point at posts that are not in the dump.** Deleted
  posts are removed; the votes on them are not.
- 2,797 post links, 9 accepted-answer ids, 1 badge and 1 last-editor id also point at
  nothing.
- Stored totals disagree with a recount from the detail rows on 2,208 post scores,
  344 tag counts, 210 comment counts and 87 answer counts.

Foreign keys with no dangling rows are ordinary validated constraints. The five with
dangling rows are declared `NOT VALID`: Postgres enforces them for every new row and
for deletes, and tolerates the historical rows that break them. No row is dropped or
edited to make a constraint pass.

## How an experiment works

Each file in [experiments/](experiments/) is one question and several ways to answer
it. For every variant, `lab/bench.py`:

1. runs its setup (an index, a statistics object, a sorted copy of a table);
2. runs the query twice to warm the cache, then 7 times, timed on the client with all
   rows fetched; the median is reported;
3. captures the plan with `EXPLAIN (ANALYZE, BUFFERS)`;
4. hashes the rows returned and compares them with the first variant;
5. checks the plan against what the experiment file claims (for example, that it uses
   a named index, or that it still scans the table);
6. drops whatever it created, so no variant inherits another's index.

A variant that should return the same rows and does not, or whose plan is not what the
file says, fails the run. Variants expected to be hopeless get one attempt and a 20 s
limit.

## Running it

You need Python 3.11+ and a PostgreSQL 14+ server.

```bash
pip install -r requirements.txt

# a throwaway server (or point PGLAB_DSN at your own)
docker run -d --name pglab -e POSTGRES_HOST_AUTH_METHOD=trust -p 5544:5432 \
    postgres:17 -c shared_buffers=2GB
docker exec pglab createdb -U postgres stacklab

# tests: load a small made-up site and run every experiment on it (about 15 s)
pytest -q

# the real thing: 1.07 GB download, about 100 s to load
curl -LO https://archive.org/download/stackexchange/askubuntu.com.7z
python -m lab.load extract askubuntu.com.7z data/xml
python -m lab.load load data/xml
python -m lab.integrity
python -m lab.bench            # all experiments, rewrites results/
python -m lab.bench 04 09      # only some
```

`PGLAB_DSN` (default `postgresql://postgres@localhost:5544/stacklab`) selects the
database; `PGLAB_TEST_DSN` selects the one the tests may wipe. Any other Stack
Exchange site's dump loads the same way, but the parameter values in the experiment
files (a user id, a tag) are Ask Ubuntu's.

## Layout

```
sql/schema/001_tables.sql        tables and primary keys
sql/schema/002_constraints.sql   foreign keys (validated and NOT VALID), checks
lab/load.py                      XML dump -> COPY, streamed; builds post_tags
lab/integrity.py                 dangling references and stored totals
lab/bench.py                     runs experiments, checks rows and plans
lab/report.py                    writes results/
lab/fixture.py                   seeded made-up site in the dump's format, for tests and CI
experiments/*.toml               the twelve experiments
results/                         generated: RESULTS.md, INTEGRITY.md, results.json, plans/
tests/                           42 tests against the fixture
```

CI starts a Postgres 17 service container, loads the fixture and runs every experiment
on it. On about 70,000 rows timings mean nothing, so CI checks what must hold at any size:
the loader is exact, the integrity counts match what the fixture was built to
contain, and each experiment's variants return the rows they claim to.

## Limits

- One machine (Ryzen 5 5600, 32 GB, Windows 11), one run of the suite, warm
  cache. Repeat runs of the same variant differ by 10-20%; treat a 1.1x difference as
  no difference and read the big ratios as orders of magnitude.
- Everything fits in memory (2.2 GB database, 2 GB `shared_buffers`). On a database
  larger than RAM the unindexed variants would be far slower than shown and the
  indexed ones barely different.
- Planner settings are the defaults (`work_mem` 4 MB, `random_page_cost` 4, two
  parallel workers) except where an experiment changes one and says so.
- Each fix is measured alone, for one query. Experiment 12 is the only measurement of
  what indexes cost on writes; there is no mixed read/write workload.
- Parameters are fixed (the most prolific user, the tag `bash`). Other values would
  give other numbers; the worst-case user was chosen on purpose for 01.

Data: Stack Exchange Data Dump, licensed CC BY-SA 4.0. No dump data is committed to
this repository.
