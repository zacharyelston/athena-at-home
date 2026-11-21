-- SPDX-License-Identifier: GPL-3.0-or-later
-- Visibility (D11): a review queue for widened settings. Widening is allowed but every widening waits here until
-- someone takes a second look; the assertion it points at holds who, what and why.

CREATE TABLE visibility_review (
    id            bigserial PRIMARY KEY,
    assertion     bigint      NOT NULL UNIQUE REFERENCES assertion (id),
    subject_type  text        NOT NULL,
    subject_id    text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    confirmed_at  timestamptz,
    confirmed_by  text
);
CREATE INDEX visibility_review_open ON visibility_review (subject_type, subject_id) WHERE confirmed_at IS NULL;
