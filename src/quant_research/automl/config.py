from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import date
import math

MODELS = ("linear", "elastic_net", "tree", "forest", "svm", "hist_gradient_boosting")
SAMPLERS = ("all", "momentum", "reversal", "liquid", "low_volatility", "news")


@dataclass(frozen=True)
class ResearchSpec:
    start: str
    end: str
    objective: str = "探索具有样本外稳定性的股票量价规律"
    symbols: tuple[str, ...] = ()
    max_symbols: int | None = None
    horizons: tuple[int, ...] = (1, 3, 7)
    tasks: tuple[str, ...] = ("classification", "regression")
    models: tuple[str, ...] = MODELS
    samplers: tuple[str, ...] = SAMPLERS
    feature_sets: tuple[str, ...] = ("technical", "enriched")
    max_trials: int = 24
    rounds: int = 2
    max_train_rows: int = 20000
    seed: int = 42
    folds: int = 3
    test_fraction: float = .2
    company_holdout_fraction: float = .2
    target_return: float = 0.0
    probability_threshold: float = .6
    regression_threshold: float = .005
    top_k: int = 5
    min_signals: int = 15
    min_win_rate: float = .5
    max_drawdown: float = .3
    commission_rate: float = .0003
    sell_tax_rate: float = .0005
    slippage_rate: float = .001
    initial_cash: float = 1000000
    include_minute: bool = False
    industries: tuple[str, ...] = ()
    min_market_cap: float | None = None
    max_market_cap: float | None = None
    min_amount: float | None = None
    feature_names: tuple[str, ...] = ()
    min_precision: float | None = None
    optimize_threshold: bool = False
    min_signal_dates: int = 5

    def __post_init__(self):
        if date.fromisoformat(self.start) >= date.fromisoformat(self.end):
            raise ValueError("start must precede end")
        for key in ("symbols", "horizons", "tasks", "models", "samplers", "feature_sets", "industries", "feature_names"):
            object.__setattr__(self, key, tuple(getattr(self, key)))
        for key in ("max_trials", "rounds", "max_train_rows", "folds", "top_k", "min_signals", "min_signal_dates"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError("seed must be a 32-bit nonnegative integer")
        if self.max_symbols is not None and (type(self.max_symbols) is not int or self.max_symbols < 1):
            raise ValueError("max_symbols must be a positive integer or null for the full universe")
        if self.max_trials > 200 or self.rounds > 5 or self.max_train_rows > 100000:
            raise ValueError("research budget exceeds supported local limits")
        if not self.horizons or any(type(h) is not int or not 1 <= h <= 60 for h in self.horizons):
            raise ValueError("horizons must contain trading-day counts between 1 and 60")
        for key, allowed in (("models", MODELS), ("samplers", SAMPLERS),
                             ("tasks", ("classification", "regression")),
                             ("feature_sets", ("technical", "enriched"))):
            if not getattr(self, key) or not set(getattr(self, key)) <= set(allowed):
                raise ValueError(f"unsupported {key}")
        if not 0 < self.test_fraction < .5:
            raise ValueError("test_fraction must be in (0, .5)")
        if not 0 <= self.company_holdout_fraction < .5:
            raise ValueError("company_holdout_fraction must be in [0, .5)")
        if self.min_precision is not None and not 0 <= self.min_precision <= 1:
            raise ValueError("min_precision must be in [0, 1]")
        if type(self.optimize_threshold) is not bool:
            raise ValueError("optimize_threshold must be boolean")
        if any(not isinstance(name, str) or not name.strip() for name in self.feature_names):
            raise ValueError("feature_names must contain nonempty feature names")
        for key in ("probability_threshold", "min_win_rate", "max_drawdown"):
            if not 0 <= getattr(self, key) <= 1:
                raise ValueError(f"{key} must be in [0, 1]")
        for key in ("commission_rate", "sell_tax_rate", "slippage_rate"):
            if not 0 <= getattr(self, key) < .1:
                raise ValueError(f"{key} must be in [0, .1)")
        for key in ("target_return", "regression_threshold", "initial_cash"):
            if not math.isfinite(getattr(self, key)):
                raise ValueError(f"{key} must be finite")
        for key in ("min_market_cap", "max_market_cap", "min_amount"):
            value = getattr(self, key)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{key} must be nonnegative and finite")
        if self.min_market_cap is not None and self.max_market_cap is not None and self.min_market_cap > self.max_market_cap:
            raise ValueError("market cap lower bound exceeds upper bound")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")

    @classmethod
    def from_dict(cls, value):
        # Unknown optional metadata is compatible; executable fields have one authority.
        keys = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in value.items() if k in keys})

    def to_dict(self):
        return asdict(self)
