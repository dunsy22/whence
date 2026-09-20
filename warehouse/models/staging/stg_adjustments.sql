-- SPDX-License-Identifier: Apache-2.0
-- Manual journal entries.
--
-- These rows are in a CSV, but the CSV is only where they were written down --
-- it is not where they came from. Their actual origin is a person and an
-- email. whence/pipeline.py marks them `asserted` for exactly this reason, and
-- the warehouse has no equivalent concept, which is itself worth noticing.
select
    adj_id,
    cast(entry_date as date)          as entry_date,
    cast(amount_usd as decimal(18,2)) as amount_usd,
    reason,
    author,
    -- Kept under its source-system name rather than renamed to something
    -- tidier. Renaming in staging is ordinary dbt practice and a real
    -- deployment would carry the correspondence in the figure mapping; here
    -- the prototype keeps its moving parts to a minimum by staying faithful.
    source
from {{ source('raw', 'adjustments') }}
