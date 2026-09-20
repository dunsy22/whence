-- SPDX-License-Identifier: Apache-2.0
-- The approved pricebook. One row per SKU.
select
    sku,
    cast(list_price     as decimal(18,2)) as list_price,
    currency,
    cast(effective_date as date)          as effective_date,
    last_modified_by,
    -- Blank means nobody signed off on this price, and that is a claim the row
    -- makes rather than a field it is missing -- see SPEC.md section 3.5,
    -- vector B, where the empty string is serialized rather than omitted.
    --
    -- The CSV reader hands back '' here; DuckDB hands back NULL for the same
    -- empty field. Normalizing to '' at the boundary is what keeps the
    -- warehouse's claim identical to the file's, and therefore what keeps the
    -- two systems' fact ids identical. A genuine NULL anywhere else is left
    -- alone, so that minting a Fact from one fails loudly instead of quietly
    -- inventing a value.
    coalesce(approval_ref, '') as approval_ref
from {{ source('raw', var('pricebook_table')) }}
