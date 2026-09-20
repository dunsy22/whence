-- SPDX-License-Identifier: Apache-2.0
--
-- Regional subtotals. Mirrors the `sum_by_region` transform.
select
    region,
    count(*)                                     as order_count,
    {{ money_reported('sum(line_revenue_usd)') }} as region_revenue_usd
from {{ ref('int_order_lines') }}
group by region
