"""
Guard for what counts as a valid implied price.

`implied price = market_value / par x 100` only means something for par-denominated
CLO tranches. A handful of NPORT lines are not that: ECC reports "CLO Participation
Fee", "CLO Participation Share" and "Common Units" — fee and equity-unit interests
whose "par" is nominal — and occasional lines carry a placeholder par. Those produce
prices like 880c or 60,489c, which silently inflate any average they land in.

They are ~0.2% of holdings but skew headline numbers (ECC's portfolio average is
~3c too high with the Common Units line included), so price math should exclude
them — and say so, rather than dropping rows quietly.
"""

# A par-denominated CLO tranche trades meaningfully within this band. Above it,
# the line isn't priced against par; at/below zero there's nothing to value.
MIN_PRICE = 0.0
MAX_PRICE = 150.0


def is_par_priced(price) -> bool:
    """True if an implied price is plausible for a par-denominated tranche."""
    return price is not None and MIN_PRICE <= price <= MAX_PRICE


def valid_prices(df, price_col: str = "price"):
    """Rows of `df` whose implied price is usable for price math."""
    return df[df[price_col].between(MIN_PRICE, MAX_PRICE)]


def split_par_priced(df, par_col: str = "par_amount", mv_col: str = "market_value"):
    """
    Split a holdings frame into (par-priced, non-par) rows.

    Use the first for price averages; report the second so the exclusion is visible
    instead of silent.
    """
    price = (df[mv_col] / df[par_col] * 100).where(df[par_col] > 0)
    ok = price.between(MIN_PRICE, MAX_PRICE)
    return df[ok], df[~ok]
