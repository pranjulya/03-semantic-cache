import pytest

from explore_sims import f1_best_threshold, summarize


def test_f1_best_threshold_separable():
    t, f1 = f1_best_threshold([0.9, 0.8, 0.5, 0.4], [True, True, False, False])
    assert t == 0.8 and f1 == 1.0


def test_f1_best_threshold_overlap_prefers_recall_of_positives():
    t, f1 = f1_best_threshold([0.9, 0.7, 0.8, 0.6], [True, True, False, False])
    assert t == 0.7 and f1 == pytest.approx(0.8)  # tp=2 fp=1 fn=0


def test_f1_best_threshold_no_positives_predicted():
    assert f1_best_threshold([0.5, 0.6], [False, False])[1] == 0.0


def test_summarize_quantiles():
    s = summarize([0.0, 0.5, 1.0])
    assert s["n"] == 3 and s["median"] == 0.5 and s["min"] == 0.0 and s["max"] == 1.0
