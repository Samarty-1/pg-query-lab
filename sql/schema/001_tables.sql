-- Tables for one Stack Exchange site, as published in the public data dump.
-- Column names follow the dump (snake_cased). Primary keys only: every other
-- index in this project is added by an experiment, so its effect can be measured.

CREATE TABLE users (
    id                integer PRIMARY KEY,
    reputation        integer NOT NULL,
    creation_date     timestamp NOT NULL,
    display_name      text,
    last_access_date  timestamp,
    website_url       text,
    location          text,
    about_me          text,
    views             integer NOT NULL DEFAULT 0,
    up_votes          integer NOT NULL DEFAULT 0,
    down_votes        integer NOT NULL DEFAULT 0,
    account_id        integer
);

CREATE TABLE posts (
    id                     integer PRIMARY KEY,
    post_type_id           smallint NOT NULL,   -- 1 question, 2 answer, 4/5 tag wiki, ...
    parent_id              integer,             -- answers: the question
    accepted_answer_id     integer,             -- questions only
    creation_date          timestamp NOT NULL,
    score                  integer NOT NULL,
    view_count             integer,
    body                   text,
    owner_user_id          integer,             -- NULL when the author's account was deleted
    owner_display_name     text,
    last_editor_user_id    integer,
    last_edit_date         timestamp,
    last_activity_date     timestamp,
    title                  text,
    tags                   text,                -- raw, as published: '|ubuntu|bash|'
    answer_count           integer,
    comment_count          integer,
    favorite_count         integer,
    closed_date            timestamp,
    community_owned_date   timestamp,
    content_license        text
);

CREATE TABLE comments (
    id                 integer PRIMARY KEY,
    post_id            integer NOT NULL,
    score              integer NOT NULL,
    text               text,
    creation_date      timestamp NOT NULL,
    user_id            integer,
    user_display_name  text,
    content_license    text
);

CREATE TABLE votes (
    id             integer PRIMARY KEY,
    post_id        integer NOT NULL,
    vote_type_id   smallint NOT NULL,   -- 1 accepted, 2 up, 3 down, 5 favourite, 8 bounty start, ...
    user_id        integer,             -- published only for favourites and bounties
    creation_date  timestamp NOT NULL,  -- day precision in the dump
    bounty_amount  integer
);

CREATE TABLE badges (
    id         integer PRIMARY KEY,
    user_id    integer NOT NULL,
    name       text NOT NULL,
    date       timestamp NOT NULL,
    class      smallint NOT NULL,       -- 1 gold, 2 silver, 3 bronze
    tag_based  boolean NOT NULL
);

CREATE TABLE tags (
    id               integer PRIMARY KEY,
    tag_name         text NOT NULL,
    count            integer NOT NULL,
    excerpt_post_id  integer,
    wiki_post_id     integer
);

CREATE TABLE post_links (
    id               integer PRIMARY KEY,
    creation_date    timestamp NOT NULL,
    post_id          integer NOT NULL,
    related_post_id  integer NOT NULL,
    link_type_id     smallint NOT NULL  -- 1 linked, 3 duplicate
);

-- Derived at load time from posts.tags: one row per (question, tag).
CREATE TABLE post_tags (
    post_id  integer NOT NULL,
    tag_id   integer NOT NULL,
    PRIMARY KEY (post_id, tag_id)
);
