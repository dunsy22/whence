{#- SPDX-License-Identifier: Apache-2.0 -#}

{#-
  Money, and the one decision this project exists to make visible.

  Whence rounds to whole cents at every single step: core.money() runs inside
  the @tracked wrapper, so the rounding lands inside the hash and is part of
  the record. SQL is not normally written that way. A data team carries full
  precision through the joins and rounds once, at the figure it reports.

  Both are defensible. They are not the same number. On a few hundred order
  lines the gap is small; it is not zero, and nobody at most companies can
  tell you which convention their reported revenue actually follows.

  So the policy is a switch rather than an assumption, and the reconciliation
  is what forces somebody to set it deliberately.
-#}

{%- macro money(expr) -%}
  {#- An intermediate figure. Rounded only under the per-step policy. -#}
  {%- if var('rounding_policy') == 'per_step' -%}
    round(cast({{ expr }} as decimal(24,8)), 2)
  {%- elif var('rounding_policy') == 'at_end' -%}
    cast({{ expr }} as decimal(24,8))
  {%- else -%}
    {{ exceptions.raise_compiler_error(
         "rounding_policy must be 'per_step' or 'at_end', got '"
         ~ var('rounding_policy') ~ "'") }}
  {%- endif -%}
{%- endmacro -%}


{%- macro money_reported(expr) -%}
  {#- A figure that appears on the board deck. Always cents, under either
      policy -- you cannot report a third of a cent. -#}
  round(cast({{ expr }} as decimal(24,8)), 2)
{%- endmacro -%}
