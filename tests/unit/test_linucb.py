import numpy as np

from edge.policy.linucb import LinUCB


def test_linucb_construct_select():
    arms = [(1.5, 6), (3.0, 8)]
    pol = LinUCB(arms=arms, d=5, alpha=0.7)
    s = np.zeros(5)
    a = pol.select(s)
    assert a in arms
