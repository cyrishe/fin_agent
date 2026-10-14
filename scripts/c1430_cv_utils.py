"""Fold-fitted clipping for the C1430 research experiment."""
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin

class QuantileClipper(TransformerMixin, BaseEstimator):
    def __init__(self, lower=.01, upper=.99):
        self.lower=lower
        self.upper=upper

    def fit(self, X, y=None):
        self.low_=np.nanquantile(X,self.lower,axis=0)
        self.high_=np.nanquantile(X,self.upper,axis=0)
        return self

    def transform(self, X):
        return np.clip(np.asarray(X,dtype=float),self.low_,self.high_)
