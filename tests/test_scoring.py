import pandas as pd
import pytest

from powderbench import scoring


def truth_frame(rows):
    return pd.DataFrame(rows, columns=["station_id", "horizon_h", "truth_in", "valid"])


def pred_frame(rows, cols=("station_id", "horizon_h", "snowfall_in")):
    return pd.DataFrame(rows, columns=list(cols))


TRUTH = truth_frame(
    [
        ("a", 24, 4.0, True),
        ("a", 48, 10.0, True),
        ("b", 24, 0.0, True),
        ("b", 48, 2.0, False),  # QC-voided: must never be scored
    ]
)


def test_pinball_loss_known_values():
    assert scoring.pinball_loss(0.9, 5.0, 10.0) == pytest.approx(4.5)  # under-forecast
    assert scoring.pinball_loss(0.9, 10.0, 5.0) == pytest.approx(0.5)  # over-forecast
    assert scoring.pinball_loss(0.5, 4.0, 10.0) == pytest.approx(3.0)  # MAE/2 at median


def test_brier_known_values():
    assert scoring.brier(1.0, True) == 0.0
    assert scoring.brier(0.0, True) == 1.0
    assert scoring.brier(0.7, False) == pytest.approx(0.49)


def test_powder_score():
    assert scoring.powder_score(1.0, 2.0) == 50.0
    assert scoring.powder_score(2.0, 2.0) == 0.0
    assert scoring.powder_score(4.0, 2.0) == -100.0
    assert scoring.powder_score(1.0, 0.0) is None


def test_score_round_basic_mae():
    pred = pred_frame([("a", 24, 6.0), ("a", 48, 10.0), ("b", 24, 1.0), ("b", 48, 99.0)])
    m = scoring.score_round(pred, TRUTH)
    # voided (b, 48) excluded: errors are |6-4|, |10-10|, |1-0|
    assert m["n_scorable"] == 3 and m["n_scored"] == 3
    assert m["mae"] == pytest.approx(1.0)
    assert m["mae_by_horizon"] == {24: 1.5, 48: 0.0}


def test_score_round_partial_coverage():
    pred = pred_frame([("a", 24, 4.0)])
    m = scoring.score_round(pred, TRUTH)
    assert m["n_scored"] == 1
    assert m["coverage"] == pytest.approx(1 / 3, abs=1e-4)


def test_powder_score_uses_same_rows_as_team():
    # Team predicts only station a; climatology MAE must be computed on a-rows only.
    pred = pred_frame([("a", 24, 5.0), ("a", 48, 9.0)])  # MAE 1.0
    climo = pred_frame([("a", 24, 2.0), ("a", 48, 6.0), ("b", 24, 0.0)])  # on a-rows MAE 3.0
    m = scoring.score_round(pred, TRUTH, climo_pred=climo)
    assert m["powder_score"] == pytest.approx(100 * (1 - 1.0 / 3.0), abs=0.01)


def test_quantile_and_event_tracks():
    cols = ("station_id", "horizon_h", "snowfall_in", "p10", "p25", "p50", "p75", "p90", "prob_6in")
    pred = pred_frame(
        [("a", 24, 4.0, 0, 1, 4, 6, 8, 0.3), ("a", 48, 10.0, 2, 5, 10, 14, 18, None),
         ("b", 24, 0.0, 0, 0, 0, 1, 2, 0.0)],
        cols=cols,
    )
    m = scoring.score_round(pred, TRUTH)
    assert m["pinball"] is not None and m["pinball"] > 0
    # brier6: (a,24) truth 4 -> no event, prob .3 -> .09 ; (b,24) truth 0, prob 0 -> 0
    assert m["brier6"] == pytest.approx((0.09 + 0.0) / 2)


def test_empty_submission():
    m = scoring.score_round(pred_frame([]), TRUTH)
    assert m["n_scored"] == 0 and m["mae"] is None
