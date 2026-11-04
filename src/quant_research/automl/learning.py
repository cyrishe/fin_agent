from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor, HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge, ElasticNet
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.svm import SVC, SVR
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor


@dataclass
class FittedModel:
    pipeline: object
    calibrator: object
    columns: list
    task: str
    training_counts: dict = field(default_factory=dict)

    def predict(self, frame):
        if self.task == "regression":
            return self.pipeline.predict(frame[self.columns])
        return self.calibrator.predict_proba(raw_score(self.pipeline, frame[self.columns]).reshape(-1, 1))[:, 1]


def raw_score(model, x):
    if hasattr(model, "decision_function"):
        return model.decision_function(x)
    p = np.clip(model.predict_proba(x)[:, 1], 1e-5, 1 - 1e-5)
    return np.log(p / (1 - p))


def training_partition(train, candidate, spec):
    """One authoritative split for fitting and auditable sample counts."""
    task, name = candidate["task"], candidate["model"]
    train = train.sort_values(["date", "symbol"])
    input_rows = len(train)
    calibration = None
    if task == "classification":
        dates = sorted(train.date.unique())
        if len(dates) < 30:
            raise ValueError("classification needs at least 30 training sessions")
        cutoff = dates[int(len(dates) * .75)]
        calibration = train[train.date >= cutoff]
        train = train[(train.date < cutoff) & (train.label_end < cutoff)]
    before_cap = len(train)
    budget = min(spec.max_train_rows, 4000 if name == "svm" else spec.max_train_rows)
    if len(train) > budget:
        train = train.sample(n=budget, random_state=spec.seed).sort_values(["date", "symbol"])
    counts = {"input_rows": input_rows, "fit_rows": len(train),
              "calibration_rows": len(calibration) if calibration is not None else 0,
              "purged_rows": input_rows - before_cap - (len(calibration) if calibration is not None else 0),
              "budget_sampled_out_rows": before_cap - len(train)}
    return train, calibration, counts


def fit_model(train, candidate, columns, spec):
    task, name = candidate["task"], candidate["model"]
    train, calibration, counts = training_partition(train, candidate, spec)
    if len(train) < 40:
        raise ValueError("insufficient training rows after purging/sample selection")
    y = (train.forward_return > spec.target_return).astype(int) if task == "classification" else train.forward_return
    if task == "classification" and (y.nunique() < 2 or (calibration.forward_return > spec.target_return).nunique() < 2):
        raise ValueError("training and calibration each require both outcome classes")
    numeric = [c for c in columns if c != "industry"]
    transforms = [("numeric", make_pipeline(SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True), StandardScaler()), numeric)]
    if "industry" in columns:
        transforms.append(("industry", OneHotEncoder(handle_unknown="ignore", sparse_output=False), ["industry"]))
    depth = candidate.get("depth", 4)
    strength = candidate.get("strength", 1.0)
    if task == "classification":
        estimators = {
            "linear": lambda: LogisticRegression(C=strength, max_iter=1000, random_state=spec.seed),
            "elastic_net": lambda: LogisticRegression(penalty="elasticnet", solver="saga", l1_ratio=.5, C=strength, max_iter=2000, random_state=spec.seed),
            "hist_gradient_boosting": lambda: HistGradientBoostingClassifier(max_iter=80, max_leaf_nodes=15, l2_regularization=1 / strength, early_stopping=False, random_state=spec.seed),
            "tree": lambda: DecisionTreeClassifier(max_depth=depth, min_samples_leaf=20, random_state=spec.seed),
            "forest": lambda: RandomForestClassifier(n_estimators=80, max_depth=depth, min_samples_leaf=10, n_jobs=1, random_state=spec.seed),
            # Probability=True uses internal shuffled calibration. Calibrate chronologically ourselves.
            "svm": lambda: SVC(C=strength, probability=False, cache_size=128),
        }
    else:
        estimators = {
            "linear": lambda: Ridge(alpha=1 / strength),
            "elastic_net": lambda: ElasticNet(alpha=.001 / strength, l1_ratio=.5, max_iter=3000),
            "hist_gradient_boosting": lambda: HistGradientBoostingRegressor(max_iter=80, max_leaf_nodes=15, l2_regularization=1 / strength, early_stopping=False, random_state=spec.seed),
            "tree": lambda: DecisionTreeRegressor(max_depth=depth, min_samples_leaf=20, random_state=spec.seed),
            "forest": lambda: RandomForestRegressor(n_estimators=80, max_depth=depth, min_samples_leaf=10, n_jobs=1, random_state=spec.seed),
            "svm": lambda: SVR(C=strength, epsilon=.005, cache_size=128),
        }
    pipeline = make_pipeline(ColumnTransformer(transforms), estimators[name]())
    pipeline.fit(train[columns], y)
    calibrator = None
    if calibration is not None:
        calibrator = LogisticRegression(C=1.0, max_iter=500, random_state=spec.seed)
        calibrator.fit(raw_score(pipeline, calibration[columns]).reshape(-1, 1),
                       (calibration.forward_return > spec.target_return).astype(int))
    return FittedModel(pipeline, calibrator, columns, task, counts)


def temporal_folds(frame, n_folds):
    dates = np.array(sorted(frame.date.unique()))
    boundaries = np.linspace(len(dates) // 2, len(dates), n_folds + 1, dtype=int)
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        if right <= left:
            continue
        start = dates[left]
        validation = frame[frame.date.isin(dates[left:right])]
        train = frame[(frame.date < start) & (frame.label_end < start)]
        # Validation labels must be observed before the following validation block.
        if right < len(dates):
            validation = validation[validation.label_end < dates[right]]
        yield train, validation
