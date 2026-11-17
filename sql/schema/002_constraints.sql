-- Foreign keys, added after the bulk load.
--
-- The public dump is not referentially complete: deleted posts and accounts are
-- removed, but rows that pointed at them are not (see results/INTEGRITY.md for
-- the counts). Two kinds of key follow from that:
--
--   * References with no dangling rows are ordinary, validated foreign keys.
--   * References with dangling rows are declared NOT VALID. Postgres then
--     enforces them for every new or changed row, and for deletes of the
--     referenced row, while tolerating the historical rows that already break
--     them. Nothing is deleted or rewritten to make a constraint pass.

-- Validated: zero dangling rows in the dump.
ALTER TABLE posts      ADD CONSTRAINT posts_owner_fk       FOREIGN KEY (owner_user_id) REFERENCES users (id);
ALTER TABLE posts      ADD CONSTRAINT posts_parent_fk      FOREIGN KEY (parent_id)     REFERENCES posts (id);
ALTER TABLE comments   ADD CONSTRAINT comments_post_fk     FOREIGN KEY (post_id)       REFERENCES posts (id);
ALTER TABLE comments   ADD CONSTRAINT comments_user_fk     FOREIGN KEY (user_id)       REFERENCES users (id);
ALTER TABLE votes      ADD CONSTRAINT votes_user_fk        FOREIGN KEY (user_id)       REFERENCES users (id);
ALTER TABLE post_links ADD CONSTRAINT post_links_post_fk   FOREIGN KEY (post_id)       REFERENCES posts (id);
ALTER TABLE post_tags  ADD CONSTRAINT post_tags_post_fk    FOREIGN KEY (post_id)       REFERENCES posts (id);
ALTER TABLE post_tags  ADD CONSTRAINT post_tags_tag_fk     FOREIGN KEY (tag_id)        REFERENCES tags (id);
ALTER TABLE tags       ADD CONSTRAINT tags_excerpt_fk      FOREIGN KEY (excerpt_post_id) REFERENCES posts (id);
ALTER TABLE tags       ADD CONSTRAINT tags_wiki_fk         FOREIGN KEY (wiki_post_id)  REFERENCES posts (id);

-- Not valid: the dump holds rows that break these.
ALTER TABLE votes      ADD CONSTRAINT votes_post_fk          FOREIGN KEY (post_id)             REFERENCES posts (id) NOT VALID;
ALTER TABLE post_links ADD CONSTRAINT post_links_related_fk  FOREIGN KEY (related_post_id)     REFERENCES posts (id) NOT VALID;
ALTER TABLE posts      ADD CONSTRAINT posts_accepted_fk      FOREIGN KEY (accepted_answer_id)  REFERENCES posts (id) NOT VALID;
ALTER TABLE posts      ADD CONSTRAINT posts_last_editor_fk   FOREIGN KEY (last_editor_user_id) REFERENCES users (id) NOT VALID;
ALTER TABLE badges     ADD CONSTRAINT badges_user_fk         FOREIGN KEY (user_id)             REFERENCES users (id) NOT VALID;

-- Value rules every row in the dump satisfies.
ALTER TABLE posts  ADD CONSTRAINT posts_answer_has_parent CHECK (post_type_id <> 2 OR parent_id IS NOT NULL);
ALTER TABLE posts  ADD CONSTRAINT posts_only_questions_tagged CHECK (tags IS NULL OR post_type_id = 1);
ALTER TABLE badges ADD CONSTRAINT badges_class CHECK (class IN (1, 2, 3));
ALTER TABLE tags   ADD CONSTRAINT tags_name_unique UNIQUE (tag_name);
