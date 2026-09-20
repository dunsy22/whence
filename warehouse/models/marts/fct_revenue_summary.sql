-- SPDX-License-Identifier: Apache-2.0
--
-- The reported figure. Mirrors `sum_regions` then `apply_adjustments`.
--
-- This is the model a Whence figure mapping would point at: one row, one
-- number, the thing that ends up on slide 4.

with regions as (
    select * from {{ ref('fct_region_revenue') }}
),

gross as (
    select
        count(*)                                       as region_count,
        sum(order_count)                               as order_count,
        {{ money_reported('sum(region_revenue_usd)') }} as gross_revenue_usd
    from regions
),

-- Added exactly like any other amount. What makes these different is not the
-- arithmetic but their origin, which the warehouse cannot express and Whence
-- can: `apply_adjustments` is plain addition in both systems.
adjustments as (
    select
        count(*)                               as adjustment_count,
        {{ money_reported('sum(amount_usd)') }} as adjustments_usd
    from {{ ref('stg_adjustments') }}
)

select
    '{{ var("close_date") }}'   as close_date,
    g.region_count,
    g.order_count,
    g.gross_revenue_usd,
    a.adjustment_count,
    a.adjustments_usd,
    {{ money_reported('g.gross_revenue_usd + a.adjustments_usd') }}
                                as net_revenue_usd,
    '{{ var("rounding_policy") }}' as rounding_policy
from gross g
cross join adjustments a
