-- SPDX-License-Identifier: Apache-2.0
-- The approved pricebook. One row per SKU.
select
    sku,
    cast(list_price     as decimal(18,2)) as list_price,
    currency,
    cast(effective_date as date)          as effective_date,
    last_modified_by,
    -- Blank means nobody signed off on this price. Kept as the empty string
    -- rather than coalesced away, so the gap stays visible downstream.
    approval_ref
from {{ source('raw', var('pricebook_table')) }}
