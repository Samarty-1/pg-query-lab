"""Write a small, made-up site in the Stack Exchange dump's XML layout.

    python -m lab.fixture tests/fixture_xml

The tests and CI load this instead of the 1 GB real dump. It is seeded, so it
is identical on every machine, and it deliberately contains the awkward cases
found in the real data: posts whose author's account was deleted (no
OwnerUserId), votes and links that point at posts no longer in the dump,
accepted-answer ids that point at deleted answers, and tag names with
characters that are special in LIKE patterns and regular expressions.
"""
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import quoteattr

N_USERS = 1500
N_QUESTIONS = 4000
N_ANSWERS = 7000
N_COMMENTS = 9000
N_VOTES = 40000
N_BADGES = 3000
N_LINKS = 600
PROLIFIC_USER = 7          # owns far more posts than anyone else
START = datetime(2015, 1, 1)
END = datetime(2024, 3, 31)

TAG_NAMES = [
    "bash", "boot", "grub2", "networking", "wireless", "apt", "drivers", "nvidia", "partitioning",
    "dual-boot", "server", "ssh", "permissions", "command-line", "gnome", "kernel", "sound", "usb",
    "python", "c++", ".net", "22.04", "20.04", "18.04", "docker", "systemd", "mount", "printing",
    "bluetooth", "updates", "scripts", "cron", "vim", "firefox", "snap", "wine", "uefi", "swap",
    "ext4", "virtualbox",
]
WORDS = ("install update kernel error after boot cannot package wifi driver screen login terminal "
         "partition disk mount usb sound black freeze slow upgrade failed missing command permission "
         "denied network connection server desktop grub windows dual laptop battery").split()


def _ts(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}"


def _write(path: Path, root: str, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f'<?xml version="1.0" encoding="utf-8"?>\n<{root}>\n')
        for row in rows:
            attrs = " ".join(f"{k}={quoteattr(str(v))}" for k, v in row.items() if v is not None)
            f.write(f"  <row {attrs} />\n")
        f.write(f"</{root}>\n")


def _text(rng, lo, hi):
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(lo, hi)))


def build(seed: int = 20240331) -> dict:
    rng = random.Random(seed)
    span = (END - START).total_seconds()

    def when(i, n):
        """Timestamps rise with id, as they do in the real tables."""
        return START + timedelta(seconds=span * (i + rng.random()) / n)

    # Users: id -1 is the Community account; ~4% of ids are gaps (deleted accounts).
    users = [{"Id": -1, "Reputation": 1, "CreationDate": _ts(START), "DisplayName": "Community",
              "Views": 0, "UpVotes": 0, "DownVotes": 0}]
    live_users = []
    for i in range(1, N_USERS + 1):
        if i != PROLIFIC_USER and rng.random() < 0.04:
            continue
        live_users.append(i)
        name = f"user{i}" if rng.random() < 0.8 else f"User{i} O'Neil"
        users.append({
            "Id": i, "Reputation": int(rng.paretovariate(1.2)), "CreationDate": _ts(when(i, N_USERS)),
            "DisplayName": name, "LastAccessDate": _ts(END),
            "Location": rng.choice([None, None, "Singapore", "Berlin, Germany", "São Paulo"]),
            "AboutMe": None if rng.random() < 0.7 else "<p>" + _text(rng, 5, 30) + "</p>",
            "Views": rng.randint(0, 500), "UpVotes": rng.randint(0, 300), "DownVotes": rng.randint(0, 30),
            "AccountId": 100000 + i,
        })

    def author():
        r = rng.random()
        if r < 0.03:
            return None                      # account deleted: the dump drops OwnerUserId
        if r < 0.13:
            return PROLIFIC_USER
        return rng.choice(live_users)

    tag_weights = [1 / (k + 1) for k in range(len(TAG_NAMES))]
    tag_counts = dict.fromkeys(TAG_NAMES, 0)
    posts, questions, answers_of = [], [], {}
    n_posts = N_QUESTIONS + N_ANSWERS
    # Ids are handed out in time order; ~2% are skipped (deleted posts).
    next_id, deleted_ids = 1, []
    for i in range(n_posts):
        while rng.random() < 0.02:
            deleted_ids.append(next_id)
            next_id += 1
        pid, next_id = next_id, next_id + 1
        created = when(i, n_posts)
        owner = author()
        is_question = len(questions) < 50 or rng.random() < N_QUESTIONS / n_posts
        base = {
            "Id": pid, "CreationDate": _ts(created), "Score": int(rng.gauss(2, 6)),
            "Body": "<p>" + _text(rng, 20, 200) + "</p>", "OwnerUserId": owner,
            "OwnerDisplayName": None if owner is not None else f"user{rng.randint(90000, 99999)}",
            "LastActivityDate": _ts(created + timedelta(days=rng.randint(0, 400))),
            "CommentCount": 0, "ContentLicense": "CC BY-SA 4.0",
        }
        if is_question:
            chosen = sorted(set(rng.choices(TAG_NAMES, weights=tag_weights, k=rng.randint(1, 5))))
            for t in chosen:
                tag_counts[t] += 1
            posts.append({**base, "PostTypeId": 1, "ViewCount": int(rng.paretovariate(0.9) * 20),
                          "Title": _text(rng, 4, 12).capitalize() + "?", "Tags": "|" + "|".join(chosen) + "|",
                          "AnswerCount": 0,
                          "ClosedDate": _ts(created + timedelta(days=2)) if rng.random() < 0.08 else None})
            questions.append(posts[-1])
            answers_of[pid] = []
        else:
            parent = rng.choice(questions[-400:])
            parent["AnswerCount"] += 1
            answers_of[parent["Id"]].append(pid)
            posts.append({**base, "PostTypeId": 2, "ParentId": parent["Id"]})

    for q in questions:
        mine = answers_of[q["Id"]]
        if mine and rng.random() < 0.45:
            q["AcceptedAnswerId"] = rng.choice(mine)
        elif deleted_ids and rng.random() < 0.01:
            q["AcceptedAnswerId"] = rng.choice(deleted_ids)   # accepted answer was later deleted

    # Tag wiki posts: no owner, no tags.
    tags = []
    for k, name in enumerate(TAG_NAMES, start=1):
        wiki = None
        if k % 3 == 0:
            wiki, next_id = next_id, next_id + 1
            posts.append({"Id": wiki, "PostTypeId": 5, "CreationDate": _ts(END), "Score": 0,
                          "Body": f"Questions about {name}.", "LastActivityDate": _ts(END),
                          "CommentCount": 0, "ContentLicense": "CC BY-SA 4.0"})
        tags.append({"Id": k, "TagName": name, "Count": tag_counts[name], "ExcerptPostId": wiki})

    post_ids = [p["Id"] for p in posts if p["PostTypeId"] in (1, 2)]
    by_id = {p["Id"]: p for p in posts}

    comments = []
    for i in range(1, N_COMMENTS + 1):
        pid = rng.choice(post_ids)
        by_id[pid]["CommentCount"] += 1
        uid = None if rng.random() < 0.02 else rng.choice(live_users)
        comments.append({"Id": i, "PostId": pid, "Score": max(0, int(rng.gauss(0, 2))), "Text": _text(rng, 5, 40),
                         "CreationDate": _ts(when(i - 1, N_COMMENTS)), "UserId": uid,
                         "UserDisplayName": None if uid is not None else "user12345",
                         "ContentLicense": "CC BY-SA 4.0"})

    votes = []
    for i in range(1, N_VOTES + 1):
        r = rng.random()
        vtype = 2 if r < 0.72 else 3 if r < 0.82 else 1 if r < 0.88 else 5 if r < 0.94 else 8 if r < 0.95 else 10
        pid = rng.choice(deleted_ids) if deleted_ids and rng.random() < 0.03 else rng.choice(post_ids)
        day = when(i - 1, N_VOTES).replace(hour=0, minute=0, second=0, microsecond=0)
        votes.append({"Id": i, "PostId": pid, "VoteTypeId": vtype,
                      "UserId": rng.choice(live_users) if vtype in (5, 8) else None,
                      "CreationDate": _ts(day), "BountyAmount": rng.choice([50, 100, 200]) if vtype == 8 else None})

    badge_names = ["Student", "Teacher", "Editor", "Supporter", "Nice Answer", "Popular Question", "bash", "Yearling"]
    badges = []
    for i in range(1, N_BADGES + 1):
        name = rng.choice(badge_names)
        badges.append({"Id": i, "UserId": rng.choice(live_users), "Name": name, "Date": _ts(when(i - 1, N_BADGES)),
                       "Class": rng.choice([1, 2, 3, 3, 3]), "TagBased": "True" if name == "bash" else "False"})

    links = []
    for i in range(1, N_LINKS + 1):
        target = rng.choice(deleted_ids) if deleted_ids and rng.random() < 0.05 else rng.choice(post_ids)
        links.append({"Id": i, "CreationDate": _ts(when(i - 1, N_LINKS)), "PostId": rng.choice(post_ids),
                      "RelatedPostId": target, "LinkTypeId": rng.choice([1, 1, 1, 3])})

    return {"Users.xml": ("users", users), "Posts.xml": ("posts", posts), "Comments.xml": ("comments", comments),
            "Votes.xml": ("votes", votes), "Badges.xml": ("badges", badges), "Tags.xml": ("tags", tags),
            "PostLinks.xml": ("postlinks", links)}


def write(out_dir: Path, seed: int = 20240331) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build(seed)
    for fname, (root, rows) in data.items():
        _write(out_dir / fname, root, rows)
    return {fname: len(rows) for fname, (_, rows) in data.items()}


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixture_xml")
    for name, n in write(target).items():
        print(f"{name:14s} {n:>7,} rows")
