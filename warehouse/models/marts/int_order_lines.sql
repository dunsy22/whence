-- SPDX-License-Identifier: Apache-2.0
--
-- One row per order line, priced and translated to USD.
--
-- This model mirrors two @tracked transforms from whence/pipeline.py, in
-- order: `multiply` (quantity x approved list price) and `fx_convert` (native
-- amount x closing rate). The order matters -- under the per-step policy the
-- FX conversion operates on an already-rounded native amount, exactly as the
-- Python does, because each tracked call quantizes its own result.

with orders as (
    select * from {{ ref('stg_orders') }}
),

pricebook as (
    select * from {{ ref('stg_pricebook') }}
),

-- One rate per currency: the closing rate, applied to every line regardless
-- of the order's own date. That is a policy choice, and it is the same one
-- pipeline._rate_row_for makes. A transaction-date policy would join on
-- order_date instead and produce visibly different numbers.
closing_rates as (
    select currency, usd_per_unit
    from {{ ref('stg_fx_rates') }}
    where rate_date = cast('{{ var("close_date") }}' as date)
),

priced as (
    select
        o.order_id,
        o.order_date,
        o.sku,
        o.region,
        o.currency,
        o.qty,
        p.list_price,
        o.unit_price_at_sale,
        p.effective_date   as price_effective_date,
        p.last_modified_by as price_modified_by,
        p.approval_ref     as price_approval_ref,
        r.usd_per_unit,
        -- Revenue is recognized at the approved list price, not at the price
        -- the ERP captured. Where they disagree the captured price is carried
        -- above and surfaced as a finding, never silently used.
        {{ money('o.qty * p.list_price') }} as line_revenue_native
    from orders o
    join pricebook     p on p.sku      = o.sku
    join closing_rates r on r.currency = o.currency
)

select
    *,
    {{ money('line_revenue_native * usd_per_unit') }} as line_revenue_usd,
    unit_price_at_sale <> list_price                  as priced_off_list
from priced
