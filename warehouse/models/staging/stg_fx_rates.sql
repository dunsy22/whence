-- SPDX-License-Identifier: Apache-2.0
-- Daily FX. The close uses one date out of this; see int_order_lines.
select
    cast(rate_date    as date)          as rate_date,
    currency,
    -- Four places, matching the rate file exactly. Widening the scale here
    -- would be harmless arithmetically and wrong provenantially: the warehouse
    -- would claim a rate of 1.093400 where the source says 1.0934, and the two
    -- systems' source facts would no longer share an identifier.
    cast(usd_per_unit as decimal(18,4)) as usd_per_unit
from {{ source('raw', 'fx_rates') }}
