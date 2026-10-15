"""Verify R23C with independent event sums and source-price checks."""
from scripts.replay_limit_rebound_fees_r23c import OUT
from scripts.verify_limit_rebound_cash_r23 import main

if __name__=='__main__':main(OUT,fee_mode=True)
