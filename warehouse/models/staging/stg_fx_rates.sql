-- SPDX-License-Identifier: Apache-2.0
-- Daily FX. The close uses one date out of this; see int_order_lines.
select
    cast(rate_date    as date)          as rate_date,
    currency,
    cast(usd_per_unit as decimal(18,6)) as usd_per_unit
from {{ source('raw', 'fx_rates') }}
