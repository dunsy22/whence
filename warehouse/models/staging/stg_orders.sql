-- SPDX-License-Identifier: Apache-2.0
-- ERP order lines, typed. One row per order.
select
    order_id,
    cast(order_date        as date)          as order_date,
    sku,
    cast(qty               as integer)       as qty,
    region,
    currency,
    -- What the ERP actually charged. Recorded, deliberately not used: revenue
    -- is recognized at the approved list price. See int_order_lines.
    cast(unit_price_at_sale as decimal(18,2)) as unit_price_at_sale
from {{ source('raw', var('orders_table')) }}
