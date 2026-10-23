"""House agents: real forecasting strategies run by the maintainers, scored
like everyone else and labelled `house-*` on the leaderboard (baselines are
reference forecasts; house agents are competitors).

An agent is a function (league, round_day, as_of) -> submission DataFrame
that may only use information available at `as_of` (the moment it runs,
before the cutoff): today's model forecasts in the round's submission folder
and the truth of rounds already resolved by then. `backtest` replays past
rounds under exactly that rule.
"""

from .blend import blend_agent

AGENTS = {
    "house-blend": blend_agent,
}
